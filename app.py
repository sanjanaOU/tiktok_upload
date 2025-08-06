from flask import Flask, redirect, request, session, jsonify, send_from_directory
import os
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# TikTok OAuth URLs
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
PROFILE_URL = "https://open.tiktokapis.com/v2/user/info/"

@app.route("/")
def index():
    auth_params = {
        "client_key": CLIENT_KEY,
        "scope": "user.info.basic",
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": "secure_random_state_123"
    }
    auth_link = f"{AUTH_URL}?{urlencode(auth_params)}"
    return f'<a href="{auth_link}">Login with TikTok</a>'

@app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return "Authorization failed. No code received."

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    payload = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(
        TOKEN_URL,
        headers=headers,
        data=urlencode(payload)
    )

    token_data = response.json()

    if "access_token" in token_data:
        session["access_token"] = token_data["access_token"]
        return f"""
            ✅ Access Token: {token_data['access_token']}<br><br>
            <a href="/profile">Get Profile Info</a>
        """
    else:
        return f"❌ Token Error: {token_data}"

@app.route("/profile")
def profile():
    access_token = session.get("access_token")
    if not access_token:
        return redirect("/")

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(PROFILE_URL, headers=headers)
    return jsonify(response.json())

# ✅ Serve TikTok Verification File
@app.route("/callback/<filename>")
def serve_verification_file(filename):
    return send_from_directory("callback", filename)

if __name__ == "__main__":
    app.run(debug=True, port=5051)
