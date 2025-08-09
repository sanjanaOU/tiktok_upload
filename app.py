import os
import secrets
import mimetypes
from urllib.parse import urlencode
from flask import Flask, request, redirect, session, jsonify, render_template_string
import requests

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()

AUTH_URL  = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPES    = "user.info.basic,video.upload,video.publish"

# Content Posting API endpoints (these names work for both direct & draft)
INIT_URL     = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
COMPLETE_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/complete/"
PUBLISH_URL  = "https://open.tiktokapis.com/v2/post/publish/video/"

# ---------- OAuth helpers ----------
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

@app.route("/")
def index():
    return render_template_string("""
    <h2>TikTok OAuth + Local File Upload</h2>
    <ol>
      <li><a href="/login">Login with TikTok</a> → after callback, go to <a href="/form">/form</a></li>
      <li>Skip OAuth: call <code>/set-token?access_token=act...&open_id=...&expires_in=86400</code>, then open <a href="/form">/form</a></li>
    </ol>
    Debug: <a href="/debug-auth">/debug-auth</a> • WhoAmI: <a href="/whoami">/whoami</a> • Health: <a href="/health">/health</a>
    """)

@app.route("/debug-auth")
def debug_auth():
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": session.get("oauth_state") or new_state(),
        "force_verify": "1",
    }
    return jsonify({
        "authorize_url": AUTH_URL + "?" + urlencode(params),
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": session.get("oauth_state"),
    })

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

    if session.get("oauth_state") != state:
        return "❌ State mismatch. Start login again.", 400

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    body = urlencode({
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    })
    r = requests.post(TOKEN_URL, headers=headers, data=body, timeout=30)
    j = r.json() if r.headers.get("content-type","").startswith("application/json") else {"raw": r.text}
    if r.status_code != 200 or "access_token" not in j:
        return f"❌ Token exchange failed: {j}", 400

    session["access_token"] = j["access_token"]
    session["open_id"] = j.get("open_id")
    return f"✅ Got token for open_id={session.get('open_id')}<br><pre>{j}</pre>"

@app.route("/set-token")
def set_token():
    t = request.args.get("access_token")
    open_id = request.args.get("open_id") or "(unknown)"
    if not t:
        return "pass ?access_token=... (&open_id=...)", 400
    session["access_token"] = t
    session["open_id"] = open_id
    session["expires_in"] = request.args.get("expires_in", "3600")
    return jsonify({"ok": True, "saved_open_id": open_id})

@app.route("/whoami")
def whoami():
    return jsonify({
        "has_token": bool(session.get("access_token")),
        "open_id": session.get("open_id"),
        "expires_in": session.get("expires_in"),
    })

# ---------- Upload Form ----------
FORM_HTML = """
<!doctype html>
<title>TikTok uploader (with Draft fallback)</title>
<h3>Upload and Publish</h3>
<form action="/upload" method="post" enctype="multipart/form-data">
  <p>Video (.mp4): <input type="file" name="video" accept="video/mp4" required></p>
  <p>Caption: <input type="text" name="title" value="Posted via API"></p>
  <p>Privacy:
    <select name="privacy">
      <option value="SELF_ONLY">SELF_ONLY (Sandbox safe)</option>
      <option value="MUTUAL_FOLLOW_FRIENDS">MUTUAL_FOLLOW_FRIENDS</option>
      <option value="PUBLIC">PUBLIC</option>
    </select>
  </p>
  <p>Cover timestamp (ms): <input type="number" name="cover_ms" value="0"></p>
  <button type="submit">Upload & Publish</button>
</form>
"""

@app.route("/form")
def form():
    if not session.get("access_token"):
        return 'No token yet. <a href="/login">Login</a> or call /set-token?access_token=...', 401
    return FORM_HTML

