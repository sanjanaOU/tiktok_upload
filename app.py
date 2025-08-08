import os, time, secrets
from urllib.parse import urlencode
from flask import Flask, request, redirect, session, jsonify, render_template
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")               # must match TikTok console exactly
SCOPES = os.getenv("TIKTOK_SCOPES", "user.info.basic video.upload video.publish")
FLASK_SECRET = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(16))

# TikTok endpoints
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DIRECT_POST_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

app = Flask(__name__)
app.secret_key = FLASK_SECRET


# ---- helpers ----
def _get_tokens():
    d = session.get("tk", {})
    return d.get("access_token"), d.get("refresh_token"), d.get("expires_at", 0)

def _save_tokens(access_token, refresh_token, expires_in):
    session["tk"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + int(expires_in) - 60,  # refresh 60s early
    }

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
    data = r.json()
    _save_tokens(data["access_token"], data["refresh_token"], data["expires_in"])
    return data["access_token"]


# ---- routes ----
@app.route("/")
def index():
    access_token, *_ = _get_tokens()
    if not access_token:
        # build auth URL
        state = secrets.token_urlsafe(16)
        session["state"] = state
        q = {
            "client_key": CLIENT_KEY,
            "response_type": "code",
            "scope": SCOPES,                 # spaces will be URL-encoded
            "redirect_uri": REDIRECT_URI,    # MUST match TikTok console exactly
            "state": state,
        }
        # helpful for debugging:
        print("AUTH:", f"{AUTH_URL}?{urlencode(q)}")
        return redirect(f"{AUTH_URL}?{urlencode(q)}")
    return render_template("index.html")


@app.route("/callback")
def callback():
    # TikTok sends ?code=...&state=...
    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "Missing 'code' from TikTok.", 400
    if state != session.get("state"):
        return "State mismatch.", 400

    # exchange code for tokens
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
    if r.status_code >= 400:
        return f"Token exchange failed: {r.status_code} {r.text}", r.status_code

    data = r.json()
    _save_tokens(data["access_token"], data["refresh_token"], data["expires_in"])
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return "Logged out. Reload / to re-auth."


@app.route("/post", methods=["POST"])
def post_video():
    """Multipart form:
       file : .mp4 (required)
       title: caption (optional)
       privacy: SELF_ONLY | PUBLIC_TO_EVERYONE | MUTUAL_FOLLOW_FRIENDS | FOLLOWER_OF_CREATOR
       cover_ms: optional int
    """
    access_token = _ensure_access_token()
    if not access_token:
        return jsonify({"error": "Not authorized"}), 401

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file (mp4) is required"}), 400

    title = request.form.get("title", "")
    privacy = request.form.get("privacy", "SELF_ONLY")  # sandbox often forces private
    cover_ms = int(request.form.get("cover_ms", "0"))

    # 1) Initialize Direct Post (FILE_UPLOAD avoids verified-domain requirement)
    init_payload = {
        "post_info": {
            "title": title,
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": cover_ms
        },
        "source_info": {
            "source": "FILE_UPLOAD"
        }
    }
    init_res = requests.post(
        DIRECT_POST_INIT,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=init_payload,
        timeout=60,
    )
    if init_res.status_code >= 400:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_res.text}), init_res.status_code

    init = init_res.json()["data"]
    upload_url = init["upload_url"]
    publish_id = init["publish_id"]

    # 2) Upload raw bytes to the provided upload_url
    video_bytes = f.read()
    put_res = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=video_bytes,
        timeout=300,
    )
    if put_res.status_code >= 400:
        return jsonify({"step": "upload", "status": put_res.status_code, "response": put_res.text}), put_res.status_code

    # 3) Fetch processing status (may return PROCESSING; you can poll client-side)
    status_res = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=30,
    )

    return jsonify({
        "ok": True,
        "publish_id": publish_id,
        "status": status_res.json()
    })


if __name__ == "__main__":
    # For local testing. On Render, use gunicorn (see below).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
