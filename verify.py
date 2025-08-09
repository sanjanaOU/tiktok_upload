import os
from flask import Flask, send_from_directory, abort

app = Flask(__name__)

CALLBACK_DIR = os.path.join(app.root_path, "callback")

@app.route("/health")
def health():
    return "ok", 200

# Optional: a simple index so /callback/ returns 200
@app.route("/callback/")
def callback_index():
    if not os.path.isdir(CALLBACK_DIR):
        return "callback directory missing", 404
    return "callback ready", 200

# Serve verification files from /callback/<filename>
@app.route("/callback/<path:filename>")
def serve_callback_file(filename: str):
    # Only allow .txt for safety
    if not filename.endswith(".txt"):
        abort(404)
    return send_from_directory(CALLBACK_DIR, filename, mimetype="text/plain")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
