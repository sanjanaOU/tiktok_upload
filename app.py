import os
import secrets
from urllib.parse import urlencode, quote
from flask import Flask, redirect, request, session, jsonify
import requests

app = Flask(__name__)

# ====== ENV ======
# Make sure your .env or Render env variables contain:
# TIKTOK_CLIENT_KEY=your_sandbox_client_key
# TIKTOK_CLIENT_SECRET=your_sandbox_client_secret
# REDIRECT_URI=https://yourdomain.com/callback
# FLASK_SECRET_KEY=randomstring

app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

# Space-separated scopes (TikTok prefers this format)
SCOPES = "user.info.basic video.upload video.publish"

# ---------- helpers ----------
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def build_auth_url(state: str) -> str:
    base = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "force_verify": "1",  # forces TikTok to re-show the consent screen
    }
    # Append scope separately so spaces become %20 not +
    return f"{AUTH_URL}?{urlencode(base)}&scope={quote(SCOPES, safe='')}"

# ---------- routes ----------
@app.route("/")
def index():
    return (
        "<h3>TikTok OAuth</h3>"
        '<p><a href="/login">Login with TikTok</a></p>'
        '<p><a href="/debug-auth">/debug-auth</a> (shows auth URL and state)</p>'
    )

@app.route("/debug-auth")
def debug_auth():
    st = session.get("oauth_state") or "(none yet)"
    return jsonify({
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": st,
        "authorize_url": build_auth_url(st),
    })

@app.route("/login")
def login():
    state = new_state()
    return redirect(build_auth_url(state), code=302)

@app.route("/callback")
def callback():
    err = request.args.get("error")
    if err:
        return f"❌ TikTok error: {err}", 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "❌ Missing ?code from TikTok.", 400

    saved_state = session.get("oauth_state")
    if not saved_state or saved_state != state:
        return "❌ State mismatch. Start login again.", 400

    # Exchange authorization code for access token
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
        return (
            "❌ Token response missing access_token: "
            + str(token_json),
            400,
        )

    # Save access token
    session["access_token"] = token_json["access_token"]
    session["open_id"] = token_json.get("open_id")

    return (
        f"✅ Got token for open_id={session.get('open_id')}<br>"
        f"<pre>{token_json}</pre>"
    )

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=True)
