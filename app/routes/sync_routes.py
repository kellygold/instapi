# routes/sync_routes.py
import os
import hashlib
import time
import threading
import shutil
import secrets as secrets_mod
import requests
from datetime import datetime
from flask import jsonify, request, send_from_directory
from PIL import Image
from app import app
import config
import db
from utils import sync_photos_to_usb, get_display_mode


def require_admin(f):
    """Require admin authentication - local copy to avoid circular import."""
    from functools import wraps
    from flask import session, redirect, url_for, jsonify, request
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authenticated"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

# --- Manifest cache ---
_manifest_cache = None
_manifest_dirty = True


def mark_manifest_dirty():
    """Mark manifest as needing rebuild. Call after photos change."""
    global _manifest_dirty
    _manifest_dirty = True


def _build_manifest():
    """Walk config.PHOTOS_DIR (excluding thumbs/ and sync/) and return photo list with md5."""
    global _manifest_cache, _manifest_dirty
    photos = []
    if os.path.exists(config.PHOTOS_DIR):
        for root, dirs, files in os.walk(config.PHOTOS_DIR):
            dirs[:] = [d for d in dirs if d not in ('thumbs', config.SYNC_DIR_NAME)]
            for f in sorted(files):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, config.PHOTOS_DIR)
                    try:
                        h = hashlib.md5()
                        with open(full_path, 'rb') as fh:
                            for chunk in iter(lambda: fh.read(8192), b''):
                                h.update(chunk)
                        md5 = h.hexdigest()
                        size = os.path.getsize(full_path)
                    except OSError:
                        continue
                    photos.append({
                        "path": rel_path,
                        "size": size,
                        "md5": md5,
                    })
    _manifest_cache = {
        "photos": photos,
        "photo_count": len(photos),
        "timestamp": int(time.time()),
    }
    _manifest_dirty = False
    return _manifest_cache


def _get_manifest():
    """Return cached manifest, rebuilding if dirty."""
    global _manifest_dirty
    if _manifest_dirty or _manifest_cache is None:
        return _build_manifest()
    return _manifest_cache


def _validate_sync_token(token):
    """Check if token matches any registered child token on this master."""
    children = db.get_setting("sync_children", [])
    return any(c["token"] == token for c in children)


# ============== MASTER ENDPOINTS ==============

@app.route("/sync/manifest")
def sync_manifest():
    """Serve photo manifest for child Pis to sync from."""
    if db.get_setting("sync_role") != "master":
        return jsonify({"error": "Not a master"}), 404

    token = request.args.get("token", "")
    if not _validate_sync_token(token):
        return jsonify({"error": "Invalid token"}), 403

    manifest = _get_manifest()
    # Include upload metadata so children know who uploaded each photo
    manifest["upload_meta"] = db.get_upload_meta()
    # Tell the child its own label (based on which token authenticated)
    for child in db.get_setting("sync_children", []):
        if child["token"] == token:
            manifest["your_label"] = child["label"]
            break
    return jsonify(manifest)


@app.route("/sync/photo/<path:photo_path>")
def sync_photo(photo_path):
    """Serve a single photo file for child download."""
    if db.get_setting("sync_role") != "master":
        return jsonify({"error": "Not a master"}), 404

    token = request.args.get("token", "")
    if not _validate_sync_token(token):
        return jsonify({"error": "Invalid token"}), 403

    # Path traversal protection
    safe_path = os.path.normpath(os.path.join(config.PHOTOS_DIR, photo_path))
    if not safe_path.startswith(os.path.normpath(config.PHOTOS_DIR)):
        return jsonify({"error": "Invalid path"}), 403

    # Don't serve from thumbs/ or sync/
    rel = os.path.relpath(safe_path, config.PHOTOS_DIR)
    if rel.startswith("thumbs") or rel.startswith(config.SYNC_DIR_NAME):
        return jsonify({"error": "Invalid path"}), 403

    if not os.path.isfile(safe_path):
        return jsonify({"error": "Not found"}), 404

    directory = os.path.dirname(safe_path)
    filename = os.path.basename(safe_path)
    return send_from_directory(directory, filename)


