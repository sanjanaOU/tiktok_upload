import os
import io
import secrets
import tempfile
from urllib.parse import urlencode

from flask import Flask, request, session, redirect, jsonify, render_template_string
import requests

app = Flask(__name__)

# ====== ENV ======
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))
CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()

# ====== TikTok endpoints ======
AUTH_URL   = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL  = "https://open.tiktokapis.com/v2/oauth/token/"
ASSET_UPLOAD_URL = "https://open.tiktokapis.com/v2/assets/upload/"           # <— CHANGED
CONTAINER_URL    = "https://open.tiktokapis.com/v2/post/publish/container/"
PUBLISH_URL      = "https://open.tiktokapis.com/v2/post/publish/"

SCOPES = "user.info.basic,video.upload,video.publish"

# ====== Download constraints ======
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm"}

# ---------- helpers ----------
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def as_json_safe(resp):
    try:
        return resp.json()
    except Exception:
        return {"non_json_body": resp.text, "status": resp.status_code}

def require_login():
    if not session.get("access_token"):
        return redirect("/login")
    return None

def download_video_to_temp(url: str):
    try:
        head = requests.head(url, allow_redirects=True, timeout=15)
    except Exception as e:
        raise ValueError(f"HEAD request failed: {e}")

    content_type = (head.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    content_len = head.headers.get("Content-Length")
    size = None
    if content_len:
        try:
            size = int(content_len)
            if size <= 0 or size > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"File too large/zero (reported {size}). Cap={MAX_DOWNLOAD_BYTES}.")
        except ValueError:
            size = None

    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        # Optional: enforce types strictly; comment out to allow anything
        pass

    try:
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()
    except Exception as e:
        raise ValueError(f"GET stream failed: {e}")

    ctype_stream = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype_stream:
        content_type = ctype_stream

    total = 0
    suffix = ".mp4" if "mp4" in content_type else ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            tmp.write(chunk)
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"Downloaded over limit ({total} > {MAX_DOWNLOAD_BYTES}).")
    finally:
        tmp.flush()
        tmp.close()

    return tmp.name, (content_type or "application/octet-stream"), total

# ---------- UI ----------
INDEX_HTML = """
<h2>TikTok Upload (push-by-file via Assets API)</h2>
<p>
  <a href="/login">Login</a> |
  <a href="/debug-auth">Debug</a> |
  <a href="/upload">Upload by public URL</a>
</p>
{% if access_token %}
  <p>✅ Logged in. open_id: {{ open_id }}</p>
{% else %}
  <p>❌ Not logged in.</p>
{% endif %}
"""

UPLOAD_HTML = """
<h3>Upload a public video URL (server downloads & uploads file to TikTok)</h3>
{% if not access_token %}
  <p style="color:red">Please <a href="/login">login</a> first.</p>
{% endif %}
<form action="/upload-by-file" method="post">
  <div>
    <label>Public Video URL (.mp4/.mov/.mkv/.webm)</label><br>
    <input style="width: 520px" type="url" name="video_url" required placeholder="https://.../file.mp4" />
  </div>
  <div style="margin-top:8px">
    <label>Caption</label><br>
    <input style="width: 520px" type="text" name="caption" maxlength="2200" placeholder="My caption"/>
  </div>
  <div style="margin-top:12px">
    <button type="submit">Upload & Publish</button>
  </div>
</form>
"""

# ---------- routes ----------
@app.route("/")
def index():
    return render_template_string(
        INDEX_HTML,
        access_token=bool(session.get("access_token")),
        open_id=session.get("open_id"),
    )

@app.route("/debug-auth")
def debug_auth():
    params = {
        "client_key": CLIENT_KEY, "response_type": "code", "scope": SCOPES,
        "redirect_uri": REDIRECT_URI, "state": session.get("oauth_state") or "(none yet)",
    }
    return jsonify({
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": session.get("oauth_state"),
        "authorize_url": AUTH_URL + "?" + urlencode(params),
        "have_access_token": bool(session.get("access_token")),
        "open_id": session.get("open_id"),
    })

