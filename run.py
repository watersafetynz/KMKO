# run.py
import os
from flask import Flask, jsonify
from dotenv import load_dotenv
from db import get_engine

# Load environment variables from .env (safe to no-op in prod if not present)
load_dotenv()

app = Flask(__name__)

# ---- Routes ----
@app.get("/")
def index():
    return "WSFL Flask service is alive ✨", 200

@app.get("/healthz")
def healthz():
    """Health endpoint with an optional DB ping."""
    try:
        with get_engine().connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return jsonify(status="ok", db="up"), 200
    except Exception as e:
        # Keep 200 so platforms don’t insta-restart while you debug DB creds
        return jsonify(status="ok", db="down", error=str(e)), 200


# ---- Error handlers (optional but handy) ----
@app.errorhandler(404)
def not_found(_e):
    return jsonify(error="not_found", message="The requested resource was not found."), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify(error="server_error", message=str(e)), 500


# ---- Local dev entrypoint ----
if __name__ == "__main__":
    # For local testing only. In production, use gunicorn:
    #   gunicorn run:app --bind 0.0.0.0:10000
    port = int(os.getenv("PORT", "10000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
