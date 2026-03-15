# routes/upload_routes.py
import os
import gc
import time
import hashlib
import shutil
import threading
from flask import render_template, jsonify, request
from PIL import Image, ImageOps
from app import app
import config
import db
from utils import sync_photos_to_usb, get_display_mode
from routes.sync_routes import mark_manifest_dirty

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
STAGING_DIR = os.path.join(config.PHOTOS_DIR, ".staging")


def _validate_token():
    """Check upload token from query param or form data.
    Returns uploader label string if valid, False if invalid.
    Master's own upload_token returns 'admin'."""
    token = request.args.get("t") or request.form.get("t")
    if not token:
        return False
    # Check master's own upload token
    if token == db.get_setting("upload_token"):
        return "admin"
    # Check sync child tokens (each child's token doubles as their upload identity)
    for child in db.get_setting("sync_children", []):
        if child["token"] == token:
            return child["label"]
    return False


@app.route("/upload")
def upload_page():
    """Upload page for family photo sharing."""
    uploader = _validate_token()
    if not uploader:
        return render_template("upload_error.html"), 403
    return render_template("upload.html", token=request.args.get("t", ""))


@app.route("/upload", methods=["POST"])
def upload_photos():
    """Save uploaded files to staging, process in background."""
    uploader = _validate_token()
    if not uploader:
        return jsonify({"success": False, "error": "Invalid token"}), 403

    files = request.files.getlist("photos")
    if not files:
        return jsonify({"success": False, "error": "No files uploaded"})

    # Check disk space
    free = shutil.disk_usage("/").free
    if free < 50 * 1024 * 1024:
        return jsonify({"success": False, "error": "Storage full. Delete some photos first."})

    # Save raw files to staging dir (fast, no processing)
    os.makedirs(STAGING_DIR, exist_ok=True)
    batch_id = int(time.time())
    staged = []
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

        # Save raw file to staging (no PIL processing yet)
        staging_name = f"stage_{batch_id}_{i}_{file.filename}"
        staging_path = os.path.join(STAGING_DIR, staging_name)
        file.save(staging_path)
        staged.append((staging_path, f"upload_{batch_id}_{i}.jpg"))

    if not staged:
        return jsonify({"success": False, "error": "No valid files"})

    # Track processing progress
    db.set_setting("upload_processing", True)
    db.set_setting("upload_total", len(staged))
    db.set_setting("upload_processed", 0)

    # Process in background thread (prevents OOM from processing all at once)
    t = threading.Thread(
        target=_process_staged_uploads,
        args=(staged, uploader),
        daemon=True
    )
    t.start()

    return jsonify({
        "success": True,
        "count": len(staged),
        "skipped": skipped,
        "processing": True
    })


def _process_staged_uploads(staged_files, uploader):
    """Process staged uploads one at a time in background thread."""
    subdir = os.path.join(config.PHOTOS_DIR, "upload")
    os.makedirs(subdir, exist_ok=True)
    thumb_dir = os.path.join(config.PHOTOS_DIR, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)

    processed_filenames = []

    for idx, (staging_path, filename) in enumerate(staged_files):
        try:
            photo_path = os.path.join(subdir, filename)

            # Open, validate, fix rotation, save as JPEG
            img = Image.open(staging_path)
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.save(photo_path, "JPEG", quality=85)
            del img

            # Generate thumbnail
            thumb_img = Image.open(photo_path)
            thumb_img.thumbnail((200, 200))
            thumb_img.save(os.path.join(thumb_dir, filename), "JPEG", quality=60)
            del thumb_img

            # Compute md5 and size for DB
            file_size = os.path.getsize(photo_path)
            h = hashlib.md5()
            with open(photo_path, 'rb') as fh:
                for chunk in iter(lambda: fh.read(8192), b''):
                    h.update(chunk)
            file_md5 = h.hexdigest()

            # Track in DB
            db.add_photo(filename, subdir="upload", uploaded_by=uploader,
                         size_bytes=file_size, md5=file_md5)

            processed_filenames.append(filename)
            print(f"[UPLOAD] Processed {idx + 1}/{len(staged_files)}: {filename}")

        except Exception as e:
            print(f"[UPLOAD] Failed to process {staging_path}: {e}")
        finally:
            # Clean up staging file
            try:
                os.remove(staging_path)
            except OSError:
                pass

            db.set_setting("upload_processed", idx + 1)

            # Free memory every 5 files
            if (idx + 1) % 5 == 0:
                gc.collect()

    if processed_filenames:
        db.set_setting("done", True)
        db.set_setting("photos_chosen", True)
        mark_manifest_dirty()

        # Sync to USB if in USB mode
        if get_display_mode() == "usb":
            sync_photos_to_usb()

    # Clear processing state
    db.delete_setting("upload_processing")
    db.delete_setting("upload_total")
    db.delete_setting("upload_processed")

    # Clean up staging dir
    try:
        os.rmdir(STAGING_DIR)
    except OSError:
        pass

    print(f"[UPLOAD] Done: {len(processed_filenames)} processed")


@app.route("/upload/status")
def upload_status():
    """Return upload processing progress."""
    return jsonify({
        "processing": db.get_setting("upload_processing", False),
        "total": db.get_setting("upload_total", 0),
        "processed": db.get_setting("upload_processed", 0),
    })
