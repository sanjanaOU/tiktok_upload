import os
import io
import secrets
import tempfile
from urllib.parse import urlencode

import requests
from flask import (
    Flask, request, session, redirect, url_for,
    jsonify, render_template, flash
)

# ------------------ Config & constants ------------------

app = Flask(__name__)

# REQUIRED ENV VARS:
# TIKTOK_CLIENT_KEY
# TIKTOK_CLIENT_SECRET
# REDIRECT_URI       -> e.g. https://tiktok-upload.onrender.com/callback
# FLASK_SECRET_KEY   -> any random string
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()

SCOPES = "user.info.basic,video.upload,video.publish"

# OAuth endpoints
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

# Content Posting endpoints
UPLOAD_URL = "https://open.tiktokapis.com/v2/post/upload/video/"
CONTAINER_URL = "https://open.tiktokapis.com/v2/post/publish/container/"
PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/"

# ------------------ Helpers ------------------

def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def bearer_headers():
    access = session.get("access_token")
    if not access:
        return None
    return {"Authorization": f"Bearer {access}"}

def need_auth():
    return "access_token" not in session or "open_id" not in session

# ------------------ Routes: auth ------------------

@app.route("/")
def home():
    authed = not need_auth()
    return render_template("home.html", authed=authed, open_id=session.get("open_id"))

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
        "state": session.get("oauth_state") or "(none yet)",
    }
    data["authorize_url"] = AUTH_URL + "?" + urlencode(params)
    return jsonify(data)

@app.route("/login")
def login():
    if not CLIENT_KEY or not CLIENT_SECRET or not REDIRECT_URI:
        return "Missing env vars. Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, REDIRECT_URI.", 500

    state = new_state()
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    url = AUTH_URL + "?" + urlencode(params)
    return redirect(url, code=302)

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
        return jsonify({"error": "token_exchange_failed", "status": r.status_code, "body": token_json}), 400

    session["access_token"] = token_json["access_token"]
    session["open_id"] = token_json.get("open_id")

    flash("Authenticated with TikTok.", "success")
    return redirect(url_for("upload_form"))

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("home"))

# ------------------ Routes: upload & publish ------------------

@app.route("/upload")
def upload_form():
    if need_auth():
        return redirect(url_for("login"))
    return render_template("upload.html")

@app.route("/upload-by-url", methods=["POST"])
def upload_by_url():
    if need_auth():
        return redirect(url_for("login"))

    video_url = (request.form.get("video_url") or "").strip()
    caption = (request.form.get("caption") or "").strip()

    if not video_url:
        flash("Please provide a video URL.", "error")
        return redirect(url_for("upload_form"))

    # 1) Download video to temp file (don’t keep in memory for big files)
    try:
        with requests.get(video_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            # sanity check
            ctype = r.headers.get("Content-Type", "")
            if not ("video" in ctype or video_url.lower().endswith(".mp4")):
                flash(f"URL doesn't look like a video (Content-Type: {ctype})", "error")
                return redirect(url_for("upload_form"))

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        tmp.write(chunk)
                temp_path = tmp.name
    except Exception as e:
        flash(f"Download failed: {e}", "error")
        return redirect(url_for("upload_form"))

    # 2) push_by_file -> upload video bytes to TikTok
    headers = bearer_headers()
    if not headers:
        flash("Missing access token. Please login again.", "error")
        return redirect(url_for("login"))

    try:
        with open(temp_path, "rb") as f:
            files = {"video": ("video.mp4", f, "video/mp4")}
            up = requests.post(UPLOAD_URL, headers=headers, files=files, timeout=180)
            up_json = up.json() if up.content else {}
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass

    if up.status_code != 200 or "data" not in up_json or "video_id" not in up_json["data"]:
        return jsonify({"step": "upload", "status_code": up.status_code, "body": up_json}), 400

    video_id = up_json["data"]["video_id"]

    # 3) Create container referencing the uploaded video
    cont_payload = {
        "open_id": session["open_id"],
        "source_info": {
            "source": "UPLOAD",
            "video_id": video_id
        },
        "text": caption[:2200]  # TikTok caption limit
    }
    cont = requests.post(
        CONTAINER_URL,
        headers={**headers, "Content-Type": "application/json"},
        json=cont_payload,
        timeout=60
    )
    cont_json = cont.json() if cont.content else {}
    if cont.status_code != 200 or "data" not in cont_json or "container_id" not in cont_json["data"]:
        return jsonify({"step": "container", "status_code": cont.status_code, "body": cont_json}), 400

    container_id = cont_json["data"]["container_id"]

    # 4) Publish
    pub_payload = {
        "open_id": session["open_id"],
        "container_id": container_id
    }
    pub = requests.post(
        PUBLISH_URL,
        headers={**headers, "Content-Type": "application/json"},
        json=pub_payload,
        timeout=60
    )
    pub_json = pub.json() if pub.content else {}
    if pub.status_code != 200:
        return jsonify({"step": "publish", "status_code": pub.status_code, "body": pub_json}), 400

    flash("🎉 Video published to TikTok!", "success")
    return render_template(
        "upload.html",
        published=True,
        result={
            "video_id": video_id,
            "container_id": container_id,
            "publish": pub_json
        }
    )

# Health check
@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    # Local dev
    app.run(host="0.0.0.0", port=5051, debug=True)
