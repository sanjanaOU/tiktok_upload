import os
from flask import Flask, send_from_directory, jsonify

app = Flask(__name__)

@app.route("/")
def index():
    return (
        "<h3>TikTok verification server</h3>"
        "<p>Use <code>/callback/&lt;your_verification_file.txt&gt;</code> to serve the verification file.</p>"
        "<p>Health: <a href='/health'>/health</a></p>"
    )

# Health probe for Render
@app.route("/health")
def health():
    return jsonify({"ok": True}), 200

# TikTok verification file route
# TikTok expects you to host their TXT file at:
#    https://your-domain.com/callback/<the-exact-file-name>.txt
@app.route("/callback/<path:filename>")
def serve_tiktok_verification(filename):
    # This will serve files from the "callback" directory
    return send_from_directory("callback", filename, mimetype="text/plain")


if __name__ == "__main__":
    # Local run: python app.py
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
