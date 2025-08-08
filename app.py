# app.py
import os
import json
import time
import secrets
from urllib.parse import urlencode

import requests
from flask import Flask, redirect, request, session, jsonify, render_template_string

app = Flask(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# REQUIRED ENV (Render → Environment or .env for local):
#   TIKTOK_CLIENT_KEY=sbawemm7fb4n0ps8iz
#   TIKTOK_CLIENT_SECRET=uF1lxNnTU20eDtoqojsfQe75HA5Jvn4g
#   REDIRECT_URI=https://tiktok-upload.onrender.com/callback
#   FLASK_SECRET_KEY=<long random string>
# ──────────────────────────────────────────────────────────────────────────────

app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()

# MUST be byte-for-byte identical here, in /login, and in the TikTok dev portal
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()

# TikTok endpoints
AUTH_URL  = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

# Content Posting API
CONTAINER_CREATE_URL = "https://open.tiktokapis.com/v2/post/publish/container/"
CONTAINER_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/container/status/"
PUBLISH_URL          = "https://open.tiktokapis.com/v2/post/publish/"

SCOPES = "user.info.basic,video.upload,video.publish"


# ───────────────────────── helpers ─────────────────────────
def new_state() -> str:
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s


def bearer_headers() -> dict:
    return {
        "Authorization": f"Bearer {session['access_token']}",
        "Content-Type": "application/json",
    }


# ───────────────────────── routes ─────────────────────────
@app.route("/")
def index():
    # Simple landing page
    return """
    <h2>TikTok OAuth & Upload Demo</h2>
    <p><a href="/login">Login with TikTok</a></p>
    <p><a href="/debug-auth">/debug-auth</a> – shows values used for OAuth</p>
    <p><a href="/upload">/upload</a> – upload a public .mp4 URL & publish</p>
    """


@app.route("/debug-auth")
def debug_auth():
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": session.get("oauth_state") or "(none yet)",
    }
    return jsonify(
        {
            "client_key": CLIENT_KEY,
            "redirect_uri_from_env": REDIRECT_URI,
            "scopes": SCOPES,
            "session_state": session.get("oauth_state"),
            "authorize_url": AUTH_URL + "?" + urlencode(params),
            "has_access_token": "access_token" in session,
            "open_id": session.get("open_id"),
        }
    )


@app.route("/login")
def login():
    # Always generate a fresh state
    state = new_state()
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,   # EXACT same value everywhere
        "state": state,
    }
    return redirect(AUTH_URL + "?" + urlencode(params), code=302)


@app.route("/callback")
def callback():
    # TikTok returns ?code and ?state
    err = request.args.get("error")
    if err:
        return f"❌ TikTok error: {err}", 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "❌ Missing ?code from TikTok.", 400

    # State protection (recommended)
    saved_state = session.get("oauth_state")
    if not saved_state or saved_state != state:
        return "❌ State mismatch. Start login again.", 400

    # Exchange auth code → access token
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    body = urlencode(
        {
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,  # MUST match
        }
    )
    r = requests.post(TOKEN_URL, headers=headers, data=body, timeout=30)

    try:
        token_json = r.json()
    except Exception:
        token_json = {"raw": r.text}

    if r.status_code != 200 or "access_token" not in token_json:
        return (
            "❌ Token response missing access_token:<br><pre>"
            + json.dumps(token_json, indent=2)
            + "</pre>",
            400,
        )

    # Save in session
    session["access_token"] = token_json["access_token"]
    session["open_id"] = token_json.get("open_id")

    return (
        f"<h3>✅ Logged in</h3>"
        f"<p>open_id: <code>{session.get('open_id')}</code></p>"
        f"<p><a href='/upload'>Go to Upload</a></p>"
        f"<pre>{json.dumps(token_json, indent=2)}</pre>"
    )


# ───────────── Upload UI ─────────────
UPLOAD_FORM_HTML = """
<h3>Upload & Publish to TikTok</h3>
{% if not logged_in %}
  <p>You are not logged in. <a href="/login">Login</a></p>
{% else %}
  <p>Logged in as open_id: <code>{{ open_id }}</code></p>
  <form method="post" action="/upload">
    <label>Public .mp4 URL</label><br/>
    <input name="video_url" size="80" required placeholder="https://example.com/video.mp4"/><br/><br/>
    <label>Caption</label><br/>
    <input name="caption" size="80" value="Uploaded via API"/><br/><br/>
    <button type="submit">Create Container + Publish</button>
  </form>
{% endif %}
"""

@app.route("/upload", methods=["GET"])
def upload_form():
    return render_template_string(
        UPLOAD_FORM_HTML,
        logged_in=("access_token" in session),
        open_id=session.get("open_id"),
    )


# ───────────── Upload + Publish ─────────────
@app.route("/upload", methods=["POST"])
def upload_post():
    if "access_token" not in session or "open_id" not in session:
        return redirect("/login")

    video_url = (request.form.get("video_url") or "").strip()
    caption   = (request.form.get("caption") or "Uploaded via API").strip()
    if not video_url:
        return "Need video_url", 400

    # 1) Create container (pull-by-url)
    r = requests.post(
        CONTAINER_CREATE_URL,
        headers=bearer_headers(),
        data=json.dumps({"video_url": video_url, "caption": caption}),
        timeout=60,
    )
    try:
        cjson = r.json()
    except Exception:
        cjson = {"raw": r.text}

    if r.status_code != 200 or "data" not in cjson or "container_id" not in cjson["data"]:
        return f"<h3>Container error</h3><pre>{json.dumps(cjson, indent=2)}</pre>", 400

    container_id = cjson["data"]["container_id"]

    # 2) (Optional) Poll until container READY (or ERROR)
    status = None
    for _ in range(12):  # up to ~24s
        s = requests.get(
            CONTAINER_STATUS_URL,
            headers=bearer_headers(),
            params={"container_id": container_id},
            timeout=30,
        )
        sj = s.json()
        status = (sj.get("data") or {}).get("status")
        if status in ("READY", "ERROR", "FAILED"):
            break
        time.sleep(2)

    # 3) Publish
    pr = requests.post(
        PUBLISH_URL,
        headers=bearer_headers(),
        data=json.dumps({"open_id": session["open_id"], "container_id": container_id}),
        timeout=60,
    )
    try:
        pjson = pr.json()
    except Exception:
        pjson = {"raw": pr.text}

    if pr.status_code != 200:
        return f"<h3>Publish error</h3><pre>{json.dumps(pjson, indent=2)}</pre>", 400

    return (
        "<h3>✅ Publish requested</h3>"
        f"<p>Container status before publish: <b>{status}</b></p>"
        f"<p>open_id: <code>{session['open_id']}</code></p>"
        f"<p>container_id: <code>{container_id}</code></p>"
        f"<pre>{json.dumps(pjson, indent=2)}</pre>"
    )


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=True)