@app.route("/login")
def login():
    state = new_state()
    params = {
        "client_key": CLIENT_KEY, "response_type": "code", "scope": SCOPES,
        "redirect_uri": REDIRECT_URI, "state": state,
    }
    return redirect(AUTH_URL + "?" + urlencode(params), code=302)

@app.route("/callback")
def callback():
    if request.args.get("error"):
        return f"❌ TikTok error: {request.args['error']}", 400
    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "❌ Missing ?code from TikTok.", 400
    if session.get("oauth_state") != state:
        return "❌ State mismatch. Start login again.", 400

    payload = {
        "client_key": CLIENT_KEY, "client_secret": CLIENT_SECRET,
        "code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI,
    }
    r = requests.post(TOKEN_URL,
                      headers={"Content-Type": "application/x-www-form-urlencoded"},
                      data=urlencode(payload), timeout=30)
    token_json = as_json_safe(r)
    if r.status_code != 200 or "access_token" not in token_json:
        return ("❌ Token exchange failed:<br><pre>" + str(token_json) + "</pre>", 400)

    session["access_token"] = token_json["access_token"]
    session["open_id"] = token_json.get("open_id")
    return (f"✅ Logged in as open_id={session.get('open_id')}<br>"
            f"<pre>{token_json}</pre><p><a href=\"/upload\">Go to upload form</a></p>")

@app.route("/upload", methods=["GET"])
def upload_form():
    return render_template_string(UPLOAD_HTML, access_token=bool(session.get("access_token")))

@app.route("/upload-by-file", methods=["POST"])
def upload_by_file():
    if (redir := require_login()) is not None:
        return redir

    access_token = session.get("access_token")
    open_id = session.get("open_id")
    video_url = (request.form.get("video_url") or "").strip()
    caption = (request.form.get("caption") or "").strip()

    if not video_url:
        return "Missing video_url", 400

    # 1) Download
    try:
        path, content_type, size = download_video_to_temp(video_url)
    except ValueError as e:
        return jsonify({"error": "download_failed", "detail": str(e)}), 400

    # 2) Upload to Assets API (multipart field name MUST be "file")
    with open(path, "rb") as f:
        files = {"file": ("video.mp4", f, content_type or "video/mp4")}
        up = requests.post(
            ASSET_UPLOAD_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            files=files,
            timeout=300,
        )
    up_json = as_json_safe(up)
    if up.status_code != 200:
        return jsonify({"step": "asset_upload", "status": up.status_code, "response": up_json}), 400

    asset_id = up_json.get("data", {}).get("asset_id") or up_json.get("asset_id")
    if not asset_id:
        return jsonify({"step": "asset_upload", "status": up.status_code, "response": up_json,
                        "error": "asset_id missing"}), 400

    # 3) Create container (source_info -> FILE -> video_post.asset_id)
    c_payload = {
        "source_info": {
            "source": "FILE",
            "video_post": {
                "asset_id": asset_id,
                "caption": caption or ""
            }
        }
    }
    c = requests.post(
        CONTAINER_URL,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=c_payload, timeout=60,
    )
    c_json = as_json_safe(c)
    if c.status_code != 200:
        return jsonify({"step": "create_container", "status": c.status_code, "response": c_json}), 400

    container_id = c_json.get("data", {}).get("container_id") or c_json.get("container_id")
    if not container_id:
        return jsonify({"step": "create_container", "status": c.status_code, "response": c_json,
                        "error": "container_id missing"}), 400

    # 4) Publish
    p_payload = {"open_id": open_id, "container_id": container_id}
    p = requests.post(
        PUBLISH_URL,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=p_payload, timeout=60,
    )
    p_json = as_json_safe(p)
    if p.status_code != 200:
        return jsonify({"step": "publish", "status": p.status_code, "response": p_json}), 400

    return jsonify({"ok": True, "message": "Upload + Publish succeeded",
                    "asset_upload": up_json, "container": c_json, "publish": p_json})

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=True)
