import os
import time
import threading
import requests
import qrcode
from PIL import Image
import db
from config import (
    PHOTOS_DIR,
    PICKER_API_BASE_URL,
    MODE_FILE,
    get_base_url,
)
from photo_ops import generate_thumbnail, compute_md5

def get_upload_url():
    """Build upload page URL with token.
    Child frames point to master's upload page using their sync token.
    Used by both QR watermarks and the choose_mode_qr endpoint."""
    if db.get_setting("sync_role") == "child" and db.get_setting("master_url"):
        token = db.get_setting("sync_token", "")
        return f"{db.get_setting('master_url')}/upload?t={token}"
    token = db.get_setting("upload_token", "")
    return f"{get_base_url()}/upload?t={token}"


MAX_FRAME_RESOLUTION = 1920  # Max dimension for USB frames — cheap frames choke on 24MP images


def add_qr_watermark(image_path, watermark_url=None):
    """Add a small QR code watermark to bottom-right of image.

    Also downsizes images to MAX_FRAME_RESOLUTION to prevent cheap photo
    frames from freezing on high-resolution photos.

    Args:
        watermark_url: URL for the QR code. If None, looks up from DB.
            Pass explicitly when calling from multiprocessing workers
            (which can't access the parent's DB connections).
    """
    try:
        img = Image.open(image_path).convert('RGBA')

        # Downsize for cheap frames — they can't handle 24MP images
        if max(img.width, img.height) > MAX_FRAME_RESOLUTION:
            img.thumbnail((MAX_FRAME_RESOLUTION, MAX_FRAME_RESOLUTION), Image.LANCZOS)

        # Generate small QR linking to auth page
        qr = qrcode.QRCode(box_size=2, border=1)
        qr.add_data(watermark_url or get_upload_url())
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color='black', back_color='white').convert('RGBA')

        # ~10% of longer dimension — scannable on 1280x800 frames
        qr_size = max(img.width, img.height) // 10
        qr_img = qr_img.resize((qr_size, qr_size))

        # Make semi-transparent
        pixels = qr_img.load()
        for y in range(qr_img.height):
            for x in range(qr_img.width):
                r, g, b, a = pixels[x, y]
                if r > 200:  # white background
                    pixels[x, y] = (255, 255, 255, 180)
                else:  # black QR
                    pixels[x, y] = (0, 0, 0, 220)

        # Position bottom-right
        pos = (img.width - qr_size - 15, img.height - qr_size - 15)
        img.paste(qr_img, pos, qr_img)

        # Save back
        img.convert('RGB').save(image_path, 'JPEG', quality=95)
        print(f"Watermark added to {image_path}")
    except Exception as e:
        print(f"Failed to add watermark: {e}")

def parse_time_value(value, default):
    """Parse a time value like '5s' or '1800s' and return an integer in seconds."""
    v = ''.join(ch for ch in value if ch.isdigit())
    try:
        return int(v)
    except ValueError:
        return default

def get_display_mode():
    """Get current display mode from file, with fallback detection."""
    if os.path.exists(MODE_FILE):
        with open(MODE_FILE) as f:
            return f.read().strip()
    # Fallback: if usb-gadget service is installed, this is a USB mode Pi
    if os.path.exists("/etc/systemd/system/usb-gadget.service"):
        return "usb"
    return "hdmi"

