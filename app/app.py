# app.py - Flask app instance (to avoid circular imports)
import os
from flask import Flask
from config import get_flask_secret

app = Flask(__name__)

# Load secret key from secrets.json (via centralized config loader)
app.secret_key = get_flask_secret() or os.urandom(24).hex()

# Session cookies: allow both HTTP (local) and HTTPS (ngrok)
# SameSite=Lax is the safe default; Secure=False so cookies work over HTTP too
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False

# Limit upload request size to 200MB to prevent OOM from huge multipart requests.
# Client-side JS batches uploads into groups of 10, so this is ~20 photos per request.
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024
