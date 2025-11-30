import os
import shutil
import subprocess
from flask import render_template, jsonify, request
from app import app
from config import device_state, SCOPES
from google_auth_oauthlib.flow import Flow

PHOTOS_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "photos")

def get_redirect_uri():
    """Get OAuth redirect URI based on current request."""
    return request.url_root.rstrip('/') + '/oauth2callback'


@app.route("/admin")
def admin():
    """Admin page for managing the photo frame."""
    # Count photos
    photo_count = 0
    if os.path.exists(PHOTOS_DIR):
        for root, dirs, files in os.walk(PHOTOS_DIR):
            photo_count += len([f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))])
    
    # Check display mode
    mode_file = os.path.join(os.path.dirname(__file__), "..", "..", ".display_mode")
    display_mode = "hdmi"  # default
    if os.path.exists(mode_file):
        with open(mode_file) as f:
            display_mode = f.read().strip()
    
    # Generate auth URL for "Pick New Photos"
    flow = Flow.from_client_secrets_file(
        "secrets.json",
        scopes=SCOPES,
        redirect_uri=get_redirect_uri()
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    
    return render_template("admin.html", photo_count=photo_count, auth_url=auth_url, display_mode=display_mode)


@app.route("/admin/delete_photos", methods=["POST"])
def delete_photos():
    """Delete all downloaded photos."""
    try:
        if os.path.exists(PHOTOS_DIR):
            for item in os.listdir(PHOTOS_DIR):
                item_path = os.path.join(PHOTOS_DIR, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                elif item != '.gitkeep':
                    os.remove(item_path)
        return jsonify({"success": True, "reload": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/git_pull", methods=["POST"])
def git_pull():
    """Pull latest code from git."""
    try:
        # Get the repo root (parent of app directory)
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        result = subprocess.run(
            ["/usr/bin/git", "pull"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PATH": "/usr/bin:/bin:/usr/local/bin"}
        )
        if result.returncode == 0:
            return jsonify({"success": True, "output": result.stdout})
        else:
            return jsonify({"success": False, "error": result.stderr or result.stdout})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/restart", methods=["POST"])
def restart_service():
    """Restart the instapi service (and kiosk if HDMI mode)."""
    try:
        # Check which mode we're in
        mode_file = os.path.join(os.path.dirname(__file__), "..", "..", ".display_mode")
        mode = "hdmi"  # default
        if os.path.exists(mode_file):
            with open(mode_file) as f:
                mode = f.read().strip()
        
        # Restart the flask app service
        subprocess.Popen(
            ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "instapi"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # If HDMI mode, also restart the kiosk
        if mode == "hdmi":
            subprocess.Popen(
                ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "instapi-kiosk"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/reset", methods=["POST"])
def reset_to_setup():
    """Reset to setup screen - behavior depends on display mode."""
    try:
        # Clear app state
        device_state.clear()
        
        # Check which mode we're in
        mode_file = os.path.join(os.path.dirname(__file__), "..", "..", ".display_mode")
        mode = "hdmi"  # default
        if os.path.exists(mode_file):
            with open(mode_file) as f:
                mode = f.read().strip()
        
        if mode == "hdmi":
            # HDMI mode: restart kiosk to go back to index page
            subprocess.Popen(
                ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "instapi-kiosk"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return jsonify({"success": True, "message": "Kiosk restarting to setup screen"})
        else:
            # USB mode: run update-photos script to put QR placeholder on USB
            script_path = os.path.join(os.path.dirname(__file__), "..", "..", "pi-setup", "reset-to-setup.sh")
            if os.path.exists(script_path):
                subprocess.Popen(
                    ["/bin/bash", script_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            return jsonify({"success": True, "message": "USB image reset to QR placeholder"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
