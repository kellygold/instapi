# routes/base_routes.py
import io
import qrcode
from flask import render_template, jsonify, redirect, url_for, request, send_file, send_from_directory
from datetime import datetime, timedelta
from app import app
import db

from config import get_base_url
from google_oauth import start_authorization, setup_url, callback_flow
from utils import get_upload_url
import os

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(app.root_path, "static"),
                               "favicon.ico", mimetype="image/x-icon")
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

BASE_URL = get_base_url()


@app.route("/auth_status")
def auth_status():
    """Return JSON indicating if the user is authenticated."""
    if not db.get_setting("credentials"):
        return jsonify({
            "authenticated": False,
            "message": "Waiting for authentication. Scan QR code and sign in with Google, or check your server secrets configuration."
        })
    return jsonify({"authenticated": True, "message": "Authenticated"})


@app.route("/")
def index():
    """Show setup page, or redirect to slideshow if photos ready."""
    # If photos are already ready, go to slideshow
    if db.get_setting("done") and db.get_photo_count() > 0:
        return redirect(url_for("slideshow"))

    return render_template("index.html", auth_url=setup_url())


@app.route("/auth")
def auth():
    """Direct auth - skip setup page, go straight to Google."""
    # Keep the existing connection/photos until replacement consent succeeds.
    auth_url = start_authorization()
    return redirect(auth_url)


@app.route("/auth_qr")
def auth_qr():
    """Generate a sign-in QR code."""
    auth_url = setup_url()

    img_io = io.BytesIO()
    img = qrcode.make(auth_url)
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')


@app.route("/oauth2callback")
def oauth2callback():
    """OAuth2 callback - immediately create picker session."""
    try:
        flow = callback_flow(request.args.get("state"))
    except ValueError:
        return "Google sign-in expired or was already used. Return to the frame and try again.", 400
    try:
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        db.set_setting("credentials", {
            "token": credentials.token,
        })
        db.delete_setting("auth_url")
        db.delete_setting("picking_session_id")
        db.delete_setting("picker_url")

        # Immediately create picker session and redirect to picker
        return redirect(url_for("launch_picker"))
    except Exception:
        # OAuth exceptions can contain provider responses; keep them out of UI/logs.
        app.logger.warning("Google sign-in could not be completed")
        return "Google sign-in could not be completed. Return to the frame and try again.", 400


@app.route("/choose_mode_qr")
def choose_mode_qr():
    """QR to link user to upload page for adding photos.
    Child frames point to master's upload page using their sync token."""
    url = get_upload_url()
    img_io = io.BytesIO()
    img = qrcode.make(url)
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

# Debug route list is printed in main.py after all imports
