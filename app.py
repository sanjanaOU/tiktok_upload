# app.py
import os
import time
import secrets
from urllib.parse import urlencode
from flask import Flask, redirect, request, session, jsonify, render_template_string
import requests

app = Flask(__name__)

# ================= ENV =================
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

CLIENT_KEY        = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET     = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI      = os.getenv("REDIRECT_URI", "").strip()
ACCESS_TOKEN_ENV  = os.getenv("ACCESS_TOKEN", "").strip()  # optional (skip OAuth)

# TikTok API endpoints
AUTH_URL         = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL        = "https://open.tiktokapis.com/v2/oauth/token/"
DIRECT_INIT_URL  = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL       = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
WHOAMI_URL       = "https://open.tiktokapis.com/v2/user/info/"

SCOPES = "user.info.basic,video.upload,video.publish"


# ================= helpers =================
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def save_tokens(access_token, refresh_token=None, expires_in=3600, open_id=None):
    session["tk"] = {
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "expires_at": int(time.time()) + int(expires_in) - 60
    }
    if open_id:
        session["open_id"] = open_id

def get_tokens():
    data = session.get("tk") or {}
    return data.get("access_token"), data.get("refresh_token"), data.get("expires_at", 0)

def access_token():
    tok, _r, exp = get_tokens()
    if tok and time.time() < exp:
        return tok
    if ACCESS_TOKEN_ENV:
        return ACCESS_TOKEN_ENV
    return None


# ================= UI =================
HOME_HTML = """
<h2>TikTok OAuth + Local File Upload</h2>
<ol>
  <li><a href="/login">Login with TikTok</a> → after callback, go to <a href="/form">/form</a></li>
  <li>Skip OAuth (optional): call <code>/set-token?access_token=act....&open_id=...&expires_in=86400</code>, then open <a href="/form">/form</a></li>
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


# ================= auth routes =================
@app.route("/")
def index():
    return render_template_string(HOME_HTML)

@app.route("/debug-auth")
def debug_auth():
    data = {
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": session.get("oauth_state"),
    }
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": session.get("oauth_state") or new_state(),
        "force_verify": "1",
    }
    data["authorize_url"] = AUTH_URL + "?" + urlencode(params)
    return jsonify(data)

@app.route("/login")
def login():
    state = new_state()
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "force_verify": "1",
    }
    return redirect(AUTH_URL + "?" + urlencode(params), code=302)

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
    try:
        tok = r.json()
    except Exception:
        tok = {"raw": r.text}

    if r.status_code != 200:
        return f"❌ Token exchange failed: {r.status_code} {tok}", r.status_code

    data = tok.get("data", tok)
    if "access_token" not in data:
        return f"❌ Token response missing access_token: {tok}", 400

    save_tokens(
        data["access_token"],
        data.get("refresh_token"),
        data.get("expires_in", 3600),
        data.get("open_id"),
    )
    return '✅ Logged in. <a href="/form">Go to /form</a>'

@app.route("/set-token")
def set_token():
    tok = request.args.get("access_token", "").strip()
    if not tok:
        return "Pass ?access_token=act... (&open_id=... [&expires_in=3600] [&refresh_token=...])", 400
    open_id = request.args.get("open_id")
    expires_in = int(request.args.get("expires_in", "3600"))
    refresh_token = request.args.get("refresh_token")
    save_tokens(tok, refresh_token, expires_in, open_id)
    return '✅ Token stored. Go to <a href="/form">/form</a>'

@app.route("/form")
def form():
    if not access_token():
        return "No token yet. Use /login or /set-token.", 401
    return render_template_string(FORM_HTML)

@app.route("/whoami")
def whoami():
    tok = access_token()
    if not tok:
        return {"error": "no token"}, 401
    r = requests.get(WHOAMI_URL, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return {"status": r.status_code, "body": body}, r.status_code


# ================= Direct Post (robust) =================
def try_init_variants(tok, video_size, file_name, mime_type, title, privacy, cover_ms):
    """
    Try up to 3 valid INIT payload shapes. Return (upload_url, publish_id, raw_response_for_debug).
    """
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

    variants = []

    # Variant A (most permissive — usually works)
    variants.append({
        "post_info": {
            "title": title, "privacy_level": privacy,
            "disable_comment": False, "disable_duet": False, "disable_stitch": False,
            "video_cover_timestamp_ms": cover_ms,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "file_name": file_name,
            "mime_type": mime_type,
            "chunk_size": video_size, "chunk_number": 1
        },
        "upload_param": {"video_size": video_size},
    })

    # Variant B (only source_info, minimal)
    variants.append({
        "post_info": {
            "title": title, "privacy_level": privacy,
            "video_cover_timestamp_ms": cover_ms,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "file_name": file_name,
            "mime_type": mime_type,
        }
    })

    # Variant C (video_size only in upload_param)
    variants.append({
        "post_info": {
            "title": title, "privacy_level": privacy,
            "video_cover_timestamp_ms": cover_ms,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "file_name": file_name,
            "mime_type": mime_type,
        },
        "upload_param": {"video_size": video_size},
    })

    for idx, body in enumerate(variants, 1):
        app.logger.info(f"[INIT v{idx}] body={body}")
        res = requests.post(DIRECT_INIT_URL, headers=headers, json=body, timeout=60)
        app.logger.info(f"[INIT v{idx}] status={res.status_code} text={res.text[:600]}")
        if res.status_code == 200:
            try:
                j = res.json()
            except Exception:
                j = {"raw": res.text}
            data = j.get("data", j)
            if data.get("upload_url") and data.get("publish_id"):
                return data["upload_url"], data["publish_id"], j
        # if 4xx, try next variant
    # all failed
    return None, None, {"last_response_text": res.text, "last_status": res.status_code}

@app.route("/upload", methods=["POST"])
def upload():
    tok = access_token()
    if not tok:
        return "Not authorized. Use /login or /set-token.", 401

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file is required (.mp4)"}), 400

    video_bytes = f.read()
    if not video_bytes:
        return jsonify({"error": "uploaded file is empty"}), 400

    video_size = len(video_bytes)
    file_name  = getattr(f, "filename", "video.mp4") or "video.mp4"
    mime_type  = "video/mp4"
    title      = request.form.get("title", "")
    privacy    = request.form.get("privacy", "SELF_ONLY")
    cover_ms   = int(request.form.get("cover_ms", "0"))

    app.logger.info(f"[UPLOAD] size={video_size} name={file_name} mime={mime_type}")

    upload_url, publish_id, init_debug = try_init_variants(
        tok, video_size, file_name, mime_type, title, privacy, cover_ms
    )
    if not upload_url or not publish_id:
        return jsonify({
            "step": "init",
            "status": init_debug.get("last_status", 400),
            "response": init_debug
        }), 400

    session["last_publish_id"] = publish_id

    # PUT to pre-signed URL
    put_res = requests.put(upload_url, headers={"Content-Type": mime_type}, data=video_bytes, timeout=300)
    app.logger.info(f"[PUT] status={put_res.status_code} text={put_res.text[:200]}")

    if put_res.status_code >= 400:
        return jsonify({"step": "upload", "status": put_res.status_code, "response": put_res.text}), put_res.status_code

    # First status check
    st = requests.post(
        STATUS_URL,
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
        "status_link": "/status-last",
    })

@app.route("/status-last")
def status_last():
    tok = access_token()
    if not tok:
        return jsonify({"error": "no token"}), 401
    pid = session.get("last_publish_id")
    if not pid:
        return jsonify({"error": "no publish_id stored in this session"}), 400

    r = requests.post(
        STATUS_URL,
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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5051)), debug=True)
