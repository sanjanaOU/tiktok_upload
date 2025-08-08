import os, time, secrets
from urllib.parse import urlencode
from flask import Flask, redirect, request, session, jsonify, render_template
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()  # MUST match console exactly

# Hardcode scopes (space-separated; no commas)
SCOPES = "user.info.basic video.upload video.publish"

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DIRECT_POST_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))


# ---------- token helpers ----------
def save_tokens(access_token, refresh_token, expires_in):
    session["tk"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + int(expires_in) - 60,  # refresh 60s early
    }

def get_tokens():
    data = session.get("tk") or {}
    return data.get("access_token"), data.get("refresh_token"), data.get("expires_at", 0)

def ensure_access_token():
    access_token, refresh_token, expires_at = get_tokens()
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
    save_tokens(data["access_token"], data["refresh_token"], data["expires_in"])
    return data["access_token"]


# ---------- routes ----------
@app.route("/")
def home():
    access_token, *_ = get_tokens()
    if not access_token:
        state = secrets.token_urlsafe(24)
        session["oauth_state"] = state
        q = {
            "client_key": CLIENT_KEY,
            "response_type": "code",
            "scope": SCOPES,               # space-separated
            "redirect_uri": REDIRECT_URI,  # EXACT match with console
            "state": state,
        }
        auth_url = f"{AUTH_URL}?{urlencode(q)}"
        print("AUTH:", auth_url)  # keep for debugging
        return redirect(auth_url, code=302)
    return render_template("index.html")


@app.route("/callback")
def callback():
    if request.args.get("error"):
        return f"❌ TikTok error: {request.args['error']}", 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "❌ Missing ?code", 400
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
            "redirect_uri": REDIRECT_URI,  # identical to / authorize
        },
        timeout=30,
    )
    if r.status_code >= 400:
        return f"❌ Token exchange failed: {r.status_code} {r.text}", r.status_code

    data = r.json()
    if "access_token" not in data:
        return f"❌ Token response missing access_token: {data}", 400

    save_tokens(data["access_token"], data["refresh_token"], data["expires_in"])
    session["open_id"] = data.get("open_id")
    return redirect("/")


@app.route("/post", methods=["POST"])
def post_video():
    """
    Multipart form-data:
      file     : required .mp4
      title    : optional caption
      privacy  : SELF_ONLY | PUBLIC_TO_EVERYONE | MUTUAL_FOLLOW_FRIENDS | FOLLOWER_OF_CREATOR
      cover_ms : optional int (ms)
    """
    access_token = ensure_access_token()
    if not access_token:
        return jsonify({"error": "Not authorized"}), 401

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file is required (mp4)"}), 400

    title = request.form.get("title", "")
    privacy = request.form.get("privacy", "SELF_ONLY")  # sandbox usually private
    cover_ms = int(request.form.get("cover_ms", "0"))

    # 1) Initialize Direct Post with FILE_UPLOAD (avoids verified-domain requirement)
    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": cover_ms
        },
        "source_info": { "source": "FILE_UPLOAD" }
    }
    init = requests.post(
        DIRECT_POST_INIT,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"},
        json=init_body,
        timeout=60,
    )
    if init.status_code >= 400:
        return jsonify({"step": "init", "status": init.status_code, "response": init.text}), init.status_code

    data = init.json()["data"]
    upload_url = data["upload_url"]
    publish_id = data["publish_id"]

    # 2) Upload raw bytes
    up = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=f.read(),
        timeout=300,
    )
    if up.status_code >= 400:
        return jsonify({"step": "upload", "status": up.status_code, "response": up.text}), up.status_code

    # 3) Fetch status (PROCESSING/SUCCESS/FAILED)
    status = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=30,
    )
    return jsonify({"ok": True, "publish_id": publish_id, "status": status.json()})


@app.route("/status")
def get_status():
    access_token = ensure_access_token()
    if not access_token:
        return jsonify({"error": "Not authorized"}), 401
    pid = request.args.get("publish_id")
    if not pid:
        return jsonify({"error": "publish_id required"}), 400
    r = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": pid},
        timeout=30,
    )
    return jsonify(r.json()), r.status_code


@app.route("/debug-auth")
def debug_auth():
    # Quick view of what we're sending to TikTok
    q = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": "debug",
    }
    return {"SCOPES": SCOPES, "REDIRECT_URI": REDIRECT_URI, "AUTH_URL": f"{AUTH_URL}?{urlencode(q)}"}


@app.route("/logout")
def logout():
    session.clear()
    return "Logged out. Reload / to re-auth."


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
