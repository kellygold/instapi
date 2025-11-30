# app.py - Flask app instance (to avoid circular imports)
import json
import os
from flask import Flask

app = Flask(__name__)

# Load secret key from secrets.json
try:
    with open("secrets.json") as f:
        _secrets = json.load(f)
        app.secret_key = _secrets.get("flask_secret", os.urandom(24).hex())
except FileNotFoundError:
    app.secret_key = os.urandom(24).hex()  # Fallback for dev

# Basic Flask config
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['PREFERRED_URL_SCHEME'] = 'https'
