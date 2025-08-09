# app.py
import os
from pathlib import Path
from flask import Flask, send_from_directory, abort, jsonify

app = Flask(__name__)

CALLBACK_DIR = Path(__file__).parent.joinpath("callback").resolve()

@app.route("/health")
def health():
    return "ok", 200

@app.route("/debug-callback")
def debug_callback():
    exists = CALLBACK_DIR.exists()
    try:
        files = sorted(os.listdir(CALLBACK_DIR)) if exists else []
    except Exception as e:
        files = [f"<error listing dir: {e}>"]
    return jsonify({
        "callback_dir": str(CALLBACK_DIR),
        "exists": exists,
        "files": files,
    })

@app.route("/callback/")
def callback_root():
    if not CALLBACK_DIR.exists():
        return f"{CALLBACK_DIR} does not exist on the server", 404
    return "callback ready", 200

@app.route("/callback/<path:filename>")
def serve_callback_file(filename: str):
    if not filename.endswith(".txt"):
        abort(404)
    if not CALLBACK_DIR.exists():
        return f"{CALLBACK_DIR} does not exist on the server", 404
    try:
        return send_from_directory(
            CALLBACK_DIR,
            filename,
            mimetype="text/plain",
            as_attachment=False,
        )
    except FileNotFoundError:
        abort(404)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print("CALLBACK_DIR:", CALLBACK_DIR)
    print("CALLBACK_DIR exists:", CALLBACK_DIR.exists())
    if CALLBACK_DIR.exists():
        print("Files in callback:", os.listdir(CALLBACK_DIR))
    app.run(host="0.0.0.0", port=port)
