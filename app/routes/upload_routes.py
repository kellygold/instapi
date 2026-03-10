# routes/upload_routes.py
import os
import time
import shutil
from flask import render_template, jsonify, request
from PIL import Image
from app import app
from config import device_state, PHOTOS_DIR, save_device_state
from utils import sync_photos_to_usb, get_display_mode

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _validate_token():
    """Check upload token from query param or form data."""
    token = request.args.get("t") or request.form.get("t")
    expected = device_state.get("upload_token")
    if not expected or token != expected:
        return False
    return True


@app.route("/upload")
def upload_page():
    """Upload page for family photo sharing."""
    if not _validate_token():
        return render_template("upload_error.html"), 403
    return render_template("upload.html", token=request.args.get("t", ""))


@app.route("/upload", methods=["POST"])
def upload_photos():
    """Handle photo uploads."""
    if not _validate_token():
        return jsonify({"success": False, "error": "Invalid token"}), 403

    files = request.files.getlist("photos")
    if not files:
        return jsonify({"success": False, "error": "No files uploaded"})

    # Check disk space
    free = shutil.disk_usage("/").free
    if free < 50 * 1024 * 1024:
        return jsonify({"success": False, "error": "Storage full. Delete some photos first."})

    subdir = os.path.join(PHOTOS_DIR, "upload")
    os.makedirs(subdir, exist_ok=True)
    thumb_dir = os.path.join(PHOTOS_DIR, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)

    batch_id = int(time.time())
    uploaded = 0
    skipped = 0

    for i, file in enumerate(files):
        if not file or not file.filename:
            continue

        # Validate file size
        file.seek(0, 2)
        size = file.tell()
        file.seek(0)
        if size > MAX_FILE_SIZE:
            skipped += 1
            print(f"[UPLOAD] Skipped {file.filename}: too large ({size} bytes)")
            continue

        # Validate it's an image
        try:
            img = Image.open(file)
            img.verify()
            file.seek(0)
            img = Image.open(file)
        except Exception:
            skipped += 1
            print(f"[UPLOAD] Skipped {file.filename}: not a valid image")
            continue

        filename = f"upload_{batch_id}_{i}.jpg"
        photo_path = os.path.join(subdir, filename)

        # Save as JPEG
        img = img.convert("RGB")
        img.save(photo_path, "JPEG", quality=85)

        # Generate thumbnail
        thumb_path = os.path.join(thumb_dir, filename)
        thumb_img = Image.open(photo_path)
        thumb_img.thumbnail((200, 200))
        thumb_img.save(thumb_path, "JPEG", quality=60)

        # Add to device state
        url_path = f"/static/photos/upload/{filename}"
        device_state.setdefault("photo_urls", []).append(url_path)
        uploaded += 1

    if uploaded > 0:
        device_state["done"] = True
        device_state["photos_chosen"] = True
        save_device_state()

        # Sync to USB if in USB mode
        if get_display_mode() == "usb":
            sync_photos_to_usb()

    return jsonify({"success": True, "count": uploaded, "skipped": skipped})
