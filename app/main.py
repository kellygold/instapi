# main.py
import os
from app import app  # Import from app.py to avoid circular imports
import config
from config import device_state, save_device_state

# Import route files
import routes.base_routes
import routes.picker_routes
import routes.admin_routes
import routes.upload_routes
import routes.sync_routes
import routes.wifi_routes


def reconcile_photos():
    """Rebuild photo_urls from what's actually on disk.

    This ensures the slideshow and admin panel reflect reality after a reboot,
    even if device_state.json was lost or out of sync.
    """
    photos_dir = config.PHOTOS_DIR
    actual_photos = []
    if os.path.exists(photos_dir):
        for root, dirs, files in os.walk(photos_dir):
            dirs[:] = [d for d in dirs if d != 'thumbs']
            for f in sorted(files):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    rel = os.path.relpath(os.path.join(root, f), os.path.dirname(photos_dir))
                    actual_photos.append(f"/static/{rel}")

    if actual_photos:
        device_state["photo_urls"] = actual_photos
        device_state["done"] = True
        device_state["photos_chosen"] = True
        save_device_state()
        print(f"Reconciled {len(actual_photos)} photos from disk")

        # Backfill thumbnails for photos that predate this feature
        thumb_dir = os.path.join(photos_dir, "thumbs")
        os.makedirs(thumb_dir, exist_ok=True)
        for photo_url in actual_photos:
            filename = os.path.basename(photo_url)
            rel = photo_url.replace("/static/", "")
            original = os.path.join(os.path.dirname(photos_dir), rel)
            thumb = os.path.join(thumb_dir, filename)
            if not os.path.exists(thumb) and os.path.exists(original):
                from PIL import Image
                img = Image.open(original)
                img.thumbnail((200, 200))
                img.save(thumb, "JPEG", quality=60)
                print(f"Generated thumbnail for {filename}")
    elif not device_state.get("photo_urls"):
        device_state["done"] = False
        print("No photos on disk")


if __name__ == "__main__":
    # Ensure photos directory exists (but never clear it — photos must survive reboots)
    os.makedirs(config.PHOTOS_DIR, exist_ok=True)
    reconcile_photos()

    # Generate upload token if not set
    if not device_state.get("upload_token"):
        import secrets
        device_state["upload_token"] = secrets.token_urlsafe(16)
        save_device_state()
        print(f"Upload token: {device_state['upload_token']}")

    # Initialize sync role
    if device_state.get("sync_role") == "master":
        device_state.setdefault("sync_children", [])
        save_device_state()

    # Start child sync loop if configured
    if device_state.get("sync_role") == "child" and device_state.get("master_url"):
        from routes.sync_routes import start_sync_loop
        start_sync_loop()

    port = int(os.environ.get("PORT", 3000))
    print(f"Starting app on port {port}")

    # Turn off the reloader to avoid double loading confusion:
    #   debug=True but use_reloader=False => You still get debug logs,
    #   but no double "restart with stat" process.
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode, use_reloader=False)
