# album_sync.py — Google Photos Library API album sync
import os
import sys
import json
import time
import threading
import requests
from PIL import Image
from config import (
    device_state, save_device_state, PHOTOS_DIR,
    LIBRARY_API_BASE_URL
)
from utils import sync_photos_to_usb

def _log(msg):
    print(msg, flush=True, file=sys.stderr)

ALBUM_NAME = "InstaPi Frame"
SYNC_INTERVAL = 30 * 60  # 30 minutes

# Load client credentials for token refresh
with open("secrets.json") as f:
    _secrets = json.load(f)
    _CLIENT_ID = _secrets["web"]["client_id"]
    _CLIENT_SECRET = _secrets["web"]["client_secret"]


def _get_access_token():
    """Get a valid access token, refreshing if needed."""
    # Try existing token first
    creds = device_state.get("credentials", {})
    if creds.get("token"):
        return creds["token"]

    # Refresh using stored refresh token
    refresh_token = device_state.get("refresh_token")
    if not refresh_token:
        return None

    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    if resp.status_code == 200:
        data = resp.json()
        device_state["credentials"] = {"token": data["access_token"]}
        _log(f"[SYNC] Token refreshed successfully")
        return data["access_token"]
    else:
        _log(f"[SYNC] Token refresh failed: {resp.status_code} {resp.text}")
        return None


def _api_get(path, token, params=None):
    """Make authenticated GET request to Library API."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{LIBRARY_API_BASE_URL}{path}", headers=headers, params=params)
    return resp


def _api_post(path, token, json_body=None):
    """Make authenticated POST request to Library API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(f"{LIBRARY_API_BASE_URL}{path}", headers=headers, json=json_body)
    return resp


def create_album(token):
    """Create the InstaPi album if it doesn't exist. Returns album ID."""
    # Check if we already have an album ID
    album_id = device_state.get("album_id")
    if album_id:
        # Verify it still exists
        resp = _api_get(f"/albums/{album_id}", token)
        if resp.status_code == 200:
            _log(f"[SYNC] Album exists: {album_id}")
            return album_id
        _log(f"[SYNC] Stored album not found, creating new one")

    # Create new album
    resp = _api_post("/albums", token, {"album": {"title": ALBUM_NAME}})
    if resp.status_code == 200:
        album_id = resp.json()["id"]
        device_state["album_id"] = album_id
        save_device_state()
        _log(f"[SYNC] Created album '{ALBUM_NAME}': {album_id}")
        return album_id
    else:
        _log(f"[SYNC] Failed to create album: {resp.status_code} {resp.text}")
        return None


def list_album_items(token, album_id):
    """List all media items in the album. Returns list of {id, baseUrl, filename}."""
    items = []
    page_token = None

    while True:
        body = {"albumId": album_id, "pageSize": 100}
        if page_token:
            body["pageToken"] = page_token

        resp = _api_post("/mediaItems:search", token, body)
        if resp.status_code != 200:
            _log(f"[SYNC] Failed to list album items: {resp.status_code} {resp.text}")
            break

        data = resp.json()
        for item in data.get("mediaItems", []):
            items.append({
                "id": item["id"],
                "baseUrl": item["baseUrl"],
                "filename": item.get("filename", f"{item['id']}.jpg"),
            })

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return items


def sync_album():
    """Main sync function — downloads new photos, removes deleted ones."""
    token = _get_access_token()
    if not token:
        _log("[SYNC] No valid token, skipping sync")
        return

    # Check disk space
    import shutil as _shutil
    free = _shutil.disk_usage("/").free
    if free < 50 * 1024 * 1024:
        _log("[SYNC] Disk space low (<50MB), skipping download")
        return

    album_id = device_state.get("album_id")
    if not album_id:
        album_id = create_album(token)
        if not album_id:
            return

    _log(f"[SYNC] Syncing album {album_id}...")
    items = list_album_items(token, album_id)
    _log(f"[SYNC] Found {len(items)} items in album")

    # Track which media IDs we've synced
    synced = set(device_state.get("synced_media_ids", []))
    current_ids = {item["id"] for item in items}

    # Download new photos
    subdir = os.path.join(PHOTOS_DIR, "album")
    os.makedirs(subdir, exist_ok=True)
    thumb_dir = os.path.join(PHOTOS_DIR, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)

    new_count = 0
    for item in items:
        if item["id"] in synced:
            continue

        # Download photo at good resolution
        photo_url = item["baseUrl"] + "=w2048-h1024"
        filename = f"album_{item['id'][:16]}.jpg"
        photo_path = os.path.join(subdir, filename)

        try:
            resp = requests.get(photo_url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                with open(photo_path, "wb") as f:
                    f.write(resp.content)

                # Generate thumbnail
                thumb_path = os.path.join(thumb_dir, filename)
                img = Image.open(photo_path)
                img.thumbnail((200, 200))
                img.save(thumb_path, "JPEG", quality=60)

                # Add to photo_urls
                url_path = f"/static/photos/album/{filename}"
                if url_path not in device_state.get("photo_urls", []):
                    device_state.setdefault("photo_urls", []).append(url_path)

                synced.add(item["id"])
                new_count += 1
                _log(f"[SYNC] Downloaded: {filename}")
            else:
                _log(f"[SYNC] Failed to download {item['id']}: {resp.status_code}")
        except Exception as e:
            _log(f"[SYNC] Error downloading {item['id']}: {e}")

    # Remove photos that were deleted from the album
    removed_ids = synced - current_ids
    if removed_ids:
        for media_id in removed_ids:
            filename = f"album_{media_id[:16]}.jpg"
            photo_path = os.path.join(subdir, filename)
            thumb_path = os.path.join(thumb_dir, filename)
            url_path = f"/static/photos/album/{filename}"

            if os.path.exists(photo_path):
                os.remove(photo_path)
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
            if url_path in device_state.get("photo_urls", []):
                device_state["photo_urls"].remove(url_path)

            synced.discard(media_id)
            _log(f"[SYNC] Removed: {filename}")

        _log(f"[SYNC] Removed {len(removed_ids)} deleted photos")

    # Save state
    device_state["synced_media_ids"] = list(synced)
    if new_count > 0 or removed_ids:
        device_state["done"] = True
        device_state["photos_chosen"] = True
        save_device_state()
        sync_photos_to_usb()
        _log(f"[SYNC] Sync complete: {new_count} new, {len(removed_ids)} removed")
    else:
        _log(f"[SYNC] No changes")


# Background sync timer
_sync_timer = None

def start_sync_timer():
    """Start periodic album sync in the background."""
    global _sync_timer

    def _run():
        global _sync_timer
        try:
            sync_album()
        except Exception as e:
            _log(f"[SYNC] Error: {e}")
        # Schedule next sync
        _sync_timer = threading.Timer(SYNC_INTERVAL, _run)
        _sync_timer.daemon = True
        _sync_timer.start()

    # Run first sync after a short delay (let server start up)
    _sync_timer = threading.Timer(10, _run)
    _sync_timer.daemon = True
    _sync_timer.start()
    _log(f"[SYNC] Auto-sync started (every {SYNC_INTERVAL // 60} min)")


def stop_sync_timer():
    """Stop the background sync."""
    global _sync_timer
    if _sync_timer:
        _sync_timer.cancel()
        _sync_timer = None
        _log("[SYNC] Auto-sync stopped")
