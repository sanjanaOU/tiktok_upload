import os
from flask import Flask, redirect, request, session
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

@app.route("/")
def home():
    return redirect("/login")

@app.route("/login")
def login():
    params = {
        "client_key": TIKTOK_CLIENT_KEY,
        "response_type": "code",
        "scope": "user.info.basic,video.upload",
        "redirect_uri": REDIRECT_URI,
        "state": "secure_random_state",
    }

    return redirect(f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}")

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Error: No code returned by TikTok"

    token_url = "https://open.tiktokapis.com/v2/oauth/token/"

    payload = {
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(token_url, data=payload, headers=headers)

    if response.status_code == 200:
        session["access_token"] = response.json().get("access_token")
        return "✅ TikTok access token received!"
    else:
        return f"❌ Token request failed:\n{response.text}"

if __name__ == "__main__":
    app.run(debug=True)
