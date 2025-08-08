import os
import secrets
from urllib.parse import urlencode

import requests
from flask import (
    Flask, request, session, redirect, jsonify, make_response
)

# ------------------------------------------------------------------------------
# ENV VARS you must set in Render (or a .env when running locally):
#   TIKTOK_CLIENT_KEY=sbawemm7fb4n0ps8iz
#   TIKTOK_CLIENT_SECRET=uF1lxNnTU20eDtoqojsfQe75HA5Jvn4g
#   REDIRECT_URI=https://tiktok-upload.onrender.com/callback
#   FLASK_SECRET_KEY=<any random string>
# ------------------------------------------------------------------------------

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip()

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
CONTAINER_URL = "https://open.tiktokapis.com/v2/post/publish/container/"
PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/"
SCOPES = "user.info.basic,video.upload,video.publish"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_" + secrets.token_hex(16))


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def new_state():
    s = secrets.token_urlsafe(24)
    session["oauth_state"] = s
    return s


def json_or_text(resp):
    """Return (status_code, body, is_json) for robust logging"""
    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        try:
            return resp.status_code, resp.json(), True
        except Exception:
            pass
    return resp.status_code, {"non_json_body": resp.text}, False


def require_auth():
    if not session.get("access_token") or not session.get("open_id"):
        return False
    return True


# ------------------------------------------------------------------------------
# OAuth routes
# ------------------------------------------------------------------------------
@app.route("/")
def index():
    if require_auth():
        return make_response(
            f"""
            <h3>TikTok OAuth ✔</h3>
            <p>open_id: <code>{session.get('open_id')}</code></p>
            <p><a href="/upload">Publish a video from VERIFIED URL</a></p>
            <p><a href="/logout">Logout</a></p>
            <p><a href="/debug-auth">/debug-auth</a></p>
            """, 200
        )
    else:
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
        return "❌ Missing ?code from TikTok.", 400
    if session.get("oauth_state") != state:
        return "❌ State mismatch. Start login again.", 400

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,  # must match exactly
    }
    r = requests.post(TOKEN_URL, headers=headers, data=urlencode(payload), timeout=30)
    status, body, is_json = json_or_text(r)

    if status != 200 or ("access_token" not in body):
        return make_response(
            f"❌ Token exchange failed ({status}):<pre>{body}</pre>", 400
        )

    session["access_token"] = body["access_token"]
    session["open_id"] = body.get("open_id")
    return redirect("/upload")


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


# ------------------------------------------------------------------------------
# Simple UI + publish-by-URL flow (PULL_FROM_URL)
# ------------------------------------------------------------------------------
@app.route("/upload", methods=["GET"])
def upload_page():
    if not require_auth():
        return redirect("/login")
    # Simple inline form and client-side fetch to POST /publish-by-url
    return make_response(
        """
        <h2>Publish to TikTok: Pull From Verified URL</h2>
        <p><b>Important:</b> The video URL must be hosted on a domain you've verified in the TikTok Developer Portal.</p>
        <form id="f" onsubmit="return false">
          <label>Public video URL (.mp4) on VERIFIED domain</label><br>
          <input style="width:600px" name="video_url" value="" placeholder="https://tiktok-upload.onrender.com/media/example.mp4"/><br><br>
          <label>Caption</label><br>
          <input style="width:600px" name="caption" value="Hello from API!"/><br><br>
          <button onclick="doUpload()">Publish</button>
        </form>
        <pre id="out" style="white-space:pre-wrap;background:#111;color:#0f0;padding:10px;"></pre>

        <script>
        async function doUpload() {
          const fd = new FormData(document.getElementById('f'));
          const video_url = fd.get('video_url');
          const caption = fd.get('caption');

          const res = await fetch('/publish-by-url', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({video_url, caption})
          });
          const data = await res.json().catch(() => ({non_json: true, body: await res.text()}));
          document.getElementById('out').textContent = JSON.stringify(data, null, 2);
        }
        </script>
        """, 200
    )


@app.route("/publish-by-url", methods=["POST"])
def publish_by_url():
    if not require_auth():
        return jsonify({"error": "not_authenticated"}), 401

    payload = request.get_json(silent=True) or {}
    video_url = (payload.get("video_url") or "").strip()
    caption = (payload.get("caption") or "").strip()

    if not video_url:
        return jsonify({"error": "video_url is required"}), 400

    access_token = session["access_token"]
    open_id = session["open_id"]

    # ---- Step 1: Create container with PULL_FROM_URL ----
    step1_json = {}
    step2_json = {}
    try:
        create_payload = {
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,  # must be on a verified domain
            },
            "text": caption,
        }
        r1 = requests.post(
            CONTAINER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=create_payload,
            timeout=60,
        )
        status1, body1, _ = json_or_text(r1)
        step1_json = {"status": status1, "response": body1}

        if status1 != 200 or not isinstance(body1, dict) or "data" not in body1 or "container_id" not in body1["data"]:
            return jsonify({
                "step": "create_container",
                "status": status1,
                "response": body1
            }), 400

        container_id = body1["data"]["container_id"]

        # ---- Step 2: Publish the container ----
        r2 = requests.post(
            PUBLISH_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"open_id": open_id, "container_id": container_id},
            timeout=60,
        )
        status2, body2, _ = json_or_text(r2)
        step2_json = {"status": status2, "response": body2}

        if status2 != 200:
            return jsonify({
                "step": "publish",
                "status": status2,
                "response": body2,
                "container_id": container_id
            }), 400

        return jsonify({
            "ok": True,
            "container_id": container_id,
            "create_container": step1_json,
            "publish": step2_json
        }), 200

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "create_container": step1_json,
            "publish": step2_json
        }), 500


# ------------------------------------------------------------------------------
@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    # Local dev
    app.run(host="0.0.0.0", port=5051, debug=True)
