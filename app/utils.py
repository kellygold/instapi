import os
import time
import json
import requests
import qrcode
from PIL import Image
from config import (
    device_state,
    PHOTOS_DIR,
    PICKER_API_BASE_URL
)

# Load base URL for watermark QR
with open("secrets.json") as f:
    _secrets = json.load(f)
    _redirect = _secrets["web"]["redirect_uris"][0]
    WATERMARK_URL = _redirect.rsplit("/", 1)[0]  # Base URL without path


def add_qr_watermark(image_path):
    """Add a small QR code watermark to bottom-right of image."""
    try:
        img = Image.open(image_path).convert('RGBA')
        
        # Generate small QR linking to auth page
        qr = qrcode.QRCode(box_size=2, border=1)
        qr.add_data(WATERMARK_URL)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color='black', back_color='white').convert('RGBA')
        
        # Scale QR - about 4% of width, min 50px
        qr_size = max(50, img.width // 20)
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
    """Get current display mode from file."""
    mode_paths = [
        os.path.expanduser("~/.display_mode"),
        "/home/instapi/.display_mode",
        os.path.join(os.path.dirname(__file__), "..", ".display_mode")
    ]
    for mode_file in mode_paths:
        if os.path.exists(mode_file):
            with open(mode_file) as f:
                return f.read().strip()
    return "hdmi"  # default

def download_and_return_paths(photo_urls, source):
    """Download photos and return their local paths for slideshow usage."""
    if "credentials" not in device_state:
        print("No credentials, cannot download.")
        return []

    headers = {"Authorization": f"Bearer {device_state['credentials']['token']}"}
    subdir = os.path.join(PHOTOS_DIR, source)
    if not os.path.exists(subdir):
        os.makedirs(subdir, exist_ok=True)

    # Check if we should watermark (USB mode only - HDMI has persistent QR overlay)
    display_mode = get_display_mode()
    should_watermark = display_mode == "usb"
    print(f"Display mode: {display_mode}, watermarking: {should_watermark}")

    returned_paths = []
    for i, photo_url in enumerate(photo_urls):
        filename = f"{source}_{i}.jpg"
        photo_path = os.path.join(subdir, filename)
        if not os.path.exists(photo_path):
            resp = requests.get(photo_url, headers=headers)
            if resp.status_code == 200:
                with open(photo_path, "wb") as img_file:
                    img_file.write(resp.content)
                print(f"{filename} downloaded successfully in {source} folder.")
                # Add QR watermark only in USB mode (HDMI has persistent overlay)
                if should_watermark:
                    add_qr_watermark(photo_path)
            else:
                print(f"Failed to download {filename}, status code: {resp.status_code}")
        else:
            print(f"{filename} already exists, skipping.")
        returned_paths.append(f"/static/photos/{source}/{filename}")
    return returned_paths

def sync_photos_to_usb():
    """Run update-photos.sh to sync photos to USB drive (USB mode only)."""
    import subprocess
    
    mode = get_display_mode()
    if mode == "usb":
        script_path = os.path.join(os.path.dirname(__file__), "..", "pi-setup", "update-photos.sh")
        if os.path.exists(script_path):
            print(f"Syncing photos to USB drive via {script_path}...")
            result = subprocess.run(
                ["/usr/bin/sudo", "/bin/bash", script_path],
                capture_output=True, text=True, timeout=120
            )
            print(f"USB sync stdout: {result.stdout}")
            if result.stderr:
                print(f"USB sync stderr: {result.stderr}")
            print(f"USB sync exit code: {result.returncode}")
        else:
            print(f"USB sync script not found: {script_path}")
    else:
        print(f"Not USB mode (mode={mode}), skipping USB sync")


def fetch_and_download_picker_photos(session_id):
    """
    Fetch photos from the picker session and download them.
    Called by poll_for_media_items once mediaItemsSet is true.
    """
    if "credentials" not in device_state:
        print("No credentials, cannot download.")
        return

    headers = {
        "Authorization": f"Bearer {device_state['credentials']['token']}",
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

        picker_paths = download_and_return_paths(picker_photo_urls, "picker")

        photo_list = device_state.get("photo_urls", [])
        photo_list.extend(picker_paths)
        device_state["photo_urls"] = photo_list
        
        # Sync to USB if in USB mode
        sync_photos_to_usb()
    else:
        print("Failed to list media items from picker:", resp_items.status_code, resp_items.text)

def fetch_picker_photos():
    """
    If needed, fetch media items from the Photo Picker session in a single shot.
    Used by finalize_selection if the user never triggered the poller or we want a re-fetch.
    """
    session_id = device_state.get("picking_session_id")
    if not session_id:
        return []
    headers = {
        "Authorization": f"Bearer {device_state['credentials']['token']}",
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
    session_id = device_state.get("picking_session_id")
    if not session_id:
        print("[POLL] No session ID found for polling.")
        return

    headers = {"Authorization": f"Bearer {device_state['credentials']['token']}"}
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
                device_state["photos_chosen"] = True
                device_state["done"] = True
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

