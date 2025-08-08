import os, time, secrets
from flask import Flask, request, session, jsonify, render_template_string, url_for
import requests

# -------------------- CONFIG --------------------
# Paste your sandbox access token here OR set ACCESS_TOKEN env var.
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "").strip() or "PASTE_YOUR_TIKTOK_ACCESS_TOKEN"

# (Optional) OPEN_ID is NOT required for Direct Post, but you can save it if you want
OPEN_ID = os.getenv("OPEN_ID", "").strip()

# TikTok Direct Post endpoints (correct ones)
DIRECT_POST_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH     = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
# ------------------------------------------------

app = Flask(__name__)
app.secret_key = "dev_" + secrets.token_hex(16)

def _access_token():
    tok = session.get("access_token") or ACCESS_TOKEN
    return tok if tok else None

HOME_HTML = """
<h3>TikTok Uploader (skip auth)</h3>
<p>Using a hardcoded/token-in-session instead of OAuth.</p>
<p>
  Token present: <b>{{ 'yes' if token_present else 'no' }}</b>
  {% if not token_present %}
    <br>Set ACCESS_TOKEN in code or env (ACCESS_TOKEN) and restart.
  {% endif %}
</p>
<ul>
  <li><a href="/form">Upload form</a></li>
  <li><a href="/status-last" target="_blank">Check last upload status</a></li>
</ul>
"""

FORM_HTML = """
<h3>Post a TikTok video</h3>
<form action="/upload" method="post" enctype="multipart/form-data">
  <div>Video (.mp4): <input type="file" name="file" accept="video/mp4" required></div>
  <div>Caption: <input type="text" name="title" placeholder="Your caption"></div>
  <div>Privacy:
    <select name="privacy">
      <option value="SELF_ONLY" selected>SELF_ONLY (sandbox)</option>
      <option value="PUBLIC_TO_EVERYONE">PUBLIC_TO_EVERYONE</option>
      <option value="MUTUAL_FOLLOW_FRIENDS">MUTUAL_FOLLOW_FRIENDS</option>
      <option value="FOLLOWER_OF_CREATOR">FOLLOWER_OF_CREATOR</option>
    </select>
  </div>
  <div>Cover timestamp (ms): <input type="number" name="cover_ms" value="0"></div>
  <button type="submit">Upload & Post</button>
</form>
<p>After upload, use <a href="/status-last" target="_blank">/status-last</a> to poll status.</p>
"""

@app.route("/")
def home():
    return render_template_string(HOME_HTML, token_present=bool(_access_token()))

@app.route("/form")
def form():
    if not _access_token():
        return "No ACCESS_TOKEN set. Edit app.py or set ACCESS_TOKEN env var.", 400
    return render_template_string(FORM_HTML)

@app.route("/upload", methods=["POST"])
def upload():
    token = _access_token()
    if not token:
        return "No ACCESS_TOKEN set. Edit app.py or set ACCESS_TOKEN env var.", 401

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file is required (.mp4)"}), 400

    # Read once to get size and reuse for PUT
    video_bytes = f.read()
    if not video_bytes:
        return jsonify({"error": "uploaded file is empty"}), 400
    video_size = len(video_bytes)

    title    = request.form.get("title", "")
    privacy  = request.form.get("privacy", "SELF_ONLY")
    cover_ms = int(request.form.get("cover_ms", "0"))

    # 1) INIT (must include upload_param.video_size)
    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": cover_ms
        },
        "source_info": {"source": "FILE_UPLOAD"},
        "upload_param": {"video_size": video_size}
    }
    init_res = requests.post(
        DIRECT_POST_INIT,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        json=init_body,
        timeout=60,
    )
    if init_res.status_code >= 400:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_res.text}), init_res.status_code

    init_json = init_res.json()
    if "data" not in init_json or "publish_id" not in init_json["data"]:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_json}), 400

    data = init_json["data"]
    upload_url = data["upload_url"]
    publish_id = data["publish_id"]
    session["last_publish_id"] = publish_id

    # 2) PUT the bytes to upload_url
    put_res = requests.put(upload_url, headers={"Content-Type": "video/mp4"}, data=video_bytes, timeout=300)
    if put_res.status_code >= 400:
        return jsonify({"step": "upload", "status": put_res.status_code, "response": put_res.text}), put_res.status_code

    # 3) First status check
    st = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=30,
    )

    return jsonify({
        "ok": True,
        "publish_id": publish_id,
        "status_first_call": st.json(),
        "status_link": url_for("status_last", _external=True),
    })

@app.route("/status-last")
def status_last():
    token = _access_token()
    if not token:
        return jsonify({"error": "No ACCESS_TOKEN set"}), 401
    pid = session.get("last_publish_id")
    if not pid:
        return jsonify({"error": "No publish_id stored in this session yet"}), 400

    r = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"publish_id": pid},
        timeout=30,
    )
    return jsonify({"publish_id": pid, "status": r.json()}), r.status_code

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    # Set PORT for Render etc., defaults to 5051 locally
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5051)), debug=True)
