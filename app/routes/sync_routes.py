# routes/sync_routes.py
import os
import hashlib
import time
import threading
import shutil
import secrets as secrets_mod
import requests
from flask import jsonify, request, send_from_directory
from PIL import Image
from app import app
import config
from config import device_state, save_device_state
from utils import sync_photos_to_usb, get_display_mode

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
                        md5 = hashlib.md5(open(full_path, 'rb').read()).hexdigest()
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
    children = device_state.get("sync_children", [])
    return any(c["token"] == token for c in children)


# ============== MASTER ENDPOINTS ==============

@app.route("/sync/manifest")
def sync_manifest():
    """Serve photo manifest for child Pis to sync from."""
    if device_state.get("sync_role") != "master":
        return jsonify({"error": "Not a master"}), 404

    token = request.args.get("token", "")
    if not _validate_sync_token(token):
        return jsonify({"error": "Invalid token"}), 403

    return jsonify(_get_manifest())


@app.route("/sync/photo/<path:photo_path>")
def sync_photo(photo_path):
    """Serve a single photo file for child download."""
    if device_state.get("sync_role") != "master":
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
def get_sync_children():
    """Return list of registered child frames."""
    return jsonify(device_state.get("sync_children", []))


@app.route("/admin/sync_add_child", methods=["POST"])
def add_sync_child():
    """Generate a new child token with a label."""
    data = request.get_json()
    label = data.get("label", "").strip()
    if not label:
        return jsonify({"success": False, "error": "Label required"})

    token = secrets_mod.token_urlsafe(16)
    child = {"label": label, "token": token}

    children = device_state.setdefault("sync_children", [])
    children.append(child)
    save_device_state()

    return jsonify({"success": True, "child": child})


@app.route("/admin/sync_remove_child", methods=["POST"])
def remove_sync_child():
    """Remove a child token."""
    data = request.get_json()
    token = data.get("token", "")

    children = device_state.get("sync_children", [])
    device_state["sync_children"] = [c for c in children if c["token"] != token]
    save_device_state()

    return jsonify({"success": True})


# ============== CHILD ENDPOINTS ==============

@app.route("/admin/sync_now", methods=["POST"])
def trigger_sync_now():
    """Trigger an immediate sync cycle."""
    if device_state.get("sync_role") != "child":
        return jsonify({"success": False, "error": "Not a child"})
    if device_state.get("sync_in_progress"):
        return jsonify({"success": False, "error": "Sync already in progress"})

    t = threading.Thread(target=run_sync_cycle, daemon=True)
    t.start()
    return jsonify({"success": True, "message": "Sync started"})


@app.route("/admin/sync_status")
def sync_status():
    """Return current sync state."""
    return jsonify({
        "sync_role": device_state.get("sync_role"),
        "master_url": device_state.get("master_url"),
        "last_sync": device_state.get("last_sync"),
        "last_sync_result": device_state.get("last_sync_result"),
        "synced_photo_count": _count_synced_photos(),
        "sync_in_progress": device_state.get("sync_in_progress", False),
        "sync_error": device_state.get("sync_error"),
        "sync_interval": device_state.get("sync_interval", config.DEFAULT_SYNC_INTERVAL),
        "sync_total": device_state.get("sync_total", 0),
        "sync_completed": device_state.get("sync_completed", 0),
        "sync_phase": device_state.get("sync_phase", ""),
    })


@app.route("/admin/sync_config", methods=["POST"])
def save_sync_config():
    """Save sync configuration (role, master URL, token, interval)."""
    data = request.get_json()
    role = data.get("sync_role", "")

    if role not in ("master", "child", ""):
        return jsonify({"success": False, "error": "Invalid role"})

    old_role = device_state.get("sync_role")

    if role:
        device_state["sync_role"] = role
    else:
        device_state.pop("sync_role", None)

    if role == "child":
        master_url = data.get("master_url", "").rstrip("/")
        sync_token = data.get("sync_token", "").strip()
        # Allow partial updates (e.g. just interval) if already configured
        if master_url:
            device_state["master_url"] = master_url
        if sync_token:
            device_state["sync_token"] = sync_token
        # Require both for initial setup
        if not device_state.get("master_url") or not device_state.get("sync_token"):
            return jsonify({"success": False, "error": "Master URL and sync token required"})
        if "sync_interval" in data:
            device_state["sync_interval"] = max(300, min(7200, int(data["sync_interval"])))
    elif role == "master":
        # Initialize children list if not present
        device_state.setdefault("sync_children", [])
        # Clean up child-only keys
        device_state.pop("master_url", None)
        device_state.pop("sync_token", None)

    save_device_state()

    # Start/stop sync loop based on role change
    if role == "child" and old_role != "child":
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
                        md5 = hashlib.md5(open(full_path, 'rb').read()).hexdigest()
                    except OSError:
                        continue
                    local[rel_path] = md5
    return local


