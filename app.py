from flask import Flask, request, render_template, redirect, session, jsonify, url_for
import os
import requests
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "supersecretkey"

# TikTok API endpoints
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/initialize/"
PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- HELPER ---
def _ensure_access_token():
    token = session.get("access_token")
    if not token:
        return None
    return token

# --- ROUTES ---
@app.route("/")
def home():
    if not _ensure_access_token():
        return render_template("login.html")
    return render_template("upload.html")

@app.route("/set-token")
def set_token():
    token = request.args.get("access_token")
    if not token:
        return "Missing access_token", 400
    session["access_token"] = token
    return redirect(url_for("home"))

@app.route("/upload", methods=["POST"])
def upload():
    access_token = _ensure_access_token()
    if not access_token:
        return jsonify({"error": "Not authorized"}), 401

    # Get form data
    video = request.files.get("video")
    caption = request.form.get("caption", "")
    privacy = request.form.get("privacy", "SELF_ONLY")
    cover_ts = int(request.form.get("cover_ts", 0))

    if not video:
        return jsonify({"error": "No video file provided"}), 400

    # Save file locally
    filename = secure_filename(video.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    video.save(filepath)
    file_size = os.path.getsize(filepath)

    # Step 1: INIT
    init_payload = {
        "post_info": {
            "title": caption,
            "privacy_level": privacy
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "upload_param": {
                "video_size": file_size
            }
        }
    }
    init_res = requests.post(
        INIT_URL,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=init_payload
    )
    if init_res.status_code != 200:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_res.text}), 400

    init_json = init_res.json()
    if "data" not in init_json or "publish_id" not in init_json["data"]:
        return jsonify({"error": "TikTok init failed", "response": init_json}), 400

    publish_id = init_json["data"]["publish_id"]
    upload_url = init_json["data"]["upload_url"]
    session["last_publish_id"] = publish_id

    # Step 2: UPLOAD
    with open(filepath, "rb") as f:
        put_res = requests.put(upload_url, data=f, headers={"Content-Type": "video/mp4"})
    if put_res.status_code not in (200, 201):
        return jsonify({"step": "upload", "status": put_res.status_code, "response": put_res.text}), 400

    # Step 3: PUBLISH
    publish_payload = {"publish_id": publish_id, "cover_tsp": cover_ts}
    pub_res = requests.post(
        PUBLISH_URL,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=publish_payload
    )
    if pub_res.status_code != 200:
        return jsonify({"step": "publish", "status": pub_res.status_code, "response": pub_res.text}), 400

    return jsonify({
        "ok": True,
        "publish_id": publish_id,
        "status_link": url_for("status_last", _external=True),
        "publish_response": pub_res.json()
    })

@app.route("/status-last")
def status_last():
    access_token = _ensure_access_token()
    if not access_token:
        return jsonify({"error": "Not authorized"}), 401
    pid = session.get("last_publish_id")
    if not pid:
        return jsonify({"error": "No publish_id stored"}), 400

    r = requests.post(
        STATUS_URL,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": pid}
    )
    return jsonify({"publish_id": pid, "status": r.json()}), r.status_code


if __name__ == "__main__":
    app.run(debug=True)