# ============== MASTER ADMIN ENDPOINTS ==============

@app.route("/admin/sync_children")
@require_admin
def get_sync_children():
    """Return list of registered child frames."""
    return jsonify(db.get_setting("sync_children", []))


@app.route("/admin/sync_add_child", methods=["POST"])
@require_admin
def add_sync_child():
    """Generate a new child token with a label."""
    data = request.get_json()
    label = data.get("label", "").strip()
    if not label:
        return jsonify({"success": False, "error": "Label required"})

    token = secrets_mod.token_urlsafe(16)
    child = {"label": label, "token": token}

    children = db.get_setting("sync_children", [])
    children.append(child)
    db.set_setting("sync_children", children)

    return jsonify({"success": True, "child": child})


@app.route("/admin/sync_remove_child", methods=["POST"])
@require_admin
def remove_sync_child():
    """Remove a child token."""
    data = request.get_json()
    token = data.get("token", "")

    children = db.get_setting("sync_children", [])
    children = [c for c in children if c["token"] != token]
    db.set_setting("sync_children", children)

    return jsonify({"success": True})


@app.route("/sync/delete_photo", methods=["POST"])
def sync_delete_photo():
    """Delete a photo on master. Requires valid token + ownership."""
    if db.get_setting("sync_role") != "master":
        return jsonify({"error": "Not a master"}), 404

    data = request.get_json()
    token = data.get("token", "")
    filename = data.get("filename", "")

    if not token or not filename:
        return jsonify({"success": False, "error": "Token and filename required"})

    # Validate token -> get uploader label
    uploader = None
    if token == db.get_setting("upload_token"):
        uploader = "admin"
    else:
        for child in db.get_setting("sync_children", []):
            if child["token"] == token:
                uploader = child["label"]

    if not uploader:
        return jsonify({"success": False, "error": "Invalid token"}), 403

    # Check upload_meta — did this uploader upload this file?
    meta = db.get_upload_meta()
    if uploader != "admin" and meta.get(filename) != uploader:
        return jsonify({"success": False, "error": "Access denied"}), 403

    # Find and delete the file
    deleted = False
    for subdir in ["upload", "picker", ""]:
        file_path = os.path.join(config.PHOTOS_DIR, subdir, filename) if subdir else os.path.join(config.PHOTOS_DIR, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
            deleted = True
            break

    if not deleted:
        return jsonify({"success": False, "error": "File not found"}), 404

    # Remove thumbnail
    thumb_path = os.path.join(config.PHOTOS_DIR, "thumbs", filename)
    if os.path.exists(thumb_path):
        os.remove(thumb_path)

    # Remove from DB
    db.remove_photo(filename)

    mark_manifest_dirty()

    # Sync USB if needed
    if get_display_mode() == "usb":
        sync_photos_to_usb()

    print(f"[SYNC] Photo {filename} deleted by {uploader}")
    return jsonify({"success": True})


# ============== CHILD ENDPOINTS ==============

@app.route("/admin/sync_now", methods=["POST"])
@require_admin
def trigger_sync_now():
    """Trigger an immediate sync cycle."""
    if db.get_setting("sync_role") != "child":
        return jsonify({"success": False, "error": "Not a child"})
    if db.get_setting("sync_in_progress", False):
        return jsonify({"success": False, "error": "Sync already in progress"})

    t = threading.Thread(target=run_sync_cycle, daemon=True)
    t.start()
    return jsonify({"success": True, "message": "Sync started"})


@app.route("/admin/sync_status")
@require_admin
def sync_status():
    """Return current sync state."""
    return jsonify({
        "sync_role": db.get_setting("sync_role"),
        "master_url": db.get_setting("master_url"),
        "last_sync": db.get_setting("last_sync"),
        "last_sync_result": db.get_setting("last_sync_result"),
        "synced_photo_count": _count_synced_photos(),
        "sync_in_progress": db.get_setting("sync_in_progress", False),
        "sync_error": db.get_setting("sync_error"),
        "sync_interval": db.get_setting("sync_interval", config.DEFAULT_SYNC_INTERVAL),
        "sync_total": db.get_setting("sync_total", 0),
        "sync_completed": db.get_setting("sync_completed", 0),
        "sync_phase": db.get_setting("sync_phase", ""),
        "sync_history": db.get_sync_history(5),
    })


@app.route("/admin/sync_config", methods=["POST"])
@require_admin
def save_sync_config():
    """Save sync configuration (role, master URL, token, interval)."""
    data = request.get_json()
    role = data.get("sync_role", "")

    if role not in ("master", "child", ""):
        return jsonify({"success": False, "error": "Invalid role"})

    old_role = db.get_setting("sync_role")

    if role:
        db.set_setting("sync_role", role)
    else:
        db.delete_setting("sync_role")

    if role == "child":
        master_url = data.get("master_url", "").rstrip("/")
        sync_token = data.get("sync_token", "").strip()
        # Allow partial updates (e.g. just interval) if already configured
        if master_url:
            db.set_setting("master_url", master_url)
        if sync_token:
            db.set_setting("sync_token", sync_token)
        # Require both for initial setup
        if not db.get_setting("master_url") or not db.get_setting("sync_token"):
            return jsonify({"success": False, "error": "Master URL and sync token required"})
        if "sync_interval" in data:
            db.set_setting("sync_interval", max(300, min(7200, int(data["sync_interval"]))))
    elif role == "master":
        # Initialize children list if not present
        if db.get_setting("sync_children") is None:
            db.set_setting("sync_children", [])
        # Clean up child-only keys
        db.delete_setting("master_url")
        db.delete_setting("sync_token")

    # Start/stop/restart sync loop
    if role == "child":
        # Restart loop to pick up any changes (interval, master URL, token)
        start_sync_loop()
    elif old_role == "child" and role != "child":
        stop_sync_loop()

    return jsonify({"success": True})


# ============== SYNC LOGIC ==============

_sync_stop_event = threading.Event()
_sync_thread = None


def _count_synced_photos():
    """Count photos in the sync directory."""
    sync_dir = os.path.join(config.PHOTOS_DIR, config.SYNC_DIR_NAME)
    count = 0
    if os.path.exists(sync_dir):
        for root, dirs, files in os.walk(sync_dir):
            count += len([f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))])
    return count


