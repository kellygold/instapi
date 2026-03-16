# routes/picker_routes.py
import os
import random
import threading
from flask import render_template, jsonify, request, redirect, url_for
from app import app
import config
import db
from routes.sync_routes import mark_manifest_dirty
from photo_ops import compute_md5, notify_photos_changed
from utils import (
    parse_time_value,
    poll_for_media_items,
    fetch_picker_photos,
    download_and_return_paths
)
import requests

@app.route("/launch_picker")
def launch_picker():
    """Create a Photo Picker session and start polling."""
    if not db.get_setting("credentials"):
        return jsonify({"error": "Not authenticated"}), 401

    credentials = db.get_setting("credentials")
    headers = {
        "Authorization": f"Bearer {credentials['token']}",
        "Content-Type": "application/json",
    }
    payload = {"mediaItemsSet": True}
    url = f"{config.PICKER_API_BASE_URL}/sessions"
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        picking_session = response.json()
        db.set_setting("picking_session_id", picking_session["id"])
        db.set_setting("picker_url", picking_session["pickerUri"])

        polling_config = picking_session.get("pollingConfig", {})
        poll_interval_str = polling_config.get("pollInterval", "5s")
        poll_timeout_str = polling_config.get("timeoutIn", "1800s")

        poll_interval = parse_time_value(poll_interval_str, 5)
        poll_timeout = parse_time_value(poll_timeout_str, 1800)

        thread = threading.Thread(
            target=poll_for_media_items,
            args=(poll_interval, poll_timeout),
            daemon=True
        )
        thread.start()

        # Redirect directly to picker with /autoclose so it closes when done
        picker_url = db.get_setting("picker_url")
        return redirect(picker_url + "/autoclose")
    else:
        return f"Failed to create picker session: {response.status_code}", 500


@app.route("/finalize_selection", methods=["POST"])
def finalize_selection():
    """Finalize selection by downloading chosen images from picker."""
    all_photo_urls = []

    # Picker photos
    if db.get_setting("picking_session_id"):
        picker_urls = fetch_picker_photos()
        if picker_urls:
            db.set_setting("photos_chosen", True)
            picker_paths = download_and_return_paths(picker_urls, "picker")
            all_photo_urls.extend(picker_paths)

            # Track downloaded picker photos in DB
            for url_path in picker_paths:
                filename = os.path.basename(url_path)
                photo_path = os.path.join(config.PHOTOS_DIR, "picker", filename)
                file_size = 0
                file_md5 = ""
                if os.path.isfile(photo_path):
                    file_size = os.path.getsize(photo_path)
                    file_md5 = compute_md5(photo_path)
                db.add_photo(filename, subdir="picker", uploaded_by="picker",
                             size_bytes=file_size, md5=file_md5)

    # Shuffle photos
    random.shuffle(all_photo_urls)

    db.set_setting("current_index", 0)
    db.set_setting("done", True)
    mark_manifest_dirty()
    notify_photos_changed()
    return redirect(url_for("done", _external=True))


@app.route("/done")
def done():
    """Redirect to admin panel after photo selection."""
    return redirect(url_for("admin"))


@app.route("/slideshow")
def slideshow():
    """Display the slideshow page on the frame."""
    photo_urls = db.get_photo_urls()
    indices = list(range(len(photo_urls)))
    return render_template("slideshow.html", media_items=indices)


@app.route("/get_next_photos")
def get_next_photos():
    """Return the next set of photos for the slideshow."""
    import random

    photo_urls = db.get_photo_urls()
    if not photo_urls:
        return jsonify([])

    count_str = request.args.get("count", "1")
    shuffle = request.args.get("shuffle", "0") == "1"

    try:
        count = int(count_str)
    except ValueError:
        count = 1

    total_photos = len(photo_urls)

    if shuffle:
        next_photos = [random.choice(photo_urls) for _ in range(count)]
    else:
        current_index = db.get_setting("current_index", 0) % total_photos
        next_photos = []
        for _ in range(count):
            next_photos.append(photo_urls[current_index])
            current_index = (current_index + 1) % total_photos
        db.set_setting("current_index", current_index)

    return jsonify(next_photos)


@app.route("/check_session_status")
def check_session_status():
    """Check if the frame can start the slideshow."""
    photo_count = db.get_photo_count()
    if db.get_setting("done", False):
        return jsonify({"ready": True, "photo_count": photo_count})
    else:
        return jsonify({"ready": False, "photo_count": photo_count})
