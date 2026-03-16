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