def _build_local_manifest():
    """Build manifest of photos/sync/ directory for comparison."""
    sync_dir = os.path.join(config.PHOTOS_DIR, config.SYNC_DIR_NAME)
    local = {}
    if os.path.exists(sync_dir):
        for root, dirs, files in os.walk(sync_dir):
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, sync_dir)
                    try:
                        h = hashlib.md5()
                        with open(full_path, 'rb') as fh:
                            for chunk in iter(lambda: fh.read(8192), b''):
                                h.update(chunk)
                        md5 = h.hexdigest()
                    except OSError:
                        continue
                    local[rel_path] = md5
    return local


def run_sync_cycle():
    """Execute one sync cycle: fetch manifest, download new, delete removed."""
    master_url = db.get_setting("master_url")
    sync_token = db.get_setting("sync_token")

    if not master_url or not sync_token:
        print("[SYNC] No master URL or sync token configured")
        return

    db.set_setting("sync_in_progress", True)
    _sync_start_time = time.time()
    print(f"[SYNC] Starting sync from {master_url}")

    try:
        # 1. Fetch master manifest
        resp = requests.get(
            f"{master_url}/sync/manifest",
            params={"token": sync_token},
            timeout=30
        )

        if resp.status_code == 403:
            db.set_setting("sync_error", "Invalid sync token")
            db.set_setting("last_sync_result", "error")
            db.add_sync_log("error",
                            duration_s=round(time.time() - _sync_start_time, 1),
                            error="Invalid sync token")
            print("[SYNC] Invalid sync token (403)")
            return
        elif resp.status_code != 200:
            db.set_setting("sync_error", f"Master returned {resp.status_code}")
            db.set_setting("last_sync_result", "error")
            db.add_sync_log("error",
                            duration_s=round(time.time() - _sync_start_time, 1),
                            error=f"Master returned {resp.status_code}")
            print(f"[SYNC] Master returned {resp.status_code}")
            return

        manifest = resp.json()
        master_photos = {p["path"]: p["md5"] for p in manifest.get("photos", [])}

        # Save upload metadata from master (who uploaded each photo)
        upload_meta = manifest.get("upload_meta", {})
        if upload_meta:
            for fname, uploader in upload_meta.items():
                photo = db.get_photo(fname)
                if photo:
                    # Update uploaded_by on existing record
                    db.add_photo(fname, subdir=photo["subdir"],
                                 uploaded_by=uploader,
                                 size_bytes=photo["size_bytes"],
                                 md5=photo["md5"])

        # Save our own label (so we know which photos are "mine")
        your_label = manifest.get("your_label")
        if your_label:
            db.set_setting("sync_label", your_label)

        # 2. Build local manifest
        local_photos = _build_local_manifest()

        # 3. Diff
        to_download = [
            path for path, md5 in master_photos.items()
            if path not in local_photos or local_photos[path] != md5
        ]
        to_delete = [
            path for path in local_photos
            if path not in master_photos
        ]

        print(f"[SYNC] {len(to_download)} to download, {len(to_delete)} to delete")
        db.set_setting("sync_total", len(to_download))
        db.set_setting("sync_completed", 0)
        db.set_setting("sync_phase", "downloading")

        # 4. Check disk space
        free = shutil.disk_usage("/").free
        if free < 50 * 1024 * 1024 and to_download:
            db.set_setting("sync_error", "Disk full")
            db.set_setting("last_sync_result", "error")
            db.add_sync_log("error",
                            duration_s=round(time.time() - _sync_start_time, 1),
                            error="Disk full")
            print("[SYNC] Disk full, skipping downloads")
            return

        sync_dir = os.path.join(config.PHOTOS_DIR, config.SYNC_DIR_NAME)
        thumb_dir = os.path.join(config.PHOTOS_DIR, "thumbs")
        os.makedirs(sync_dir, exist_ok=True)
        os.makedirs(thumb_dir, exist_ok=True)

        # 5. Download new/changed photos
        downloaded = 0
        for path in to_download:
            try:
                photo_resp = requests.get(
                    f"{master_url}/sync/photo/{path}",
                    params={"token": sync_token},
                    timeout=60
                )
                if photo_resp.status_code != 200:
                    print(f"[SYNC] Failed to download {path}: {photo_resp.status_code}")
                    continue

                # Save photo
                dest = os.path.join(sync_dir, path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as f:
                    f.write(photo_resp.content)

                # Compute md5 of downloaded file
                h = hashlib.md5()
                with open(dest, 'rb') as fh:
                    for chunk in iter(lambda: fh.read(8192), b''):
                        h.update(chunk)
                file_md5 = h.hexdigest()
                file_size = os.path.getsize(dest)

                # Track in DB
                uploader = upload_meta.get(os.path.basename(path), "")
                db.add_photo(os.path.basename(path), subdir="sync",
                             uploaded_by=uploader,
                             size_bytes=file_size, md5=file_md5)

                # Generate thumbnail
                thumb_path = os.path.join(thumb_dir, os.path.basename(path))
                try:
                    img = Image.open(dest)
                    img.thumbnail((200, 200))
                    img.save(thumb_path, "JPEG", quality=60)
                except Exception as e:
                    print(f"[SYNC] Thumbnail failed for {path}: {e}")

                downloaded += 1
                db.set_setting("sync_completed", downloaded)
            except requests.RequestException as e:
                print(f"[SYNC] Download error for {path}: {e}")
                continue

        # 6. Delete removed photos
        db.set_setting("sync_phase", "cleaning")
        deleted = 0
        for path in to_delete:
            full_path = os.path.join(sync_dir, path)
            if os.path.exists(full_path):
                os.remove(full_path)
                # Remove thumbnail
                thumb_path = os.path.join(thumb_dir, os.path.basename(path))
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)
                # Remove from DB
                db.remove_photo(os.path.basename(path))
                deleted += 1

        # Clean up empty subdirectories in sync/
        for root, dirs, files in os.walk(sync_dir, topdown=False):
            if root != sync_dir and not files and not dirs:
                os.rmdir(root)

        # 7. Reconcile state in DB
        _reconcile_after_sync()

        # 8. USB sync if needed
        if get_display_mode() == "usb" and (downloaded > 0 or deleted > 0):
            db.set_setting("sync_phase", "updating_frame")
            sync_photos_to_usb()

        # 9. Update state
        db.set_setting("last_sync", datetime.now().isoformat(timespec="seconds"))
        db.set_setting("last_sync_result", "success")
        db.delete_setting("sync_error")
        db.add_sync_log("success",
                        photos_added=downloaded,
                        photos_removed=deleted,
                        duration_s=round(time.time() - _sync_start_time, 1))

        print(f"[SYNC] Complete: {downloaded} downloaded, {deleted} deleted")

    except requests.RequestException as e:
        db.set_setting("sync_error", str(e))
        db.set_setting("last_sync_result", "error")
        db.add_sync_log("error",
                        duration_s=round(time.time() - _sync_start_time, 1),
                        error=str(e))
        print(f"[SYNC] Network error: {e}")
    except Exception as e:
        db.set_setting("sync_error", str(e))
        db.set_setting("last_sync_result", "error")
        db.add_sync_log("error",
                        duration_s=round(time.time() - _sync_start_time, 1),
                        error=str(e))
        print(f"[SYNC] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.set_setting("sync_in_progress", False)
        db.delete_setting("sync_total")
        db.delete_setting("sync_completed")
        db.delete_setting("sync_phase")


def _reconcile_after_sync():
    """Rebuild photo_urls in DB from disk after sync changes."""
    actual_photos = []
    if os.path.exists(config.PHOTOS_DIR):
        for root, dirs, files in os.walk(config.PHOTOS_DIR):
            dirs[:] = [d for d in dirs if d != 'thumbs']
            for f in sorted(files):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    rel = os.path.relpath(os.path.join(root, f), os.path.dirname(config.PHOTOS_DIR))
                    actual_photos.append(f"/static/{rel}")
    if actual_photos:
        db.set_setting("photo_urls", actual_photos)
        db.set_setting("done", True)
        db.set_setting("photos_chosen", True)
    elif not db.get_setting("photo_urls"):
        db.set_setting("done", False)


def _sync_loop():
    """Background loop that runs sync cycles at configured interval."""
    # Initial delay to let the app finish starting
    if _sync_stop_event.wait(10):
        return

    while not _sync_stop_event.is_set():
        run_sync_cycle()
        interval = db.get_setting("sync_interval", config.DEFAULT_SYNC_INTERVAL)
        if _sync_stop_event.wait(interval):
            break

    print("[SYNC] Sync loop stopped")


def start_sync_loop():
    """Start the background sync loop."""
    global _sync_thread
    stop_sync_loop()
    _sync_stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_loop, daemon=True)
    _sync_thread.start()
    print("[SYNC] Sync loop started")


def stop_sync_loop():
    """Stop the background sync loop."""
    global _sync_thread
    _sync_stop_event.set()
    if _sync_thread and _sync_thread.is_alive():
        _sync_thread.join(timeout=5)
    _sync_thread = None
