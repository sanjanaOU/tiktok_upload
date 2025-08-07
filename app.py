from flask import Flask, redirect, request, session, jsonify
import os
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

SCOPES = [
    "user.info.basic",
    "video.list",
    "video.upload",
    "video.publish"
]

@app.route("/")
def home():
    auth_params = {
        "client_key": CLIENT_KEY,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": "myappstate123"
    }
    auth_link = f"{AUTH_URL}?{urlencode(auth_params)}"
    return f"<h2>Login with TikTok</h2><a href='{auth_link}'>Click here to authenticate</a>"

@app.route("/callback")
def callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"<h3>❌ TikTok Login Error:</h3><pre>{error}</pre>"

    if not code:
        return "Missing authorization code", 400

    payload = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(TOKEN_URL, json=payload)
    
    if response.status_code == 200:
        data = response.json()
        return f"<h3>✅ Access Token Received:</h3><pre>{data}</pre>"
    else:
        return f"<h3>❌ Failed to get access token:</h3><pre>{response.text}</pre>"

if __name__ == "__main__":
    app.run(debug=True)
