import os
import secrets
import json
import time
from urllib.parse import urlencode
from flask import Flask, redirect, request, session, jsonify, render_template_string

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

# TikTok API endpoints for video upload (Inbox flow - videos go to user's inbox for manual posting)
CONTENT_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"

SCOPES = "user.info.basic,video.upload,video.publish"

# Maximum file size (TikTok limit is around 287MB)
MAX_FILE_SIZE = 287 * 1024 * 1024  # 287MB in bytes

# Upload form HTML template
UPLOAD_FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>TikTok Video Upload</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input, textarea, select { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
        textarea { height: 100px; resize: vertical; }
        button { background: #ff0050; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; }
        button:hover { background: #e6004a; }
        .error { color: red; margin-top: 10px; }
        .success { color: green; margin-top: 10px; }
    </style>
</head>
<body>
    <h2>Upload Video to TikTok</h2>
    
    {% if not session.get('access_token') %}
        <p>❌ You need to authenticate first: <a href="/login">Login with TikTok</a></p>
    {% else %}
        <p>✅ Authenticated as: {{ session.get('open_id', 'Unknown') }}</p>
        
        <form method="POST" enctype="multipart/form-data">
            <div class="form-group">
                <label for="video_file">Video File (.mp4):</label>
                <input type="file" name="video_file" accept=".mp4,video/mp4" required>
            </div>
            
            <div class="form-group">
                <label for="title">Title (Optional for inbox flow):</label>
                <input type="text" name="title" placeholder="Enter video title (will be added in TikTok app)">
            </div>
            
            <div class="form-group">
                <label for="description">Description (Optional for inbox flow):</label>
                <textarea name="description" placeholder="Enter video description (will be added in TikTok app)"></textarea>
            </div>
            
            <div class="form-group">
                <p><strong>Note:</strong> This uses TikTok's "inbox" upload flow. Your video will be uploaded to your TikTok inbox where you can manually add captions, hashtags, and privacy settings before posting through the TikTok app.</p>
            </div>
            
            <button type="submit">Upload Video</button>
        </form>
        
        <p><a href="/logout">Logout</a></p>
    {% endif %}
    
    <p><a href="/">← Back to Home</a></p>
</body>
</html>
"""


# ---------- helpers ----------
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s


def get_auth_headers():
    """Get authorization headers for API calls"""
    access_token = session.get("access_token")
    if not access_token:
        return None
    return {"Authorization": f"Bearer {access_token}"}


def upload_video_to_tiktok(video_file, title="", description="", privacy_level="PUBLIC_TO_EVERYONE", 
                          disable_duet=False, disable_comment=False, disable_stitch=False):
    """
    Upload a video to TikTok using the inbox flow (2-step process):
    1. Initialize upload - gets upload URL
    2. Upload video content to TikTok servers
    
    Note: With the inbox flow, videos are uploaded to the user's TikTok inbox
    where they can manually add captions and post them through the TikTok app.
    """
    headers = get_auth_headers()
    if not headers:
        return {"error": "Not authenticated"}
    
    try:
        # Get file size first
        video_file.seek(0, 2)  # Seek to end
        video_size = video_file.tell()
        video_file.seek(0)  # Reset to beginning
        
        # Step 1: Initialize upload (simplified for inbox flow)
        print("Step 1: Initializing video upload...")
        init_data = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,  # Single chunk upload
                "total_chunk_count": 1
            }
        }
        
        headers_init = get_auth_headers()
        headers_init["Content-Type"] = "application/json; charset=UTF-8"
        
        print(f"Init request URL: {CONTENT_INIT_URL}")
        print(f"Init request data: {json.dumps(init_data, indent=2)}")
        print(f"Video size: {video_size} bytes")
        
        init_response = requests.post(
            CONTENT_INIT_URL,
            headers=headers_init,
            data=json.dumps(init_data),
            timeout=30
        )
        
        print(f"Init response status: {init_response.status_code}")
        print(f"Init response: {init_response.text}")
        
        if init_response.status_code != 200:
            return {"error": f"Init failed (HTTP {init_response.status_code}): {init_response.text}"}
        
        try:
            init_result = init_response.json()
        except:
            return {"error": f"Init response not valid JSON: {init_response.text}"}
        
        # Check for errors in response
        error_info = init_result.get("error", {})
        if error_info.get("code") != "ok":
            return {"error": f"Init API error: {error_info}"}
        
        if "data" not in init_result:
            return {"error": f"Init response missing data: {init_result}"}
        
        data = init_result["data"]
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        
        if not publish_id or not upload_url:
            return {"error": f"Init response missing publish_id or upload_url: {init_result}"}
        
        print(f"✅ Init successful. Publish ID: {publish_id}")
        print(f"Upload URL: {upload_url}")
        
        # Step 2: Upload video content to TikTok servers
        print("Step 2: Uploading video content...")
        video_file.seek(0)  # Reset file pointer
        
        # Prepare upload headers (no Authorization needed for upload URL)
        upload_headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(video_size),
            "Content-Range": f"bytes 0-{video_size-1}/{video_size}"
        }
        
        print(f"Upload headers: {upload_headers}")
        
        upload_response = requests.put(
            upload_url,  # Use the full URL as provided by TikTok
            headers=upload_headers,
            data=video_file.read(),
            timeout=120
        )
        
        print(f"Upload response status: {upload_response.status_code}")
        print(f"Upload response: {upload_response.text}")
        
        if upload_response.status_code not in [200, 201, 202, 204]:
            return {"error": f"Upload failed (HTTP {upload_response.status_code}): {upload_response.text}"}
        
        print("✅ Upload successful!")
        
        return {
            "success": True,
            "publish_id": publish_id,
            "message": "Video uploaded successfully to your TikTok inbox! Check your TikTok app notifications to add captions and post it.",
            "note": "This uses TikTok's inbox flow - the video will appear in your TikTok inbox where you can manually add captions and publish it."
        }
        
    except Exception as e:
        print(f"Exception during upload: {str(e)}")
        return {"error": f"Exception during upload: {str(e)}"}


# ---------- routes ----------
@app.route("/")
def index():
    return (
        "<h3>TikTok OAuth + Video Upload</h3>"
        '<p><a href="/login">Login with TikTok</a></p>'
        '<p><a href="/upload">Upload Video</a></p>'
        '<p><a href="/debug-auth">/debug-auth</a> (shows values the server is using)</p>'
    )


@app.route("/debug-auth")
def debug_auth():
    data = {
        "client_key": CLIENT_KEY,
        "redirect_uri_from_env": REDIRECT_URI,
        "scopes": SCOPES,
        "session_state": session.get("oauth_state"),
        "authenticated": bool(session.get("access_token")),
        "open_id": session.get("open_id"),
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
    return redirect("/upload")


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template_string(UPLOAD_FORM_HTML, session=session)
    
    # POST request - handle file upload
    if not session.get("access_token"):
        return "❌ Not authenticated. Please login first.", 401
    
    # Get form data
    video_file = request.files.get("video_file")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    
    # Note: Privacy settings and other options are not used in inbox flow
    # Users will set these manually in the TikTok app
    
    # Validation
    if not video_file or not video_file.filename:
        return "❌ No video file selected", 400
    
    if not video_file.filename.lower().endswith('.mp4'):
        return "❌ Only MP4 files are supported", 400
    
    # Check file size (TikTok has limits)
    video_file.seek(0, 2)  # Seek to end
    file_size = video_file.tell()
    video_file.seek(0)  # Reset
    
    # TikTok file size limit
    if file_size > MAX_FILE_SIZE:
        return f"❌ File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB", 400
    
    print(f"Starting upload: {video_file.filename} ({file_size} bytes)")
    
    # Upload video
    result = upload_video_to_tiktok(
        video_file=video_file,
        title=title,
        description=description
    )
    
    if result.get("error"):
        return f"❌ Upload failed: {result['error']}", 400
    
    return f"""
    ✅ Video uploaded successfully to your TikTok inbox!<br>
    Publish ID: {result.get('publish_id', 'N/A')}<br>
    <br>
    <strong>Next steps:</strong><br>
    1. Open your TikTok app<br>
    2. Check your notifications/inbox<br>
    3. Find your uploaded video<br>
    4. Add captions, hashtags, and privacy settings<br>
    5. Post the video<br>
    <br>
    <p><em>{result.get('note', '')}</em></p>
    <br>
    <a href="/upload">Upload Another Video</a> | <a href="/">Home</a>
    """


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    # Local dev: python app.py
    app.run(host="0.0.0.0", port=5051, debug=True)