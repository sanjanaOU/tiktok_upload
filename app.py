import os
import json
import secrets
from urllib.parse import urlencode

import requests
from flask import (
    Flask, request, session, redirect, jsonify, make_response, send_from_directory
)
from werkzeug.utils import secure_filename

# ------------------------------------------------------------------------------
# ENV (set these in Render or your shell)
# ------------------------------------------------------------------------------
CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()
FLASK_SECRET = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

# TikTok endpoints (v2)
AUTH_URL       = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL      = "https://open.tiktokapis.com/v2/oauth/token/"
CONTAINER_URL  = "https://open.tiktokapis.com/v2/post/publish/container/"
PUBLISH_URL    = "https://open.tiktokapis.com/v2/post/publish/"
SCOPES         = "user.info.basic,video.upload,video.publish"

# Flask
app = Flask(__name__)
app.secret_key = FLASK_SECRET

# --- Upload limits (you can adjust these; Render/host may impose their own) ---
# e.g., 200 MB
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v"}

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def json_or_text(resp):
    """Return (status_code, body_dict_or_text, is_json) for robust logging."""
    ct = (resp.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        try:
            return resp.status_code, resp.json(), True
        except Exception:
            pass
    return resp.status_code, {"non_json_body": resp.text}, False

def require_auth() -> bool:
    return bool(session.get("access_token") and session.get("open_id"))

def allowed_filename(filename: str) -> bool:
    _fn = filename.lower()
    return any(_fn.endswith(ext) for ext in ALLOWED_EXTENSIONS)

# ------------------------------------------------------------------------------
# OAuth routes
# ------------------------------------------------------------------------------
@app.route("/")
def index():
    if require_auth():
        return make_response(
            f"""
            <h3>TikTok OAuth ✔</h3>
            <p>open_id: <code>{session.get('open_id')}</code></p>
            <p><a href="/upload">Publish a video from your computer</a></p>
            <p><a href="/logout">Logout</a></p>
            <p><a href="/debug-auth">/debug-auth</a></p>
            """,
            200,
        )
    return make_response(
        """
        <h3>TikTok OAuth</h3>
        <p><a href="/login">Login with TikTok</a></p>
        <p><a href="/debug-auth">/debug-auth</a></p>
        """,
        200,
    )

@app.route("/login")
def login():
    state = new_state()
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": state,
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
        "redirect_uri": REDIRECT_URI,  # must match exactly
    }
    r = requests.post(TOKEN_URL, headers=headers, data=urlencode(payload), timeout=45)
    status, body, _ = json_or_text(r)

    if status != 200 or "access_token" not in body:
        return make_response(
            f"❌ Token exchange failed ({status}):<pre>{body}</pre>",
            400,
        )

    session["access_token"] = body["access_token"]
    session["open_id"] = body.get("open_id")
    return redirect("/upload")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/debug-auth")
def debug_auth():
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": session.get("oauth_state") or "(none yet)",
    }
    return jsonify({
        "client_key_starts_with": CLIENT_KEY[:4] + "***",
        "redirect_uri_from_env": REDIRECT_URI,
        "authorize_url": AUTH_URL + "?" + urlencode(params),
        "has_token": bool(session.get("access_token")),
        "open_id": session.get("open_id"),
    })

# ------------------------------------------------------------------------------
# UI + UPLOAD_BY_FILE flow (local file → backend → TikTok)
# ------------------------------------------------------------------------------
@app.route("/upload", methods=["GET"])
def upload_page():
    if not require_auth():
        return redirect("/login")
    return make_response(
        """
        <h2>Publish to TikTok: Upload a file from your computer</h2>
        <form id="f" enctype="multipart/form-data" onsubmit="return false">
          <label>Select video (.mp4/.mov/.m4v)</label><br>
          <input type="file" name="video" accept="video/*" required /><br><br>
          <label>Caption</label><br>
          <input style="width:600px" name="caption" value="Hello from Content Posting API!"/><br><br>
          <button onclick="doUpload()">Publish</button>
        </form>
        <pre id="out" style="white-space:pre-wrap;background:#111;color:#0f0;padding:10px;"></pre>

        <script>
        async function doUpload() {
          const fd = new FormData(document.getElementById('f'));
          const res = await fetch('/publish-file', {
            method: 'POST',
            body: fd
          });
          let text = await res.text();
          try { text = JSON.stringify(JSON.parse(text), null, 2); } catch {}
          document.getElementById('out').textContent = text;
        }
        </script>
        """,
        200,
    )

@app.route("/publish-file", methods=["POST"])
def publish_file():
    """
    Creates a container with source=UPLOAD_BY_FILE (multipart/form-data),
    then publishes it. No verified domain is needed for this flow.
    """
    if not require_auth():
        return jsonify({"error": "not_authenticated"}), 401

    file = request.files.get("video")
    caption = (request.form.get("caption") or "").strip()

    if not file or file.filename == "":
        return jsonify({"error": "No file uploaded"}), 400

    if not allowed_filename(file.filename):
        return jsonify({"error": "Unsupported file type"}), 400

    # 1) Create publish container (UPLOAD_BY_FILE)
    access_token = session["access_token"]
    open_id = session["open_id"]

    filename = secure_filename(file.filename)
    mime = file.mimetype or "video/mp4"

    # TikTok expects multipart/form-data with:
    #   - files['video'] : the binary
    #   - data['post_info'] : JSON string for meta (e.g., {"text":"..."})
    data = {"post_info": json.dumps({"text": caption})}
    files = {"video": (filename, file.stream, mime)}

    step1_json, step2_json = {}, {}

    try:
        r1 = requests.post(
            CONTAINER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data=data,
            files=files,
            timeout=300,  # large timeout for bigger videos
        )
        status1, body1, _ = json_or_text(r1)
        step1_json = {"status": status1, "response": body1}

        if status1 != 200 or not isinstance(body1, dict) or "data" not in body1 or "container_id" not in body1["data"]:
            return jsonify({"step": "create_container", **step1_json}), 400

        container_id = body1["data"]["container_id"]

        # 2) Publish the container
        r2 = requests.post(
            PUBLISH_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"open_id": open_id, "container_id": container_id},
            timeout=60,
        )
        status2, body2, _ = json_or_text(r2)
        step2_json = {"status": status2, "response": body2}

        if status2 != 200:
            return jsonify({"step": "publish", "container_id": container_id, **step2_json}), 400

        return jsonify({
            "ok": True,
            "container_id": container_id,
            "create_container": step1_json,
            "publish": step2_json,
        }), 200

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "create_container": step1_json,
            "publish": step2_json,
        }), 500

# ------------------------------------------------------------------------------
@app.route("/health")
def health():
    return "ok", 200

# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # Local dev
    app.run(host="0.0.0.0", port=5051, debug=True)
