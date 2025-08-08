# app.py
import os, time, secrets
from urllib.parse import urlencode, quote
from flask import Flask, request, session, jsonify, render_template_string, redirect, url_for
import requests

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

# ---- ENV for OAuth (optional) ----
CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI  = os.getenv("REDIRECT_URI", "").strip()

AUTH_URL   = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL  = "https://open.tiktokapis.com/v2/oauth/token/"

# Direct Post endpoints (correct ones)
DIRECT_POST_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH     = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# scopes must be SPACE-separated; we’ll encode as %20, not +
SCOPES = "user.info.basic video.upload video.publish"


# ========= token helpers =========
def _save_tokens(access_token: str, refresh_token: str | None, expires_in: int, open_id: str | None):
    session["tk"] = {
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "expires_at": int(time.time()) + int(expires_in or 3600) - 60,
    }
    if open_id:
        session["open_id"] = open_id

def _get_tokens():
    d = session.get("tk") or {}
    return d.get("access_token"), d.get("refresh_token"), d.get("expires_at", 0)

def _ensure_access_token():
    access, refresh, exp = _get_tokens()
    if not access:
        return None
    if time.time() < exp or not refresh:
        return access
    # optional refresh (only if we have refresh_token and env creds)
    if not CLIENT_KEY or not CLIENT_SECRET:
        return access
    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        },
        timeout=30,
    )
    if r.ok and "access_token" in r.json():
        j = r.json()
        _save_tokens(j["access_token"], j.get("refresh_token"), j.get("expires_in", 3600), j.get("open_id"))
        return j["access_token"]
    return access


# ========= OAuth helpers =========
def _new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def _build_auth_url(state: str) -> str:
    # Encode everything normally, but encode scope as %20 (not +)
    base = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "force_verify": "1",
    }
    return f"{AUTH_URL}?{urlencode(base)}&scope={quote(SCOPES, safe='')}"


# ========= pages =========
HOME_HTML = """
<h3>TikTok Uploader</h3>
<p>Pick one login method:</p>
<ol>
  <li><b>OAuth</b> (recommended): <a href="/login">Login with TikTok</a></li>
  <li><b>Use existing token</b>: <code>/set-token?access_token=...&open_id=...&expires_in=3600</code></li>
</ol>
<p>
  Once logged in, go to <a href="/form">/form</a> to upload an .mp4<br>
  Debug: <a href="/debug-auth">/debug-auth</a>
</p>
"""

FORM_HTML = """
<h3>Post a TikTok video</h3>
<p>Make sure you either logged in via OAuth or called <code>/set-token?access_token=...</code> in this browser.</p>
<form action="/upload" method="post" enctype="multipart/form-data">
  <div>Video (.mp4): <input type="file" name="file" accept="video/mp4" required></div>
  <div>Caption: <input type="text" name="title" placeholder="Your caption"></div>
  <div>Privacy:
    <select name="privacy">
      <option value="SELF_ONLY" selected>SELF_ONLY (sandbox)</option>
      <option value="PUBLIC_TO_EVERYONE">PUBLIC_TO_EVERYONE</option>
      <option value="MUTUAL_FOLLOW_FRIENDS">MUTUAL_FOLLOW_FRIENDS</option>
      <option value="FOLLOWER_OF_CREATOR">FOLLOWER_OF_CREATOR</option>
    </select>
  </div>
  <div>Cover timestamp (ms): <input type="number" name="cover_ms" value="0"></div>
  <button type="submit">Upload & Post</button>
</form>
<p>
  After upload, check status here: <a href="/status-last" target="_blank">/status-last</a>
</p>
"""

@app.route("/")
def home():
    return render_template_string(HOME_HTML)

@app.route("/form")
def form():
    return render_template_string(FORM_HTML)


# ========= Option A: seed an existing token =========
@app.route("/set-token")
def set_token():
    access_token = request.args.get("access_token")
    if not access_token:
        return "Pass ?access_token=... (&open_id=... [&expires_in=3600] [&refresh_token=...])", 400
    open_id = request.args.get("open_id")
    refresh_token = request.args.get("refresh_token")
    expires_in = int(request.args.get("expires_in", "3600"))
    _save_tokens(access_token, refresh_token, expires_in, open_id)
    return '✅ Token stored. Go to <a href="/form">/form</a> to upload.'


# ========= OAuth routes (optional) =========
@app.route("/debug-auth")
def debug_auth():
    st = session.get("oauth_state") or "(none)"
    return jsonify({
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": st,
        "authorize_url": _build_auth_url(st),
    })

@app.route("/login")
def login():
    if not CLIENT_KEY or not CLIENT_SECRET or not REDIRECT_URI:
        return "Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, REDIRECT_URI in env to use OAuth.", 400
    return redirect(_build_auth_url(_new_state()), 302)

@app.route("/callback")
def callback():
    if request.args.get("error"):
        return f"❌ TikTok error: {request.args['error']}", 400
    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "❌ Missing code.", 400
    if state != session.get("oauth_state"):
        return "❌ State mismatch.", 400

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
    if not r.ok:
        return f"❌ Token exchange failed: {r.status_code} {r.text}", r.status_code
    j = r.json()
    if "access_token" not in j:
        return f"❌ Bad token response: {j}", 400
    _save_tokens(j["access_token"], j.get("refresh_token"), j.get("expires_in", 3600), j.get("open_id"))
    return '✅ Logged in. <a href="/form">Go to /form</a>'


# ========= Upload + Status =========
@app.route("/upload", methods=["POST"])
def upload():
    access_token = _ensure_access_token()
    if not access_token:
        return "Not authorized. Login or call /set-token first.", 401

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file is required (.mp4)"}), 400

    video_bytes = f.read()
    if not video_bytes:
        return jsonify({"error": "uploaded file is empty"}), 400
    video_size = len(video_bytes)

    title    = request.form.get("title", "")
    privacy  = request.form.get("privacy", "SELF_ONLY")
    cover_ms = int(request.form.get("cover_ms", "0"))

    # 1) INIT
    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": cover_ms
        },
        "source_info": {"source": "FILE_UPLOAD"},
        "upload_param": {"video_size": video_size}
    }
    init_res = requests.post(
        DIRECT_POST_INIT,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"},
        json=init_body,
        timeout=60,
    )
    if init_res.status_code >= 400:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_res.text}), init_res.status_code

    init_json = init_res.json()
    if "data" not in init_json or "publish_id" not in init_json["data"]:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_json}), 400

    data = init_json["data"]
    upload_url = data["upload_url"]
    publish_id = data["publish_id"]
    session["last_publish_id"] = publish_id

    # 2) PUT raw video bytes
    put_res = requests.put(upload_url, headers={"Content-Type": "video/mp4"}, data=video_bytes, timeout=300)
    if put_res.status_code >= 400:
        return jsonify({"step": "upload", "status": put_res.status_code, "response": put_res.text}), put_res.status_code

    # 3) First status check
    st = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=30,
    )

    return jsonify({
        "ok": True,
        "publish_id": publish_id,
        "status_first_call": st.json(),
        "status_link": url_for("status_last", _external=True),
    })

@app.route("/status-last")
def status_last():
    access_token = _ensure_access_token()
    if not access_token:
        return jsonify({"error": "Not authorized"}), 401
    pid = session.get("last_publish_id")
    if not pid:
        return jsonify({"error": "No publish_id stored in this session"}), 400
    r = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": pid},
        timeout=30,
    )
    return jsonify({"publish_id": pid, "status": r.json()}), r.status_code


@app.route("/logout")
def logout():
    session.clear()
    return "Session cleared."


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5051)), debug=True)
