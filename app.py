from flask import Flask, redirect, request, session, url_for
import requests
from urllib.parse import urlencode
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# TikTok App Credentials (from Developer Portal)
CLIENT_KEY = "sbawemm7fb4n0ps8iz"
CLIENT_SECRET = "uF1lxNnTU20eDtoqojsfQe75HA5Jvn4g"

# Set this to match TikTok Developer Console exactly
REDIRECT_URI = "https://tiktok-upload.onrender.com/callback"

# Step 1: OAuth Login URL
@app.route('/')
def login():
    auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": "user.info.basic,video.upload,video.publish",
        "redirect_uri": REDIRECT_URI,
        "state": "secure_random_state"
    })
    return redirect(auth_url)

# Step 2: TikTok redirects to this after login
@app.route('/callback')
def callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"Error from TikTok: {error}"

    if not code:
        return "No code received from TikTok."

    # Exchange code for access token
    token_url = "https://open.tiktokapis.com/v2/oauth/token"
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(token_url, data=data)
    if response.status_code == 200:
        token_info = response.json()
        access_token = token_info.get("access_token")
        open_id = token_info.get("open_id")
        session["access_token"] = access_token
        session["open_id"] = open_id
        return f"✅ Access Token: {access_token}<br>Open ID: {open_id}"
    else:
        return f"❌ Failed to get access token: {response.text}"

# Optional: Revoke session or test
@app.route('/logout')
def logout():
    session.clear()
    return "Logged out."

if __name__ == '__main__':
    app.run(debug=True)
