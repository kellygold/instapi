import os
import getpass
import shutil
import subprocess
import socket
from functools import wraps
from flask import render_template, jsonify, request, session, redirect, url_for
from app import app
import db
from config import SCOPES, PHOTOS_DIR, load_slideshow_config, save_slideshow_config
from google_auth_oauthlib.flow import Flow
from utils import get_display_mode
from routes.sync_routes import mark_manifest_dirty

MODE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", ".display_mode")

# Load fallback redirect URI from secrets.json (used when dynamic detection yields HTTP non-localhost)
import json
with open("secrets.json") as _f:
    _FALLBACK_REDIRECT_URI = json.load(_f)["web"]["redirect_uris"][0]


def verify_password(password):
    """Verify against system password or test override."""
    test_pw = os.environ.get("INSTAPI_ADMIN_PASSWORD")
    if test_pw:
        return password == test_pw
    # Production: verify via su command
    username = getpass.getuser()
    try:
        result = subprocess.run(
            ["/bin/su", "-c", "true", username],
            input=password + "\n",
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authenticated"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if verify_password(password):
            session["admin_authenticated"] = True
            return redirect(url_for("admin"))
        return render_template("admin_login.html", error="Invalid password"), 401
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@require_admin
def admin():
    """Admin page for managing the photo frame."""
    # Count photos
    photo_count = db.get_photo_count()

    # Check display mode
    display_mode = get_display_mode()

    # Generate auth URL for "Pick New Photos"
    # Check forwarded headers for ngrok/reverse proxy, fall back to request.url_root
    forwarded_host = request.headers.get('X-Forwarded-Host') or request.headers.get('Host')
    forwarded_proto = request.headers.get('X-Forwarded-Proto', 'http')

    if forwarded_host and 'ngrok' in forwarded_host:
        # Behind ngrok - use forwarded headers
        base_url = f"https://{forwarded_host}"
    elif forwarded_host and 'localhost' not in forwarded_host:
        # Behind some other proxy
        base_url = f"{forwarded_proto}://{forwarded_host}"
    else:
        # Direct access
        base_url = request.url_root.rstrip('/')

    redirect_uri = base_url + '/oauth2callback'
    # Google requires HTTPS for non-localhost redirect URIs
    if redirect_uri.startswith("http://") and "localhost" not in redirect_uri:
        redirect_uri = _FALLBACK_REDIRECT_URI
    flow = Flow.from_client_secrets_file(
        "secrets.json",
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    upload_token = db.get_setting("upload_token", "")
    sync_role = db.get_setting("sync_role", "")
    # Infer master role if children exist but role wasn't saved
    if not sync_role and db.get_setting("sync_children"):
        sync_role = "master"
        db.set_setting("sync_role", "master")
    storage = get_storage_info()
    return render_template("admin.html",
        photo_count=photo_count, auth_url=auth_url,
        display_mode=display_mode, upload_token=upload_token,
        sync_role=sync_role,
        sync_label=db.get_setting("sync_label", ""),
        storage=storage)



@app.route("/admin/git_pull", methods=["POST"])
@require_admin
def git_pull():
    """Pull latest code from git."""
    try:
        # Get the repo root (parent of app directory)
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        print(f"[GIT PULL] Starting git pull in: {repo_root}")

        # First check current branch and status
        status_result = subprocess.run(
            ["/usr/bin/git", "status", "--short"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        print(f"[GIT PULL] Current status: {status_result.stdout.strip() or 'clean'}")

        result = subprocess.run(
            ["/usr/bin/git", "pull"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PATH": "/usr/bin:/bin:/usr/local/bin"}
        )

        print(f"[GIT PULL] Return code: {result.returncode}")
        print(f"[GIT PULL] stdout: {result.stdout}")
        if result.stderr:
            print(f"[GIT PULL] stderr: {result.stderr}")

        if result.returncode == 0:
            return jsonify({"success": True, "output": result.stdout})
        else:
            return jsonify({"success": False, "error": result.stderr or result.stdout})
    except Exception as e:
        print(f"[GIT PULL] Exception: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/restart", methods=["POST"])
@require_admin
def restart_service():
    """Restart the instapi service (and kiosk if HDMI mode)."""
    try:
        # Check which mode we're in
        mode = "hdmi"  # default
        if os.path.exists(MODE_FILE):
            with open(MODE_FILE) as f:
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


@app.route("/admin/sync_usb", methods=["POST"])
@require_admin
def sync_usb():
    """Sync photos to USB drive (USB mode only)."""
    try:
        script_path = os.path.join(os.path.dirname(__file__), "..", "..", "pi-setup", "update-photos.sh")
        result = subprocess.run(
            ["/bin/bash", script_path],
            capture_output=True,
            text=True,
            timeout=60
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return jsonify({"success": True, "message": "Photos synced to USB.", "output": output})
        else:
            return jsonify({"success": False, "error": "Sync failed", "output": output})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/reset", methods=["POST"])
@require_admin
def reset_to_setup():
    """Reset to setup screen - behavior depends on display mode."""
    try:
        # Delete all photos and thumbnails from disk
        if os.path.exists(PHOTOS_DIR):
            for item in os.listdir(PHOTOS_DIR):
                item_path = os.path.join(PHOTOS_DIR, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                elif item != '.gitkeep':
                    os.remove(item_path)

        # Clear app state
        db.clear_all_settings()
        db.clear_all_photos()

        # Check which mode we're in
        mode = get_display_mode()

        if mode == "hdmi":
            # HDMI mode: restart kiosk to go back to index page
            subprocess.Popen(
                ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "instapi-kiosk"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return jsonify({"success": True, "mode": "hdmi", "message": "Kiosk restarting to setup screen", "redirect": True})
        else:
            # USB mode: run reset script to put QR placeholder on USB
            script_path = os.path.join(os.path.dirname(__file__), "..", "..", "pi-setup", "reset-to-setup.sh")
            result = subprocess.run(
                ["/bin/bash", script_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            output = result.stdout + result.stderr
            if result.returncode == 0:
                return jsonify({"success": True, "mode": "usb", "message": "QR placeholder copied to USB.", "output": output, "redirect": False})
            else:
                return jsonify({"success": False, "mode": "usb", "error": result.stderr or "Script failed", "output": output})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============== NEW ENDPOINTS ==============

def get_local_ip():
    """Get local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "Unknown"


def get_uptime():
    """Get system uptime as human-readable string."""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except:
        return "Unknown"


def get_storage_info():
    """Get storage usage info."""
    try:
        # Get disk usage for photos directory
        photos_size = 0
        if os.path.exists(PHOTOS_DIR):
            for root, dirs, files in os.walk(PHOTOS_DIR):
                for f in files:
                    photos_size += os.path.getsize(os.path.join(root, f))

        # Get total disk usage
        total, used, free = shutil.disk_usage("/")

        return {
            "photos_mb": round(photos_size / (1024 * 1024), 1),
            "used_gb": round(used / (1024 ** 3), 1),
            "free_gb": round(free / (1024 ** 3), 1),
            "total_gb": round(total / (1024 ** 3), 1)
        }
    except:
        return {"photos_mb": 0, "used_gb": 0, "free_gb": 0, "total_gb": 0}


@app.route("/admin/system_info")
@require_admin
def system_info():
    """Return system information."""
    storage = get_storage_info()

    # Count photos
    photo_count = db.get_photo_count()

    return jsonify({
        "ip_address": get_local_ip(),
        "uptime": get_uptime(),
        "display_mode": get_display_mode(),
        "photo_count": photo_count,
        "storage": storage
    })


@app.route("/admin/photos")
@require_admin
def list_photos():
    """Return list of all photos with their paths and uploader info."""
    meta = db.get_upload_meta()

    photos = []
    if os.path.exists(PHOTOS_DIR):
        for root, dirs, files in os.walk(PHOTOS_DIR):
            dirs[:] = [d for d in dirs if d != 'thumbs']
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')) and f != '.gitkeep':
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, os.path.dirname(PHOTOS_DIR))
                    photos.append({
                        "path": f"/static/{rel_path}",
                        "thumb": f"/static/photos/thumbs/{f}",
                        "name": f,
                        "size": os.path.getsize(full_path),
                        "uploaded_by": meta.get(f, "unknown")
                    })
    return jsonify(photos)


@app.route("/admin/delete_photo", methods=["POST"])
@require_admin
def delete_single_photo():
    """Delete a single photo. On child frames, proxies to master."""
    try:
        data = request.get_json()
        photo_path = data.get("path", "")

        # Security: ensure path is within photos dir
        if not photo_path.startswith("/static/photos/"):
            return jsonify({"success": False, "error": "Invalid path"})

        filename = os.path.basename(photo_path)

        # Child frame: proxy delete to master
        if db.get_setting("sync_role") == "child" and "/sync/" in photo_path:
            master_url = db.get_setting("master_url")
            sync_token = db.get_setting("sync_token")
            if not master_url or not sync_token:
                return jsonify({"success": False, "error": "Not configured for sync"})

            import requests as req
            try:
                resp = req.post(
                    f"{master_url}/sync/delete_photo",
                    json={"token": sync_token, "filename": filename},
                    timeout=15
                )
                result = resp.json()
                if result.get("success"):
                    # Also delete local synced copy + thumbnail
                    file_path = os.path.join(os.path.dirname(PHOTOS_DIR), photo_path.lstrip("/static/"))
                    file_path = os.path.normpath(file_path)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    thumb_path = os.path.join(PHOTOS_DIR, "thumbs", filename)
                    if os.path.exists(thumb_path):
                        os.remove(thumb_path)
                    db.remove_photo(filename)
                    return jsonify({"success": True})
                else:
                    return jsonify(result), resp.status_code
            except Exception as e:
                return jsonify({"success": False, "error": f"Could not reach master: {e}"})

        # Master or standalone: delete locally
        file_path = os.path.join(os.path.dirname(PHOTOS_DIR), photo_path.lstrip("/static/"))
        file_path = os.path.normpath(file_path)

        # Verify it's within PHOTOS_DIR
        if not file_path.startswith(os.path.normpath(PHOTOS_DIR)):
            return jsonify({"success": False, "error": "Invalid path"})

        if os.path.exists(file_path):
            os.remove(file_path)
            thumb_path = os.path.join(PHOTOS_DIR, "thumbs", filename)
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
            db.remove_photo(filename)
            mark_manifest_dirty()
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "File not found"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/settings", methods=["GET"])
@require_admin
def get_settings():
    """Get current slideshow settings."""
    return jsonify(load_slideshow_config())


@app.route("/admin/settings", methods=["POST"])
@require_admin
def update_settings():
    """Update slideshow settings."""
    try:
        data = request.get_json()
        config = load_slideshow_config()

        # Update only valid keys
        if "slide_duration" in data:
            config["slide_duration"] = max(1, min(60, int(data["slide_duration"])))
        if "transition" in data and data["transition"] in ["fade", "slide", "zoom"]:
            config["transition"] = data["transition"]
        if "shuffle" in data:
            config["shuffle"] = bool(data["shuffle"])
        if "ken_burns" in data:
            config["ken_burns"] = bool(data["ken_burns"])

        if save_slideshow_config(config):
            return jsonify({"success": True, "config": config})
        else:
            return jsonify({"success": False, "error": "Failed to save"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/switch_mode", methods=["POST"])
@require_admin
def switch_mode():
    """Switch between USB and HDMI display modes."""
    try:
        data = request.get_json()
        new_mode = data.get("mode", "").lower()

        if new_mode not in ["usb", "hdmi"]:
            return jsonify({"success": False, "error": "Invalid mode. Use 'usb' or 'hdmi'"})

        # Write new mode
        with open(MODE_FILE, 'w') as f:
            f.write(new_mode)

        return jsonify({
            "success": True,
            "mode": new_mode,
            "message": f"Mode switched to {new_mode.upper()}. Restart required for full effect."
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/download_status")
@require_admin
def download_status():
    """Return current photo download progress."""
    return jsonify({
        "downloading": db.get_setting("downloading", False),
        "download_total": db.get_setting("download_total", 0),
        "download_completed": db.get_setting("download_completed", 0),
        "done": db.get_setting("done", False),
        "photo_count": db.get_photo_count()
    })


@app.route("/admin/update_and_restart", methods=["POST"])
@require_admin
def update_and_restart():
    """Pull latest code and restart the service."""
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        result = subprocess.run(
            ["/usr/bin/git", "pull"], cwd=repo_root,
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "PATH": "/usr/bin:/bin:/usr/local/bin"}
        )
        if result.returncode != 0:
            return jsonify({"success": False, "error": result.stderr or result.stdout})

        mode = get_display_mode()
        subprocess.Popen(
            ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "instapi"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if mode == "hdmi":
            subprocess.Popen(
                ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "instapi-kiosk"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        return jsonify({"success": True, "message": "Updated! Restarting..."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