def run_sync_cycle():
    """Execute one sync cycle: fetch manifest, download new, delete removed."""
    master_url = device_state.get("master_url")
    sync_token = device_state.get("sync_token")

    if not master_url or not sync_token:
        print("[SYNC] No master URL or sync token configured")
        return

    device_state["sync_in_progress"] = True
    print(f"[SYNC] Starting sync from {master_url}")

    try:
        # 1. Fetch master manifest
        resp = requests.get(
            f"{master_url}/sync/manifest",
            params={"token": sync_token},
            timeout=30
        )

        if resp.status_code == 403:
            device_state["sync_error"] = "Invalid sync token"
            device_state["last_sync_result"] = "error"
            print("[SYNC] Invalid sync token (403)")
            return
        elif resp.status_code != 200:
            device_state["sync_error"] = f"Master returned {resp.status_code}"
            device_state["last_sync_result"] = "error"
            print(f"[SYNC] Master returned {resp.status_code}")
            return

        manifest = resp.json()
        master_photos = {p["path"]: p["md5"] for p in manifest.get("photos", [])}

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
        device_state["sync_total"] = len(to_download)
        device_state["sync_completed"] = 0
        device_state["sync_phase"] = "downloading"

        # 4. Check disk space
        free = shutil.disk_usage("/").free
        if free < 50 * 1024 * 1024 and to_download:
            device_state["sync_error"] = "Disk full"
            device_state["last_sync_result"] = "error"
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

                # Generate thumbnail
                thumb_path = os.path.join(thumb_dir, os.path.basename(path))
                try:
                    img = Image.open(dest)
                    img.thumbnail((200, 200))
                    img.save(thumb_path, "JPEG", quality=60)
                except Exception as e:
                    print(f"[SYNC] Thumbnail failed for {path}: {e}")

                # Add QR watermark in USB mode (points to master upload page)
                if get_display_mode() == "usb":
                    try:
                        from utils import add_qr_watermark
                        add_qr_watermark(dest)
                    except Exception as e:
                        print(f"[SYNC] Watermark failed for {path}: {e}")

                downloaded += 1
                device_state["sync_completed"] = downloaded
            except requests.RequestException as e:
                print(f"[SYNC] Download error for {path}: {e}")
                continue

        # 6. Delete removed photos
        device_state["sync_phase"] = "cleaning"
        deleted = 0
        for path in to_delete:
            full_path = os.path.join(sync_dir, path)
            if os.path.exists(full_path):
                os.remove(full_path)
                # Remove thumbnail
                thumb_path = os.path.join(thumb_dir, os.path.basename(path))
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)
                deleted += 1

        # Clean up empty subdirectories in sync/
        for root, dirs, files in os.walk(sync_dir, topdown=False):
            if root != sync_dir and not files and not dirs:
                os.rmdir(root)

        # 7. Rebuild photo_urls from disk (reconcile)
        _reconcile_after_sync()

        # 8. USB sync if needed
        if get_display_mode() == "usb" and (downloaded > 0 or deleted > 0):
            device_state["sync_phase"] = "updating_frame"
            sync_photos_to_usb()

        # 9. Update state
        from datetime import datetime
        device_state["last_sync"] = datetime.now().isoformat(timespec="seconds")
        device_state["last_sync_result"] = "success"
        device_state.pop("sync_error", None)
        save_device_state()

        print(f"[SYNC] Complete: {downloaded} downloaded, {deleted} deleted")

    except requests.RequestException as e:
        device_state["sync_error"] = str(e)
        device_state["last_sync_result"] = "error"
        print(f"[SYNC] Network error: {e}")
    except Exception as e:
        device_state["sync_error"] = str(e)
        device_state["last_sync_result"] = "error"
        print(f"[SYNC] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        device_state["sync_in_progress"] = False
        device_state.pop("sync_total", None)
        device_state.pop("sync_completed", None)
        device_state.pop("sync_phase", None)


def _reconcile_after_sync():
    """Rebuild photo_urls from disk after sync changes."""
    actual_photos = []
    if os.path.exists(config.PHOTOS_DIR):
        for root, dirs, files in os.walk(config.PHOTOS_DIR):
            dirs[:] = [d for d in dirs if d != 'thumbs']
            for f in sorted(files):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    rel = os.path.relpath(os.path.join(root, f), os.path.dirname(config.PHOTOS_DIR))
                    actual_photos.append(f"/static/{rel}")
    if actual_photos:
        device_state["photo_urls"] = actual_photos
        device_state["done"] = True
        device_state["photos_chosen"] = True
    elif not device_state.get("photo_urls"):
        device_state["done"] = False


def _sync_loop():
    """Background loop that runs sync cycles at configured interval."""
    # Initial delay to let the app finish starting
    if _sync_stop_event.wait(10):
        return

    while not _sync_stop_event.is_set():
        run_sync_cycle()
        interval = device_state.get("sync_interval", config.DEFAULT_SYNC_INTERVAL)
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
