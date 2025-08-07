import os
import secrets
from urllib.parse import urlencode
from flask import Flask, redirect, request, session, jsonify

import requests

app = Flask(__name__)

# ====== ENV ======
# .env (or Render "Environment") MUST contain these, exactly:
# TIKTOK_CLIENT_KEY=sbawemm7fb4n0ps8iz           <-- your sandbox client key
# TIKTOK_CLIENT_SECRET=uF1lxNnTU20eDtoqojsfQe75HA5Jvn4g
# REDIRECT_URI=https://tiktok-upload.onrender.com/callback
# FLASK_SECRET_KEY=<any random string>

app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()

# *** This must be IDENTICAL everywhere (portal, authorize, token exchange) ***
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

SCOPES = "user.info.basic,video.upload,video.publish"


# ---------- helpers ----------
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s


# ---------- routes ----------
@app.route("/")
def index():
    return (
        "<h3>TikTok OAuth</h3>"
        '<p><a href="/login">Login with TikTok</a></p>'
        '<p><a href="/debug-auth">/debug-auth</a> (shows values the server is using)</p>'
    )


@app.route("/debug-auth")
def debug_auth():
    data = {
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": session.get("oauth_state"),
    }
    # Show the exact authorize URL we will send the user to
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
    # Always generate a fresh state to avoid reusing codes tied to older redirects
    state = new_state()

    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,   # <-- EXACT SAME STRING
        "state": state,
        # "force_verify": "1",  # optional: forces TikTok to re-prompt
    }
    url = AUTH_URL + "?" + urlencode(params)
    return redirect(url, code=302)


@app.route("/callback")
def callback():
    # TikTok returns ?code=...&state=...
    err = request.args.get("error")
    if err:
        return f"❌ TikTok error: {err}", 400

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return "❌ Missing ?code from TikTok.", 400

    # Optional: check state
    saved_state = session.get("oauth_state")
    if not saved_state or saved_state != state:
        return "❌ State mismatch. Start login again.", 400

    # --- Exchange authorization code for access token ---
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        # *** MUST MATCH *** the value used in /login and the app portal
        "redirect_uri": REDIRECT_URI,
    }

    # Use urlencoded body (not JSON)
    body = urlencode(payload)
    r = requests.post(TOKEN_URL, headers=headers, data=body, timeout=30)

    try:
        token_json = r.json()
    except Exception:
        token_json = {"raw": r.text}

    if r.status_code != 200 or "access_token" not in token_json:
        return (
            "❌ Token response missing access_token: "
            + jsonify(token_json).get_data(as_text=True),
            400,
        )

    # success
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
    # Local dev: python app.py
    app.run(host="0.0.0.0", port=5051, debug=True)
