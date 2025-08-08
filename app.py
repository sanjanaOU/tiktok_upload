import os, time, secrets
from urllib.parse import urlencode
from flask import Flask, redirect, request, session, jsonify, render_template_string
import requests

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DIRECT_POST_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# IMPORTANT: space-separated, not commas
SCOPES = "user.info.basic video.upload video.publish"


# ------------ helpers ------------
def _new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def _save_tokens(tok):
    # tok is the token JSON from TikTok
    session["tk"] = {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"),
        "expires_at": int(time.time()) + int(tok.get("expires_in", 3600)) - 60,
    }
    session["open_id"] = tok.get("open_id")

def _get_tokens():
    d = session.get("tk") or {}
    return d.get("access_token"), d.get("refresh_token"), d.get("expires_at", 0)

def _ensure_access_token():
    access_token, refresh_token, expires_at = _get_tokens()
    if not access_token:
        return None
    if time.time() < expires_at:
        return access_token
    # refresh
    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    r.raise_for_status()
    _save_tokens(r.json())
    return session["tk"]["access_token"]


# ------------ routes ------------
@app.route("/")
def index():
    return (
        "<h3>TikTok OAuth + Upload</h3>"
        '<p><a href="/login">Login with TikTok</a></p>'
        '<p><a href="/debug-auth">/debug-auth</a></p>'
        '<p><a href="/form">Upload form</a></p>'
    )

@app.route("/debug-auth")
def debug_auth():
    state = session.get("oauth_state") or "(none yet)"
    q = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,               # space-separated
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return jsonify({
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": state,
        "authorize_url": AUTH_URL + "?" + urlencode(q),
    })

@app.route("/login")
def login():
    state = _new_state()
    q = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,   # must match console exactly
        "state": state,
        "force_verify": "1",            # show consent every time (handy while testing)
    }
    return redirect(AUTH_URL + "?" + urlencode(q), code=302)

@app.route("/callback")
def callback():
    err = request.args.get("error")
    if err:
        return f"❌ TikTok error: {err}", 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "❌ Missing ?code from TikTok.", 400
    if state != session.get("oauth_state"):
        return "❌ State mismatch. Start login again.", 400

    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    if r.status_code != 200:
        return f"❌ Token exchange failed: {r.status_code} {r.text}", r.status_code

    tok = r.json()
    if "access_token" not in tok:
        return f"❌ Token response missing access_token: {tok}", 400

    _save_tokens(tok)
    return (
        f"✅ Got token for open_id={session.get('open_id')}<br>"
        f'<a href="/form">Go to upload form</a>'
    )

# simple HTML upload form
@app.route("/form")
def form():
    html = """
    <h3>Post a TikTok video</h3>
    <form action="/upload" method="post" enctype="multipart/form-data">
      <div>Video (.mp4): <input type="file" name="file" accept="video/mp4" required></div>
      <div>Caption: <input type="text" name="title" placeholder="Your caption"></div>
      <div>Privacy:
        <select name="privacy">
          <option value="SELF_ONLY" selected>SELF_ONLY</option>
          <option value="PUBLIC_TO_EVERYONE">PUBLIC_TO_EVERYONE</option>
          <option value="MUTUAL_FOLLOW_FRIENDS">MUTUAL_FOLLOW_FRIENDS</option>
          <option value="FOLLOWER_OF_CREATOR">FOLLOWER_OF_CREATOR</option>
        </select>
      </div>
      <div>Cover timestamp (ms): <input type="number" name="cover_ms" value="0"></div>
      <button type="submit">Upload & Post</button>
    </form>
    """
    return render_template_string(html)

# MAIN: file upload -> post
@app.route("/upload", methods=["POST"])
def upload():
    access_token = _ensure_access_token()
    if not access_token:
        return "Not authorized. Hit /login first.", 401

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file is required (.mp4)"}), 400

    title = request.form.get("title", "")
    privacy = request.form.get("privacy", "SELF_ONLY")  # sandbox often forces private
    cover_ms = int(request.form.get("cover_ms", "0"))

    # 1) Initialize Direct Post (FILE_UPLOAD)
    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": cover_ms
        },
        "source_info": { "source": "FILE_UPLOAD" }
    }
    init_res = requests.post(
        DIRECT_POST_INIT,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=init_body,
        timeout=60,
    )
    if init_res.status_code >= 400:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_res.text}), init_res.status_code

    data = init_res.json()["data"]
    upload_url = data["upload_url"]
    publish_id = data["publish_id"]

    # 2) PUT raw bytes to upload_url
    put_res = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=f.read(),
        timeout=300,
    )
    if put_res.status_code >= 400:
        return jsonify({"step": "upload", "status": put_res.status_code, "response": put_res.text}), put_res.status_code

    # 3) Poll status once (you can also call /status later)
    st = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=30,
    )

    return jsonify({"ok": True, "publish_id": publish_id, "status": st.json()})

@app.route("/status")
def status():
    access_token = _ensure_access_token()
    if not access_token:
        return jsonify({"error": "Not authorized"}), 401
    pid = request.args.get("publish_id")
    if not pid:
        return jsonify({"error": "publish_id required"}), 400
    r = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": pid},
        timeout=30,
    )
    return jsonify(r.json()), r.status_code


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5051)), debug=True)
