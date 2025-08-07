from flask import Flask, render_template, request, redirect, session, url_for, jsonify
import os
import requests
from urllib.parse import urlencode

# ====== CONFIG ======
# Set these via environment variables in Render (.env) or locally:
# TIKTOK_CLIENT_KEY=...
# TIKTOK_CLIENT_SECRET=...
# REDIRECT_URI=https://<your-service>.onrender.com/callback
# FLASK_SECRET_KEY=any-long-random-string
CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
FLASK_SECRET = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# TikTok endpoints
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token"
CONTAINER_URL = "https://open.tiktokapis.com/v2/post/publish/container/"
PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/"

app = Flask(__name__)
app.secret_key = FLASK_SECRET


# ====== VIEWS ======

@app.route("/")
def index():
    """Simple form UI"""
    return render_template("index.html",
                           authed=bool(session.get("access_token")),
                           open_id=session.get("open_id"))


@app.route("/start-upload", methods=["POST"])
def start_upload():
    """
    Save form input → if not logged in, go to OAuth. If logged in, upload now.
    """
    video_url = request.form.get("video_url", "").strip()
    caption = request.form.get("caption", "").strip()

    if not video_url:
        return render_template("result.html", ok=False, message="Please provide a public .mp4 URL.")

    # Save the pending job in session (super simple for demo)
    session["pending_upload"] = {"video_url": video_url, "caption": caption}

    if not session.get("access_token") or not session.get("open_id"):
        return redirect(url_for("login"))

    return redirect(url_for("do_upload"))


@app.route("/login")
def login():
    """
    Kick off TikTok OAuth with scopes required to publish.
    """
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": "user.info.basic,video.upload,video.publish",
        "redirect_uri": REDIRECT_URI,
        "state": "state123",  # put a real CSRF token in production
    }
    return redirect(f"{AUTH_URL}/?{urlencode(params)}")


@app.route("/callback")
def callback():
    """
    TikTok redirects here. Exchange code for token.
    If a pending upload exists, finish it automatically.
    """
    if request.args.get("error"):
        return render_template("result.html", ok=False,
                               message=f"TikTok error: {request.args.get('error')}")

    code = request.args.get("code")
    if not code:
        return render_template("result.html", ok=False, message="Missing ?code from TikTok.")

    # Exchange code → access token
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }
    token_res = requests.post(TOKEN_URL, data=data, timeout=30)

    if token_res.status_code != 200:
        return render_template("result.html", ok=False,
                               message=f"Token exchange failed: {token_res.text}")

    token_json = token_res.json()
    access_token = token_json.get("access_token")
    open_id = token_json.get("open_id")

    if not access_token or not open_id:
        return render_template("result.html", ok=False,
                               message=f"Token response missing fields: {token_json}")

    # Save to session
    session["access_token"] = access_token
    session["open_id"] = open_id

    # If a form was submitted before auth, finish the upload
    if session.get("pending_upload"):
        return redirect(url_for("do_upload"))

    # Otherwise just return to home
    return redirect(url_for("index"))


@app.route("/do-upload")
def do_upload():
    """
    Create a container with video_url + caption, then publish it.
    Requires the domain of video_url to be VERIFIED in TikTok portal (for pull_by_url).
    """
    access_token = session.get("access_token")
    open_id = session.get("open_id")
    pending = session.get("pending_upload")

    if not access_token or not open_id:
        return redirect(url_for("login"))

    if not pending:
        return render_template("result.html", ok=False, message="No video to upload. Please submit the form.")

    video_url = pending["video_url"]
    caption = pending.get("caption") or ""

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    # 1) Create container
    container_payload = {
        "video_url": video_url,   # must be public & on a verified domain
        "caption": caption[:2200] # TikTok caption limit
    }
    c_res = requests.post(CONTAINER_URL, json=container_payload, headers=headers, timeout=60)
    if c_res.status_code != 200:
        return render_template("result.html", ok=False,
                               message=f"Container error: {c_res.text}")

    c_json = c_res.json()
    container_id = (c_json.get("data") or {}).get("container_id")
    if not container_id:
        return render_template("result.html", ok=False,
                               message=f"No container_id in response: {c_json}")

    # 2) Publish
    publish_payload = {
        "open_id": open_id,
        "container_id": container_id
    }
    p_res = requests.post(PUBLISH_URL, json=publish_payload, headers=headers, timeout=60)
    # Clear pending so a refresh doesn't repost
    session.pop("pending_upload", None)

    if p_res.status_code != 200:
        return render_template("result.html", ok=False,
                               message=f"Publish error: {p_res.text}")

    return render_template("result.html", ok=True, message=p_res.json())


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/health")
def health():
    return "ok", 200