def download_and_return_paths(photo_urls, source):
    """Download photos and return their local paths for slideshow usage."""
    creds = db.get_setting("credentials")
    if not creds:
        print("No credentials, cannot download.")
        return []

    headers = {"Authorization": f"Bearer {creds['token']}"}
    subdir = os.path.join(PHOTOS_DIR, source)
    if not os.path.exists(subdir):
        os.makedirs(subdir, exist_ok=True)

    # Check if we should watermark (USB mode only - HDMI has persistent QR overlay)
    display_mode = get_display_mode()
    should_watermark = display_mode == "usb"
    print(f"Display mode: {display_mode}, watermarking: {should_watermark}")

    # Use timestamp prefix so each picker session gets unique filenames
    import time as _time
    batch_id = int(_time.time())

    returned_paths = []
    for i, photo_url in enumerate(photo_urls):
        filename = f"{source}_{batch_id}_{i}.jpg"
        photo_path = os.path.join(subdir, filename)
        if not os.path.exists(photo_path):
            resp = requests.get(photo_url, headers=headers)
            if resp.status_code == 200:
                with open(photo_path, "wb") as img_file:
                    img_file.write(resp.content)
                print(f"{filename} downloaded successfully in {source} folder.")
                # Generate thumbnail for admin gallery
                thumb_dir = os.path.join(PHOTOS_DIR, "thumbs")
                os.makedirs(thumb_dir, exist_ok=True)
                generate_thumbnail(photo_path, os.path.join(thumb_dir, filename))
                # Track in DB
                size = os.path.getsize(photo_path)
                file_md5 = compute_md5(photo_path)
                db.add_photo(filename, subdir=source, uploaded_by='admin',
                             size_bytes=size, md5=file_md5)
                # Add QR watermark only in USB mode (HDMI has persistent overlay)
                if should_watermark:
                    add_qr_watermark(photo_path)
            else:
                print(f"Failed to download {filename}, status code: {resp.status_code}")
        else:
            print(f"{filename} already exists, skipping.")
        db.set_setting("download_completed", i + 1)
        returned_paths.append(f"/static/photos/{source}/{filename}")
    return returned_paths

_usb_sync_lock = threading.Lock()

def sync_photos_to_usb():
    """Run update-photos.sh to sync photos to USB drive (USB mode only).

    Timeout is generous (30 min) because watermarking many photos on
    Pi Zero 2 W is slow (~5s per photo).  291 photos takes ~24 min.

    Thread-safe: uses a lock to prevent concurrent syncs that would corrupt
    the FAT32 filesystem. If a sync is already running, the call is skipped
    (the running sync will pick up all current photos from disk).
    """
    import subprocess

    mode = get_display_mode()
    if mode != "usb":
        print(f"Not USB mode (mode={mode}), skipping USB sync")
        return

    if not _usb_sync_lock.acquire(blocking=False):
        print("[USB] Sync already in progress, skipping (will be picked up by running sync)")
        return

    try:
        script_path = os.path.join(os.path.dirname(__file__), "..", "pi-setup", "update-photos.sh")
        if os.path.exists(script_path):
            print(f"Syncing photos to USB drive via {script_path}...", flush=True)
            try:
                result = subprocess.run(
                    ["/usr/bin/sudo", "/bin/bash", script_path],
                    capture_output=True, text=True, timeout=1800
                )
                print(f"USB sync stdout: {result.stdout[-500:]}")
                if result.stderr:
                    print(f"USB sync stderr: {result.stderr[-500:]}")
                print(f"USB sync exit code: {result.returncode}")
            except subprocess.TimeoutExpired:
                print("USB sync timed out after 1800s!", flush=True)
        else:
            print(f"USB sync script not found: {script_path}")
    finally:
        _usb_sync_lock.release()


def fetch_and_download_picker_photos(session_id):
    """
    Fetch photos from the picker session and download them.
    Called by poll_for_media_items once mediaItemsSet is true.
    """
    creds = db.get_setting("credentials")
    if not creds:
        print("No credentials, cannot download.")
        return

    headers = {
        "Authorization": f"Bearer {creds['token']}",
        "Content-Type": "application/json",
    }
    media_items_url = f"{PICKER_API_BASE_URL}/mediaItems?sessionId={session_id}"
    resp_items = requests.get(media_items_url, headers=headers)
    if resp_items.status_code == 200:
        media_items_data = resp_items.json()
        listed_items = media_items_data.get("mediaItems", [])
        picker_photo_urls = []
        for item in listed_items:
            if "mediaFile" in item and "baseUrl" in item["mediaFile"]:
                # Request a large-enough resolution
                picker_photo_urls.append(item["mediaFile"]["baseUrl"] + "=w2048-h1024")

        # Track download progress for admin UI
        db.set_setting("downloading", True)
        db.set_setting("download_total", len(picker_photo_urls))
        db.set_setting("download_completed", 0)

        download_and_return_paths(picker_photo_urls, "picker")

        db.set_setting("downloading", False)

        # Notify: sets done/photos_chosen, triggers USB sync if needed
        from photo_ops import notify_photos_changed
        notify_photos_changed()
    else:
        print("Failed to list media items from picker:", resp_items.status_code, resp_items.text)

