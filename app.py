from flask import Flask, redirect, request, session, jsonify
import os
import requests
from urllib.parse import urlencode

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")

# === TikTok OAuth config ===
CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")

# You said you want to use this one:
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://tiktok-upload.onrender.com/callback")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

# Scopes you enabled (Login Kit + Content Posting)
SCOPES = "user.info.basic,video.upload,video.publish"


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/")
def index():
    return (
        "<h3>TikTok OAuth Demo</h3>"
        "<p><a href='/login'>Login with TikTok</a></p>"
        "<p><a href='/debug-auth'>Debug authorize URL</a></p>"
    )


@app.route("/login")
def login():
    # Build the authorize URL
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,   # must match portal exactly
        "state": "state123",
    }
    return redirect(f"{AUTH_URL}?{urlencode(params)}")


@app.route("/debug-auth")
def debug_auth():
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": "state123",
    }
    url = f"{AUTH_URL}?{urlencode(params)}"
    html = [
        "<h3>Authorize URL (copy this entire line)</h3>",
        f"<pre>{url}</pre>",
        "<p>Now click <a href='/login'>/login</a> to use the same URL.</p>",
        "<p><b>IMPORTANT:</b> This redirect_uri must be byte-for-byte identical to the one in your TikTok portal:</p>",
        f"<pre>{REDIRECT_URI}</pre>",
    ]
    return "\n".join(html)


@app.route("/callback")
def callback():
    # TikTok sends you back here
    err = request.args.get("error")
    if err:
        return f"Error from TikTok: {err}", 400

    code = request.args.get("code")
    if not code:
        return "Authorization failed: no 'code' in query.", 400

    # Exchange code for access token
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,  # must match exactly
    }
    r = requests.post(TOKEN_URL, headers=headers, data=data)
    if r.status_code != 200:
        return f"❌ Failed to get access token: {r.text}", 400

    tok = r.json()
    access_token = tok.get("access_token")
    open_id = tok.get("open_id")

    if not access_token:
        return f"❌ Token response missing access_token: {tok}", 400

    session["access_token"] = access_token
    session["open_id"] = open_id

    # Simple success page
    masked = access_token[:6] + "..." + access_token[-4:]
    return (
        f"<p>✅ Access token acquired.</p>"
        f"<p>Open ID: {open_id}</p>"
        f"<p>Access token (masked): {masked}</p>"
        f"<p><a href='/'>Home</a></p>"
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
