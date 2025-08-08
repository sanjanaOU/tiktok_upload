import os, time, secrets
from urllib.parse import urlencode
from flask import Flask, request, session, jsonify, render_template_string, redirect
import requests

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))

# TikTok endpoints
DIRECT_POST_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH     = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# ---------- helpers ----------
def _save_tokens(access_token: str, refresh_token: str | None = None,
                 expires_in: int = 3600):
    """Store tokens in session; expire slightly early."""
    session["tk"] = {
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "expires_at": int(time.time()) + int(expires_in) - 60,
    }

def _ensure_access_token():
    """Return access token if present (no refresh in Option A)."""
    d = session.get("tk") or {}
    if not d:
        return None
    if time.time() >= d.get("expires_at", 0):
        # no refresh flow in option A—force re-seed via /set-token
        return None
    return d["access_token"]


# ---------- routes ----------
@app.route("/")
def home():
    has = bool(session.get("tk"))
    return (
        "<h3>TikTok Upload (Option A: use existing token)</h3>"
        f"<p>Token in session: <b>{'yes' if has else 'no'}</b></p>"
        '<p><a href="/set-token">/set-token</a> (seed your token once)</p>'
        '<p><a href="/form">/form</a> (upload UI)</p>'
        '<p><a href="/status?publish_id=...">/status</a> (poll status)</p>'
    )

@app.route("/set-token")
def set_token():
    """
    Seed session with an existing token so you can upload without re-auth.
    Usage (GET):
      /set-token?access_token=XXX[&open_id=YYY][&expires_in=3600][&refresh_token=ZZZ]
    """
    access_token = request.args.get("access_token")
    if not access_token:
        return (
            "Pass ?access_token=... (&open_id=... [&expires_in=3600] [&refresh_token=...])",
            400,
        )
    open_id = request.args.get("open_id", "")
    refresh_token = request.args.get("refresh_token", "")
    expires_in = int(request.args.get("expires_in", "3600"))

    _save_tokens(access_token, refresh_token or None, expires_in)
    session["open_id"] = open_id

    return (
        '✅ Token stored in session. '
        'Now go to <a href="/form">/form</a> to upload a .mp4.'
    )

@app.route("/form")
def form():
    html = """
    <h3>Post a TikTok video</h3>
    <p>Make sure you called <code>/set-token?access_token=...</code> first.</p>
    <form action="/upload" method="post" enctype="multipart/form-data">
      <div>Video (.mp4): <input type="file" name="file" accept="video/mp4" required></div>
      <div>Caption: <input type="text" name="title" placeholder="Your caption"></div>
      <div>Privacy:
        <select name="privacy">
          <option value="SELF_ONLY" selected>SELF_ONLY (recommended for sandbox)</option>
          <option value="PUBLIC_TO_EVERYONE">PUBLIC_TO_EVERYONE</option>
          <option value="MUTUAL_FOLLOW_FRIENDS">MUTUAL_FOLLOW_FRIENDS</option>
          <option value="FOLLOWER_OF_CREATOR">FOLLOWER_OF_CREATOR</option>
        </select>
      </div>
      <div>Cover timestamp (ms): <input type="number" name="cover_ms" value="0"></div>
      <button type="submit">Upload & Post</button>
    </form>
    """
    return render_template_string(html)

@app.route("/upload", methods=["POST"])
def upload():
    access_token = _ensure_access_token()
    if not access_token:
        return "Not authorized: seed your token via /set-token first (or token expired).", 401

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file is required (.mp4)"}), 400

    title    = request.form.get("title", "")
    privacy  = request.form.get("privacy", "SELF_ONLY")
    cover_ms = int(request.form.get("cover_ms", "0"))

    # 1) Initialize Direct Post (FILE_UPLOAD)
    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": cover_ms
        },
        "source_info": {"source": "FILE_UPLOAD"}
    }
    init_res = requests.post(
        DIRECT_POST_INIT,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=init_body,
        timeout=60,
    )
    if init_res.status_code >= 400:
        return jsonify({"step": "init", "status": init_res.status_code, "response": init_res.text}), init_res.status_code

    init = init_res.json()["data"]
    upload_url = init["upload_url"]
    publish_id = init["publish_id"]

    # 2) PUT raw bytes to upload_url
    put_res = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=f.read(),
        timeout=300,
    )
    if put_res.status_code >= 400:
        return jsonify({"step": "upload", "status": put_res.status_code, "response": put_res.text}), put_res.status_code

    # 3) Optional: check status once
    st = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=30,
    )

    return jsonify({"ok": True, "publish_id": publish_id, "status": st.json()})

@app.route("/status")
def status():
    access_token = _ensure_access_token()
    if not access_token:
        return jsonify({"error": "Not authorized"}), 401
    pid = request.args.get("publish_id")
    if not pid:
        return jsonify({"error": "publish_id required"}), 400
    r = requests.post(
        STATUS_FETCH,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": pid},
        timeout=30,
    )
    return jsonify(r.json()), r.status_code

@app.route("/logout")
def logout():
    session.clear()
    return "Session cleared."

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5051)), debug=True)
