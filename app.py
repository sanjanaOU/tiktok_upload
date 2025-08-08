import os, secrets, time
from urllib.parse import urlencode
from flask import Flask, redirect, request, session, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI")
SCOPES = os.getenv("TIKTOK_SCOPES", "user.info.basic video.upload video.publish")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DIRECT_POST_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", secrets.token_hex(16))

def _tok():
    t = session.get("tk", {})
    return t.get("access_token"), t.get("refresh_token"), t.get("expires_at", 0)

def _save_tokens(access_token, refresh_token, expires_in):
    session["tk"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + int(expires_in) - 60,
    }

def _ensure_token():
    access_token, refresh_token, expires_at = _tok()
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

@app.route("/")
def home():
    access_token, *_ = _tok()
    if not access_token:
        state = secrets.token_urlsafe(16)
        session["state"] = state
        q = {
            "client_key": CLIENT_KEY,
            "response_type": "code",
            "scope": SCOPES,
            "redirect_uri": REDIRECT_URI,
            "state": state,
        }
        return redirect(f"{AUTH_URL}?{urlencode(q)}")
    return "Authenticated with TikTok. POST /post with form: file=@video.mp4&title=Your+caption"

@app.route("/callback")
def callback():
    if request.args.get("state") != session.get("state"):
        return "State mismatch", 400
    code = request.args.get("code")
    if not code:
        return "Missing code", 400
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
    r.raise_for_status()
    data = r.json()
    _save_tokens(data["access_token"], data["refresh_token"], data["expires_in"])
    return redirect("/")

@app.route("/post", methods=["POST"])
def post_video():
    """
    Form-data:
      file: (binary .mp4)
      title: optional caption
      privacy: PUBLIC_TO_EVERYONE | MUTUAL_FOLLOW_FRIENDS | FOLLOWER_OF_CREATOR | SELF_ONLY
               In sandbox, SELF_ONLY is often required.
      cover_ms: optional int (frame for cover in ms)
    """
    access_token = _ensure_token()
    if not access_token:
        return "Not authorized", 401

    f = request.files.get("file")
    if not f:
        return "file is required (.mp4)", 400

    title = request.form.get("title", "")
    privacy = request.form.get("privacy", "SELF_ONLY")
    cover_ms = int(request.form.get("cover_ms", "0"))

    # 1) Initialize Direct Post (returns upload_url + publish_id)
    init_payload = {
        "post_info": {
            "title": title,
            "privacy_level": privacy,
            "disable_duet": False,
            "disable_stitch": False,
            "disable_comment": False,
            "video_cover_timestamp_ms": cover_ms
        },
        "source_info": {
            "source": "FILE_UPLOAD"   # IMPORTANT: avoids verified-domain requirement
        }
    }
    r = requests.post(
        DIRECT_POST_INIT,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=init_payload,
        timeout=60,
    )
    if r.status_code >= 400:
        return jsonify({"step": "init", "status": r.status_code, "response": r.text}), r.status_code

    init = r.json()
    upload_url = init["data"]["upload_url"]
    publish_id = init["data"]["publish_id"]

    # 2) Upload the bytes to the given upload_url (PUT raw bytes)
    # TikTok inspects content-type but doesn't require multipart here.
    video_bytes = f.read()
    up = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=video_bytes,
        timeout=300,
    )
    if up.status_code >= 400:
        return jsonify({"step": "upload", "status": up.status_code, "response": up.text}), up.status_code

    # 3) Optionally: poll status so you know when it’s posted
    # (TikTok processes asynchronously)
    status = requests.post(
        STATUS_FETCH,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"publish_id": publish_id},
        timeout=30,
    )
    # status may be "PROCESSING" at first
    return jsonify({
        "ok": True,
        "publish_id": publish_id,
        "status_fetch": status.json()
    })
