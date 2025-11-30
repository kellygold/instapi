# app.py - Flask app instance (to avoid circular imports)
from flask import Flask

app = Flask(__name__)
app.secret_key = "your_secret_key"

# Basic Flask config
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['PREFERRED_URL_SCHEME'] = 'https'
