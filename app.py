from flask import Flask, request, redirect, session, render_template, jsonify
import requests
import os

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Load TikTok credentials from environment
CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

# TikTok domain verification route
@app.route("/tiktokdvfNYX2K11qZunpO3MBI04o1RWhOo5Xq.txt")
def verify_file():
    return "tiktok-developers-site-verification=dvfNYX2K11qZunpO3MBI04o1RWhOo5Xq", 200, {'Content-Type': 'text/plain'}

# Home page
@app.route('/')
def index():
    return render_template('index.html')

# Redirect to TikTok login
@app.route('/login')
def login():
    return redirect(
        f"https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={CLIENT_KEY}"
        f"&scope=user.info.basic,video.list,video.upload"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state=your_custom_state"
    )

# Callback route for TikTok OAuth
@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return 'Authorization failed', 400

    token_url = 'https://open.tiktokapis.com/v2/oauth/token/'
    data = {
        'client_key': CLIENT_KEY,
        'client_secret': CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': REDIRECT_URI
    }

    response = requests.post(token_url, data=data)
    if response.status_code != 200:
        return 'Failed to get access token', 400

    token_data = response.json()
    session['access_token'] = token_data['access_token']
    session['open_id'] = token_data['open_id']

    return redirect('/upload')

# Upload a video by URL
@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        access_token = session.get('access_token')
        open_id = session.get('open_id')
        video_url = request.form.get('video_url')
        caption = request.form.get('caption', 'Uploaded via Flask')

        # Step 1: Create media container
        container_url = "https://open.tiktokapis.com/v2/post/publish/container/"
        container_payload = {
            "video_url": video_url,
            "caption": caption
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        container_response = requests.post(container_url, json=container_payload, headers=headers)
        if container_response.status_code != 200:
            return jsonify(container_response.json()), 400

        container_id = container_response.json().get('data', {}).get('container_id')

        # Step 2: Publish video
        publish_url = "https://open.tiktokapis.com/v2/post/publish/"
        publish_payload = {
            "open_id": open_id,
            "container_id": container_id
        }

        publish_response = requests.post(publish_url, json=publish_payload, headers=headers)
        return jsonify(publish_response.json())

    return render_template('upload.html')
