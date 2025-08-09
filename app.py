# app.py
import os, time, secrets
from urllib.parse import urlencode, quote
from flask import Flask, request, session, jsonify, render_template_string, redirect, url_for
import requests

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

# ============ Environment (Render) ============
CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI  = os.getenv("REDIRECT_URI", "").strip()  # e.g. https://tiktok-upload.onrender.com/callback
# Optional: put a token here to skip OAuth entirely
ACCESS_TOKEN_ENV = os.getenv("ACCESS_TOKEN", "").strip()

# TikTok OAuth + Direct Post endpoints
AUTH_URL         = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL        = "https://open.tiktokapis.com/v2/oauth/token/"
DIRECT_POST_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH     = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# TikTok wants scopes space-separated (we'll encode as %20)
SCOPES = "user.info.basic video.upload video.publish"

# ============ Token helpers ============
def _save_tokens(access_token: str, refresh_token: str | None, expires_in: int, open_id: str | None):
    session["tk"] = {
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "expires_at": int(time.time()) + int(expires_in or 3600) - 60,
    }
    if open_id:
        session["open_id"] = open_id

def _get_tokens():
    data = session.get("tk") or {}
    return data.get("access_token"), data.get("refresh_token"), data.get("expires_at", 0)

def _access_token():
    """
    Priority:
      1) OAuth/session token (auto-refresh if we can)
      2) ACCESS_TOKEN env as fallback (no refresh)
    """
    token, refresh, exp = _get_tokens()
    if token:
        if time.time() < exp or not refresh:
            return token
        # try refresh once if we have creds
        if CLIENT_KEY and CLIENT_SECRET:
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
            if r.ok:
                j = r.json()
                data = j.get("data", j)
                if "access_token" in data:
                    _save_tokens(data["access_token"], data.get("refresh_token"), data.get("expires_in", 3600), data.get("open_id"))
                    return data["access_token"]
        return token
    return ACCESS_TOKEN_ENV or None

# ============ OAuth helpers ============
def _new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def _build_auth_url(state: str) -> str:
    # encode scope with %20 (not +) and force consent prompt
    base = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "force_verify": "1",
    }
    return f"{AUTH_URL}?{urlencode(base)}&scope={quote(SCOPES, safe='')}"

# ============ Minimal UI ============
HOME_HTML = """
<h2>TikTok Uploader (OAuth + File Upload)</h2>
<ol>
  <li><b>OAuth</b>: <a href="/login">Login with TikTok</a> → after callback, go to <a href="/form">/form</a></li>
  <li><b>Skip-auth</b>: set ACCESS_TOKEN env on Render or call <code>/set-token?access_token=act....</code>, then open <a href="/form">/form</a></li>
</ol>
<p>
  Debug: <a href="/debug-auth" target="_blank">/debug-auth</a> •
  WhoAmI: <a href="/whoami" target="_blank">/whoami</a> •
  Health: <a href="/health" target="_blank">/health</a>
</p>
"""

FORM_HTML = """
<h3>Post a TikTok video</h3>
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
<p>After upload, check <a href="/status-last" target="_blank">/status-last</a></p>
"""

# ============ Routes ============
@app.route("/")
def home():
    return render_template_string(HOME_HTML)

@app.route("/debug-auth")
def debug_auth():
    st = session.get("oauth_state") or "(none)"
    auth_url = "(set env first)"
    if CLIENT_KEY and REDIRECT_URI:
        auth_url = _build_auth_url(_new_state())
    return jsonify({
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": st,
        "authorize_url": auth_url
    })

@app.route("/login")
def login():
    if not CLIENT_KEY or not CLIENT_SECRET or not REDIRECT_URI:
        return "Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, REDIRECT_URI env vars.", 400
    return redirect(_build_auth_url(_new_state()), 302)

@app.route("/callback")
def callback():
    if request.args.get("error"):
        return f"❌ TikTok error: {request.args['error']}", 400
    code = request.args.get("code"); state = request.args.get("state")
    if not code:
        return "❌ Missing code", 400
    if state != session.get("oauth_state"):
        return "❌ State mismatch", 400

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
    data = j.get("data", j)  # handle both shapes
    if "access_token" not in data:
        return f"❌ Bad token response: {j}", 400

    _save_tokens(data["access_token"], data.get("refresh_token"), data.get("expires_in", 3600), data.get("open_id"))
    return '✅ Logged in. <a href="/form">Go to /form</a>'

@app.route("/set-token")
def set_token():
    tok = request.args.get("access_token", "").strip()
    if not tok:
        return "Pass ?access_token=act.... (&open_id=... [&expires_in=3600] [&refresh_token=...])", 400
    open_id = request.args.get("open_id")
    expires_in = int(request.args.get("expires_in", "3600"))
    refresh_token = request.args.get("refresh_token")
    _save_tokens(tok, refresh_token, expires_in, open_id)
    return '✅ Token stored. Go to <a href="/form">/form</a>.'

@app.route("/form")
def form():
    if not _access_token():
        return "No token available. Use /login or /set-token (or set ACCESS_TOKEN env).", 401
    return render_template_string(FORM_HTML)

@app.route("/whoami")
def whoami():
    tok = _access_token()
    if not tok:
        return {"error": "no token"}, 401
    r = requests.get(
        "https://open.tiktokapis.com/v2/user/info/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=20
    )
    try:
        body = r.json()
    except Exception:
        body = r.text
    return {"status": r.status_code, "body": body}, r.status_code

# ============ Upload + Status ============
@app.route("/upload", methods=["POST"])
def upload():
    tok = _access_token()
    if not tok:
        return "Not authorized. Use /login or /set-token.", 401

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

    # 1) INIT (include upload_param.video_size — required)
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
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=init_body,
        timeout=60,
    )
    if init_res.status_code >= 400:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_res.text}), init_res.status_code

    init_json = init_res.json()
    data = init_json.get("data", init_json)
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id")
    if not upload_url or not publish_id:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_json}), 400

    session["last_publish_id"] = publish_id

    # 2) PUT bytes to upload_url
    put_res = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=video_bytes,
        timeout=300,
    )
    if put_res.status_code >= 400:
        return jsonify({"step": "upload", "status": put_res.status_code, "response": put_res.text}), put_res.status_code

    # 3) First status check
    st = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=30,
    )
    try:
        st_json = st.json()
    except Exception:
        st_json = {"raw": st.text}

    return jsonify({
        "ok": True,
        "publish_id": publish_id,
        "status_http": st.status_code,
        "status_first_call": st_json,
        "status_link": url_for("status_last", _external=True),
    })

@app.route("/status-last")
def status_last():
    tok = _access_token()
    if not tok:
        return jsonify({"error": "no token"}), 401
    pid = session.get("last_publish_id")
    if not pid:
        return jsonify({"error": "no publish_id stored in this session"}), 400
    r = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        json={"publish_id": pid},
        timeout=30,
    )
    try:
        body = r.json()
    except Exception:
        body = r.text
    return jsonify({"publish_id": pid, "status_http": r.status_code, "status_body": body}), r.status_code

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    # PORT is set by Render; defaults to 5051 locally
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5051)), debug=True)
