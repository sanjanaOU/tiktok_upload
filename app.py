import os
import json
import secrets
from urllib.parse import urlencode

import requests
from flask import (
    Flask, request, redirect, session, jsonify, render_template_string
)

# -------- Flask setup --------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

# -------- TikTok OAuth config (set these in Render Environment) --------
CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()  # e.g. https://YOUR-SERVICE.onrender.com/callback

AUTH_URL  = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

# Direct Post endpoints
DIRECT_INIT_URL   = "https://open.tiktokapis.com/v2/post/publish/video/init/"
DIRECT_SUBMIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/submit/"

# Scopes we request
SCOPES = "user.info.basic,video.upload,video.publish"

# TikTok size guard. TikTok allows ~287MB.
MAX_FILE_SIZE = 287 * 1024 * 1024  # 287MB


# =======================
#        HTML
# =======================
INDEX_HTML = """
<h2>TikTok Direct Post (Sandbox)</h2>
<ol>
  <li><a href="/login">Login with TikTok</a></li>
  <li>After callback you’ll be redirected to <a href="/upload">/upload</a></li>
</ol>
<p>Debug: <a href="/debug-auth">/debug-auth</a> • <a href="/whoami">/whoami</a> • <a href="/health">/health</a></p>
"""

UPLOAD_FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>TikTok Direct Post</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 640px; margin: 40px auto; padding: 16px; }
    .form-group { margin-bottom: 14px; }
    label { display:block; margin-bottom:5px; font-weight:600; }
    input, textarea, select { width:100%; padding:8px; border:1px solid #ddd; border-radius:6px; }
    textarea { height:90px; resize:vertical; }
    small { color:#666; }
    button { background:#ff0050; color:#fff; padding:10px 16px; border:none; border-radius:6px; cursor:pointer; }
    button:hover { background:#e6004a; }
  </style>
</head>
<body>
  <h2>Direct Post to TikTok</h2>

  {% if not session.get('access_token') %}
    <p>❌ You need to authenticate first: <a href="/login">Login with TikTok</a></p>
  {% else %}
    <p>✅ Authenticated as: {{ session.get('open_id', 'Unknown') }}</p>

    <form method="POST" enctype="multipart/form-data">
      <div class="form-group">
        <label>Video File (.mp4)</label>
        <input type="file" name="video_file" accept=".mp4,video/mp4" required>
      </div>

      <div class="form-group">
        <label>Caption</label>
        <textarea name="caption" placeholder="Write your caption (optional)"></textarea>
        <small>Max ~2,200 chars (TikTok may truncate).</small>
      </div>

      <div class="form-group">
        <label>Privacy</label>
        <select name="privacy">
          <option value="PUBLIC_TO_EVERYONE">Public</option>
          <option value="FRIENDS">Friends</option>
          <option value="PRIVATE">Private</option>
        </select>
      </div>

      <div class="form-group">
        <label>Cover timestamp (ms)</label>
        <input type="number" name="cover_ts" value="0" min="0">
        <small>0 = let TikTok pick a frame</small>
      </div>

      <div class="form-group">
        <label><input type="checkbox" name="disable_duet"> Disable Duet</label><br>
        <label><input type="checkbox" name="disable_comment"> Disable Comments</label><br>
        <label><input type="checkbox" name="disable_stitch"> Disable Stitch</label>
      </div>

      <button type="submit">Publish Now</button>
    </form>

    <p><a href="/logout">Logout</a></p>
  {% endif %}

  <p><a href="/">← Back to Home</a></p>
</body>
</html>
"""


# =======================
#      Helpers
# =======================
def new_state() -> str:
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s


def auth_headers():
    token = session.get("access_token")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def direct_post_to_tiktok(
    video_file,
    caption="",
    privacy_level="PUBLIC_TO_EVERYONE",
    disable_duet=False,
    disable_comment=False,
    disable_stitch=False,
    cover_timestamp_ms=0,
):
    """
    Direct Post: init -> PUT upload -> submit
    """
    headers = auth_headers()
    if not headers:
        return {"error": "Not authenticated"}

    try:
        # --- size ---
        video_file.seek(0, 2)
        size = video_file.tell()
        video_file.seek(0)

        # --- INIT ---
        init_payload = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1
            }
        }
        h = auth_headers()
        h["Content-Type"] = "application/json; charset=UTF-8"
        r = requests.post(DIRECT_INIT_URL, headers=h, data=json.dumps(init_payload), timeout=30)

        print("INIT status:", r.status_code)
        print("INIT body:", r.text)

        if r.status_code != 200:
            return {"error": f"Init failed (HTTP {r.status_code}): {r.text}"}
        j = r.json()
        if j.get("error", {}).get("code") != "ok" or "data" not in j:
            return {"error": f"Init API error: {j}"}

        data = j["data"]
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            return {"error": f"Init response missing publish_id or upload_url: {j}"}

        # --- UPLOAD (PUT to upload_url) ---
        video_file.seek(0)
        up_headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size-1}/{size}",
        }
        up = requests.put(upload_url, headers=up_headers, data=video_file.read(), timeout=180)

        print("UPLOAD status:", up.status_code)
        print("UPLOAD body:", up.text)

        if up.status_code not in (200, 201, 202, 204):
            return {"error": f"Upload failed (HTTP {up.status_code}): {up.text}"}

        # --- SUBMIT ---
        submit_payload = {
            "publish_id": publish_id,
            "post_info": {
                "caption": caption or "",
                "privacy_level": privacy_level,
                "disable_duet": bool(disable_duet),
                "disable_comment": bool(disable_comment),
                "disable_stitch": bool(disable_stitch),
                "cover_timestamp_ms": int(cover_timestamp_ms),
            },
        }
        h2 = auth_headers()
        h2["Content-Type"] = "application/json; charset=UTF-8"
        s = requests.post(DIRECT_SUBMIT_URL, headers=h2, data=json.dumps(submit_payload), timeout=30)

        print("SUBMIT status:", s.status_code)
        print("SUBMIT body:", s.text)

        if s.status_code != 200:
            return {"error": f"Submit failed (HTTP {s.status_code}): {s.text}"}
        sj = s.json()
        if sj.get("error", {}).get("code") != "ok":
            return {"error": f"Submit API error: {sj}"}

        return {
            "success": True,
            "publish_id": publish_id,
            "result": sj.get("data", {}),
            "message": "Video has been published (Direct Post).",
        }
    except Exception as e:
        return {"error": f"Exception during direct post: {str(e)}"}


# =======================
#       Routes
# =======================
@app.route("/")
def index():
    return INDEX_HTML


@app.route("/debug-auth")
def debug_auth():
    data = {
        "client_key": CLIENT_KEY,
        "redirect_uri": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": session.get("oauth_state"),
        "authenticated": bool(session.get("access_token")),
        "open_id": session.get("open_id"),
    }
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": session.get("oauth_state") or "(none yet)",
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
        # "force_verify": "1",  # uncomment if you want to force re-consent each time
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
    payload = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }
    r = requests.post(TOKEN_URL, headers=headers, data=urlencode(payload), timeout=30)

    try:
        token_json = r.json()
    except Exception:
        token_json = {"raw": r.text}

    if r.status_code != 200 or "access_token" not in token_json:
        return "❌ Token exchange failed: " + json.dumps(token_json), 400

    session["access_token"] = token_json["access_token"]
    session["open_id"] = token_json.get("open_id")

    return redirect("/upload")


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template_string(UPLOAD_FORM_HTML, session=session)

    if not session.get("access_token"):
        return "❌ Not authenticated. Please login first.", 401

    f = request.files.get("video_file")
    if not f or not f.filename:
        return "❌ No video file selected", 400
    if not f.filename.lower().endswith(".mp4"):
        return "❌ Only MP4 files are supported", 400

    # size check
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > MAX_FILE_SIZE:
        return f"❌ File too large. Max is {MAX_FILE_SIZE // (1024*1024)} MB", 400

    caption = (request.form.get("caption") or "").strip()
    privacy = request.form.get("privacy") or "PUBLIC_TO_EVERYONE"
    cover_ts = int(request.form.get("cover_ts") or "0")
    disable_duet = bool(request.form.get("disable_duet"))
    disable_comment = bool(request.form.get("disable_comment"))
    disable_stitch = bool(request.form.get("disable_stitch"))

    result = direct_post_to_tiktok(
        video_file=f,
        caption=caption,
        privacy_level=privacy,
        disable_duet=disable_duet,
        disable_comment=disable_comment,
        disable_stitch=disable_stitch,
        cover_timestamp_ms=cover_ts,
    )

    if result.get("error"):
        return f"❌ Publish failed: {result['error']}", 400

    return f"""
    ✅ Direct Post successful!<br>
    Publish ID: {result.get('publish_id')}<br>
    <pre>{json.dumps(result.get('result', {}), indent=2)}</pre>
    <br>
    <a href="/upload">Post Another</a> | <a href="/">Home</a>
    """


@app.route("/whoami")
def whoami():
    """Simple check to see if we have a token and open_id"""
    return jsonify({
        "has_token": bool(session.get("access_token")),
        "open_id": session.get("open_id"),
    })


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/health")
def health():
    return "ok", 200


# -------- local run --------
if __name__ == "__main__":
    # For local dev: python app.py
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5051")), debug=True)
