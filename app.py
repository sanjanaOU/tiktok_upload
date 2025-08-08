import os
import secrets
import tempfile
from urllib.parse import urlencode

import requests
from flask import Flask, request, session, redirect, jsonify, make_response

# ========= ENV =========
CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()
SCOPES = "user.info.basic,video.upload,video.publish"

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
CONTAINER_URL = "https://open.tiktokapis.com/v2/post/publish/container/"
PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))


# ========= helpers =========
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s

def json_or_text(resp):
    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        try:
            return resp.status_code, resp.json(), True
        except Exception:
            pass
    return resp.status_code, {"non_json_body": resp.text}, False

def authed():
    return bool(session.get("access_token") and session.get("open_id"))

# ========= oauth =========
@app.route("/")
def index():
    if authed():
        return make_response(
            f"""
            <h3>TikTok OAuth ✔</h3>
            <p>open_id: <code>{session.get('open_id')}</code></p>
            <p><a href="/upload-url">Post a video from ANY public URL (server will re-upload)</a></p>
            <p><a href="/logout">Logout</a></p>
            <p><a href="/debug-auth">/debug-auth</a></p>
            """, 200
        )
    return make_response(
        """
        <h3>TikTok OAuth</h3>
        <p><a href="/login">Login with TikTok</a></p>
        <p><a href="/debug-auth">/debug-auth</a></p>
        """, 200
    )

@app.route("/login")
def login():
    state = new_state()
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return redirect(AUTH_URL + "?" + urlencode(params), code=302)

@app.route("/callback")
def callback():
    err = request.args.get("error")
    if err:
        return f"❌ TikTok error: {err}", 400
    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "❌ Missing ?code", 400
    if session.get("oauth_state") != state:
        return "❌ State mismatch. Start again.", 400

    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=urlencode({
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        }),
        timeout=30
    )
    status, body, _ = json_or_text(r)
    if status != 200 or "access_token" not in body:
        return make_response(f"❌ Token exchange failed ({status}):<pre>{body}</pre>", 400)

    session["access_token"] = body["access_token"]
    session["open_id"] = body.get("open_id")
    return redirect("/upload-url")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/debug-auth")
def debug_auth():
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": session.get("oauth_state") or "(none yet)",
    }
    return jsonify({
        "client_key": CLIENT_KEY[:4] + "***",
        "redirect_uri_from_env": REDIRECT_URI,
        "authorize_url": AUTH_URL + "?" + urlencode(params),
        "has_token": bool(session.get("access_token")),
        "open_id": session.get("open_id"),
    })

# ========= UI: give PUBLIC URL; backend re-uploads =========
@app.route("/upload-url", methods=["GET"])
def upload_url_page():
    if not authed():
        return redirect("/login")
    return make_response(
        """
        <h2>Post from ANY public URL (domain NOT required to be verified)</h2>
        <p>Server will download the file first, then upload to TikTok via UPLOAD_FROM_FILE.</p>
        <form id="f" onsubmit="return false">
          <label>Public MP4 URL</label><br>
          <input name="video_url" style="width:600px" placeholder="https://example.com/video.mp4" />
          <br><br>
          <label>Caption</label><br>
          <input name="caption" style="width:600px" value="Hello from API" />
          <br><br>
          <button onclick="go()">Publish</button>
        </form>
        <pre id="out" style="white-space:pre-wrap;background:#111;color:#0f0;padding:10px;"></pre>
        <script>
        async function go(){
          const fd = new FormData(document.getElementById('f'));
          const res = await fetch('/publish-from-public-url', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({
              video_url: fd.get('video_url'),
              caption: fd.get('caption')
            })
          });
          const text = await res.text();
          try { document.getElementById('out').textContent = JSON.stringify(JSON.parse(text), null, 2); }
          catch(e){ document.getElementById('out').textContent = text; }
        }
        </script>
        """, 200
    )

@app.route("/publish-from-public-url", methods=["POST"])
def publish_from_public_url():
    if not authed():
        return jsonify({"error":"not_authenticated"}), 401

    data = request.get_json(silent=True) or {}
    video_url = (data.get("video_url") or "").strip()
    caption = (data.get("caption") or "").strip()
    if not video_url:
        return jsonify({"error":"video_url is required"}), 400

    # 1) Download the remote file to a temp path
    try:
        with requests.get(video_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            # quick validation – you can relax this if needed
            ct = r.headers.get("content-type", "")
            if "mp4" not in ct and not video_url.lower().endswith(".mp4"):
                return jsonify({"error":"file must be mp4 (content-type or .mp4)"}), 400

            # Size guard (e.g., 1 GB)
            total = int(r.headers.get("content-length", "0") or "0")
            if total and total > 1_000_000_000:
                return jsonify({"error":"file too large (>1GB)"}), 400

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        tmp.write(chunk)
                tmp_path = tmp.name
    except Exception as e:
        return jsonify({"error":"download_failed", "detail": str(e)}), 400

    access_token = session["access_token"]
    open_id = session["open_id"]

    # 2) Create container with UPLOAD_FROM_FILE
    try:
        create_payload = {
            "source_info": { "source": "UPLOAD_FROM_FILE" },
            "text": caption
        }
        r1 = requests.post(
            CONTAINER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=create_payload,
            timeout=60
        )
        s1, b1, _ = json_or_text(r1)
        if s1 != 200 or "data" not in b1 or "container_id" not in b1["data"] or "upload_url" not in b1["data"]:
            os.unlink(tmp_path)
            return jsonify({"step":"create_container", "status": s1, "response": b1}), 400

        container_id = b1["data"]["container_id"]
        upload_url = b1["data"]["upload_url"]

        # 3) PUT the bytes to upload_url
        size_bytes = os.path.getsize(tmp_path)
        with open(tmp_path, "rb") as f:
            put = requests.put(
                upload_url,
                data=f,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size_bytes),
                },
                timeout=120
            )
        os.unlink(tmp_path)
        s2, b2, _ = json_or_text(put)
        if s2 not in (200, 201, 204):  # some CDNs return 204 for successful upload
            return jsonify({"step":"upload_file", "status": s2, "response": b2}), 400

        # 4) Publish container
        r3 = requests.post(
            PUBLISH_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"open_id": open_id, "container_id": container_id},
            timeout=60
        )
        s3, b3, _ = json_or_text(r3)
        if s3 != 200:
            return jsonify({"step":"publish", "status": s3, "response": b3, "container_id": container_id}), 400

        return jsonify({
            "ok": True,
            "container_id": container_id,
            "upload": {"status": s2, "resp": b2},
            "publish": {"status": s3, "resp": b3}
        }), 200

    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=True)
