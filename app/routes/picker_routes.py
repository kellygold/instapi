# routes/picker_routes.py
import random
import threading
from flask import render_template, jsonify, request, redirect, url_for
from app import app
from config import device_state, PICKER_API_BASE_URL
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
    if "credentials" not in device_state:
        return jsonify({"error": "Not authenticated"}), 401

    headers = {
        "Authorization": f"Bearer {device_state['credentials']['token']}",
        "Content-Type": "application/json",
    }
    payload = {"mediaItemsSet": True}
    url = f"{PICKER_API_BASE_URL}/sessions"
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        picking_session = response.json()
        device_state["picking_session_id"] = picking_session["id"]
        device_state["picker_url"] = picking_session["pickerUri"]

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

        # Redirect to Google's picker
        return redirect(device_state["picker_url"])
    else:
        return f"Failed to create picker session: {response.status_code}", 500


@app.route("/finalize_selection", methods=["POST"])
def finalize_selection():
    """Finalize selection by downloading chosen images from picker."""
    all_photo_urls = []

    # Picker photos
    if device_state.get("picking_session_id"):
        picker_urls = fetch_picker_photos()
        if picker_urls:
            device_state["photos_chosen"] = True
            picker_paths = download_and_return_paths(picker_urls, "picker")
            all_photo_urls.extend(picker_paths)

    # Shuffle photos
    random.shuffle(all_photo_urls)

    device_state["photo_urls"] = all_photo_urls
    device_state["current_index"] = 0
    device_state["done"] = True
    return redirect(url_for("done", _external=True))


@app.route("/done")
def done():
    """User sees success message, or redirect to slideshow if HDMI mode."""
    import os
    
    # Check display mode
    mode = "hdmi"
    mode_paths = [
        os.path.expanduser("~/.display_mode"),
        "/home/instapi/.display_mode"
    ]
    for mode_file in mode_paths:
        if os.path.exists(mode_file):
            with open(mode_file) as f:
                mode = f.read().strip()
            break
    
    # HDMI mode: redirect to slideshow (browser IS the frame)
    # USB mode: show done page (user is on phone, frame is separate)
    if mode == "hdmi" and device_state.get("photo_urls"):
        return redirect(url_for("slideshow"))
    
    return render_template("done.html")


@app.route("/slideshow")
def slideshow():
    """Display the slideshow page on the frame."""
    photo_urls = device_state.get("photo_urls", [])
    indices = list(range(len(photo_urls)))
    return render_template("slideshow.html", media_items=indices)


@app.route("/get_next_photos")
def get_next_photos():
    """Return the next set of photos for the slideshow."""
    if "photo_urls" not in device_state or not device_state["photo_urls"]:
        return jsonify([])

    count_str = request.args.get("count", "1")
    try:
        count = int(count_str)
    except ValueError:
        count = 1

    photo_urls = device_state["photo_urls"]
    current_index = device_state.get("current_index", 0)
    total_photos = len(photo_urls)

    next_photos = []
    for _ in range(count):
        next_photos.append(photo_urls[current_index])
        current_index = (current_index + 1) % total_photos

    device_state["current_index"] = current_index
    return jsonify(next_photos)


@app.route("/check_session_status")
def check_session_status():
    """Check if the frame can start the slideshow."""
    photo_count = len(device_state.get("photo_urls", []))
    if device_state.get("done", False):
        return jsonify({"ready": True, "photo_count": photo_count})
    else:
        return jsonify({"ready": False, "photo_count": photo_count})


@app.route("/user_selection_status")
def user_selection_status():
    """Check if user previously chose photos or albums."""
    return jsonify({
        "photos_chosen": device_state.get("photos_chosen", False),
        "albums_chosen": device_state.get("albums_chosen", False)
    })