def fetch_picker_photos():
    """
    If needed, fetch media items from the Photo Picker session in a single shot.
    Used by finalize_selection if the user never triggered the poller or we want a re-fetch.
    """
    session_id = db.get_setting("picking_session_id")
    if not session_id:
        return []
    creds = db.get_setting("credentials")
    if not creds:
        return []
    headers = {
        "Authorization": f"Bearer {creds['token']}",
        "Content-Type": "application/json",
    }
    session_url = f"{PICKER_API_BASE_URL}/sessions/{session_id}"
    resp = requests.get(session_url, headers=headers)
    if resp.status_code == 200:
        session_data = resp.json()
        if session_data.get("mediaItemsSet"):
            media_items_url = f"{PICKER_API_BASE_URL}/mediaItems?sessionId={session_id}"
            resp_items = requests.get(media_items_url, headers=headers)
            if resp_items.status_code == 200:
                media_items_data = resp_items.json()
                listed_items = media_items_data.get("mediaItems", [])
                picker_photo_urls = []
                for item in listed_items:
                    if "mediaFile" in item and "baseUrl" in item["mediaFile"]:
                        picker_photo_urls.append(item["mediaFile"]["baseUrl"] + "=w2048-h1024")
                return picker_photo_urls
    return []

def poll_for_media_items(poll_interval, poll_timeout):
    """Poll the picker session until media items are set, timeout, or auth error."""
    session_id = db.get_setting("picking_session_id")
    if not session_id:
        print("[POLL] No session ID found for polling.")
        return

    creds = db.get_setting("credentials")
    if not creds:
        print("[POLL] No credentials for polling.")
        return

    headers = {"Authorization": f"Bearer {creds['token']}"}
    start_time = time.time()
    error_count = 0
    max_errors = 3  # Stop after 3 consecutive errors

    print(f"[POLL] Starting polling for session {session_id[:20]}... (timeout: {poll_timeout}s)")

    while time.time() - start_time < poll_timeout:
        time.sleep(poll_interval)
        url = f"{PICKER_API_BASE_URL}/sessions/{session_id}"

        try:
            resp = requests.get(url, headers=headers, timeout=10)
        except Exception as e:
            print(f"[POLL] Request exception: {e}")
            error_count += 1
            if error_count >= max_errors:
                print("[POLL] Too many errors, stopping polling.")
                break
            continue

        if resp.status_code == 200:
            error_count = 0  # Reset on success
            session_data = resp.json()
            if session_data.get("mediaItemsSet"):
                print("[POLL] Media items set! Downloading photos...")
                fetch_and_download_picker_photos(session_id)
                db.set_setting("photos_chosen", True)
                db.set_setting("done", True)
                print("[POLL] Photos downloaded. Polling complete.")
                return  # Success - stop polling
        elif resp.status_code == 401:
            print("[POLL] Token expired (401). Stopping polling.")
            return  # Auth expired - stop polling
        elif resp.status_code == 404:
            print("[POLL] Session not found (404). Stopping polling.")
            return  # Session gone - stop polling
        else:
            print(f"[POLL] Error {resp.status_code}, will retry...")
            error_count += 1
            if error_count >= max_errors:
                print("[POLL] Too many errors, stopping polling.")
                return

    print("[POLL] Timeout reached, stopping polling.")
