import os
from urllib.parse import urlencode
from dotenv import load_dotenv
from flask import Flask, redirect, request, jsonify, Response

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev")

CLIENT_KEY     = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET  = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_URI   = os.getenv("REDIRECT_URI")  # <- must be EXACTLY what's in the portal

AUTH_URL  = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

@app.route("/")
def home():
    # Simple landing page to avoid immediate redirect loops
    return (
        "<h2>TikTok OAuth Debug</h2>"
        "<p><a href='/debug-auth'>Go to /debug-auth</a> (shows the EXACT authorize URL)</p>"
        "<p><a href='/login'>Go to /login</a> (redirects straight to TikTok)</p>"
        f"<pre>.env values\nCLIENT_KEY={CLIENT_KEY}\nREDIRECT_URI={REDIRECT_URI}</pre>"
    )

@app.route("/debug-auth")
def debug_auth():
    # Build the query we will send to TikTok so you can SEE it
    qs = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": "user.info.basic,video.upload",  # keep minimal until login works
        "redirect_uri": REDIRECT_URI,
        "state": "state123",
    }
    auth_url = f"{AUTH_URL}?{urlencode(qs)}"

    html = (
        "<h3>Authorize URL (copy this entire line)</h3>"
        f"<code style='word-break:break-all'>{auth_url}</code>"
        "<hr/>"
        "<p>Now click <a href='/login'>/login</a> to use the same URL.</p>"
        f"<p><b>IMPORTANT:</b> This redirect_uri must be byte-for-byte identical "
        f"to what you saved in TikTok Portal: <code>{REDIRECT_URI}</code></p>"
    )
    return Response(html, mimetype="text/html")

@app.route("/login")
def login():
    # Use the exact same construction as in /debug-auth
    qs = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": "user.info.basic,video.upload",
        "redirect_uri": REDIRECT_URI,
        "state": "state123",
    }
    auth_url = f"{AUTH_URL}?{urlencode(qs)}"
    return redirect(auth_url)

@app.route("/callback")
def callback():
    # TikTok will hit this AFTER the user authorizes
    err  = request.args.get("error")
    code = request.args.get("code")

    if err:
        return f"❌ TikTok sent error: {err}", 400
    if not code:
        return "❌ No code received on /callback", 400

    # Exchange the code for tokens
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,   # MUST match exactly again here
    }
    # TikTok expects x-www-form-urlencoded
    import requests
    r = requests.post(TOKEN_URL, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded"
    })

    return Response(
        f"<h3>Token Exchange Response</h3><pre>{r.status_code}\n{r.text}</pre>",
        mimetype="text/html"
    )

if __name__ == "__main__":
    app.run(debug=True)