# ---------- Core: INIT (direct), fallback to DRAFT, upload, complete, publish ----------
def init_direct_or_draft(tok, video_size, file_name, mime_type, caption, privacy, cover_ms):
    """
    1) Try DIRECT_POST init
    2) If 4xx -> try DRAFT init (no post_mode)
    Returns (upload_url, publish_id, used_mode, raw_json) or (None, None, None, last_json)
    """
    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    # DIRECT INIT
    body_direct = {
        "post_mode": "DIRECT_POST",
        "post_info": {
            "caption": caption,             # caption works widely; title also OK for many tenants
            "privacy_level": privacy,
            "video_cover_timestamp_ms": cover_ms,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "file_name": file_name,
            "mime_type": mime_type,
        },
        "upload_param": {"video_size": int(video_size)}
    }
    r1 = requests.post(INIT_URL, headers=headers, json=body_direct, timeout=60)
    app.logger.info(f"[INIT direct] status={r1.status_code} text={r1.text[:1200]}")
    if r1.status_code == 200:
        j = r1.json()
        data = j.get("data", j)
        if data.get("upload_url") and data.get("publish_id"):
            return data["upload_url"], data["publish_id"], "DIRECT_POST", j

    # DRAFT INIT (fallback)
    body_draft = {
        "post_info": {
            "caption": caption,
            "privacy_level": privacy,
            "video_cover_timestamp_ms": cover_ms,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "file_name": file_name,
            "mime_type": mime_type,
        },
        "upload_param": {"video_size": int(video_size)}
    }
    r2 = requests.post(INIT_URL, headers=headers, json=body_draft, timeout=60)
    app.logger.info(f"[INIT draft] status={r2.status_code} text={r2.text[:1200]}")
    if r2.status_code == 200:
        j = r2.json()
        data = j.get("data", j)
        if data.get("upload_url") and data.get("publish_id"):
            return data["upload_url"], data["publish_id"], "DRAFT", j

    # All failed
    last = r2 if r2 is not None else r1
    last_json = {"status": getattr(last, "status_code", 0), "text": getattr(last, "text", "")}
    return None, None, None, last_json

def tiktok_complete(tok, publish_id):
    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    body = {"publish_id": publish_id}
    return requests.post(COMPLETE_URL, headers=headers, json=body, timeout=60)

def tiktok_publish(tok, publish_id):
    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    body = {"publish_id": publish_id}
    return requests.post(PUBLISH_URL, headers=headers, json=body, timeout=60)

@app.route("/upload", methods=["POST"])
def upload():
    if not session.get("access_token"):
        return "No access token. Login first.", 401

    tok = session["access_token"]
    f = request.files.get("video")
    if not f or not f.filename:
        return "Please choose a .mp4 file.", 400

    video_bytes = f.read()
    video_size = len(video_bytes)
    file_name = f.filename
    mime_type = mimetypes.guess_type(file_name)[0] or "video/mp4"

    caption = (request.form.get("title") or "").strip() or "Posted via API"
    privacy = (request.form.get("privacy") or "SELF_ONLY").strip()
    cover_ms = int(request.form.get("cover_ms") or 0)

    # 1) INIT (direct, then fallback to draft)
    upload_url, publish_id, mode_used, init_json = init_direct_or_draft(
        tok, video_size, file_name, mime_type, caption, privacy, cover_ms
    )
    if not upload_url:
        return jsonify({"status": 400, "step": "init", "response": init_json}), 400

    # 2) UPLOAD
    r_up = requests.put(upload_url, data=video_bytes, headers={"Content-Type": mime_type}, timeout=600)
    if r_up.status_code not in (200, 201, 204):
        return jsonify({"status": r_up.status_code, "step": "upload", "response": r_up.text}), 400

    # 3) COMPLETE
    r_comp = tiktok_complete(tok, publish_id)
    if r_comp.status_code != 200:
        return jsonify({"status": r_comp.status_code, "step": "complete", "response": r_comp.text}), 400

    # 4) PUBLISH (works for both direct & draft)
    r_pub = tiktok_publish(tok, publish_id)
    return jsonify({
        "status": r_pub.status_code,
        "mode_used": mode_used,          # "DIRECT_POST" or "DRAFT"
        "publish_response": r_pub.json() if r_pub.headers.get("content-type","").startswith("application/json") else r_pub.text
    }), (200 if r_pub.status_code == 200 else 400)

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=True)
