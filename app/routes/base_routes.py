# routes/base_routes.py
import io
import json
import qrcode
from flask import render_template, jsonify, redirect, url_for, request, send_file
from google_auth_oauthlib.flow import Flow
from datetime import datetime, timedelta
from app import app


from config import SCOPES, device_state
import os

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Load redirect URI from secrets.json and derive base URL
with open("secrets.json") as f:
    _secrets = json.load(f)
    REDIRECT_URI = _secrets["web"]["redirect_uris"][0]
    # Extract base URL from redirect URI (e.g., https://xxx.ngrok-free.dev)
    BASE_URL = REDIRECT_URI.rsplit("/", 1)[0]


@app.route("/auth_status")
def auth_status():
    """Return JSON indicating if the user is authenticated."""
    if "credentials" not in device_state:
        return jsonify({
            "authenticated": False,
            "message": "Waiting for authentication. Scan QR code and sign in with Google, or check your server secrets configuration."
        })
    return jsonify({"authenticated": True, "message": "Authenticated"})


@app.route("/")
def index():
    """Always start fresh auth - generate new auth URL."""
    # Clear any old state for fresh start
    device_state.clear()
    
    flow = Flow.from_client_secrets_file(
        "secrets.json",
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    device_state["auth_url"] = auth_url
    return render_template("index.html", auth_url=auth_url)


@app.route("/auth")
def auth():
    """Direct auth - skip setup page, go straight to Google."""
    device_state.clear()
    flow = Flow.from_client_secrets_file(
        "secrets.json",
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    device_state["auth_url"] = auth_url
    return redirect(auth_url)


@app.route("/auth_qr")
def auth_qr():
    """Generate a sign-in QR code."""
    if "auth_url" not in device_state:
        return "No auth URL found", 404

    img_io = io.BytesIO()
    img = qrcode.make(device_state["auth_url"])
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')


@app.route("/oauth2callback")
def oauth2callback():
    """OAuth2 callback - immediately create picker session."""
    flow = Flow.from_client_secrets_file(
        "secrets.json",
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    try:
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        device_state["credentials"] = {
            "token": credentials.token,
        }
        
        # Immediately create picker session and redirect to picker
        return redirect(url_for("launch_picker"))
    except Exception as e:
        print("Error exchanging token:", e)
        return "Failed to exchange token.", 500


@app.route("/choose_mode_qr")
def choose_mode_qr():
    """QR to link user to choose_mode on phone."""
    choose_mode_url = f"{BASE_URL}/choose_mode"
    img_io = io.BytesIO()
    img = qrcode.make(choose_mode_url)
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')


@app.route("/wait_for_photos", methods=["GET"])
def wait_for_photos():
    """Show the transitional page with a QR/link to continue setup."""
    if "credentials" not in device_state:
        return redirect(url_for("index"))

    choose_mode_url = f"{BASE_URL}/choose_mode"
    return render_template("select_photos.html", choose_mode_url=choose_mode_url)

print("DEBUG: Current routes:", app.url_map)
