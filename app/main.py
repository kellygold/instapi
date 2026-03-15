# main.py
import os
from app import app  # Import from app.py to avoid circular imports
import config
import db

# Import route files
import routes.base_routes
import routes.picker_routes
import routes.admin_routes
import routes.upload_routes
import routes.sync_routes
import routes.wifi_routes


def reconcile_photos():
    """Rebuild photo records from what's actually on disk.

    This ensures the slideshow and admin panel reflect reality after a reboot,
    even if the database was lost or out of sync.
    """
    photos_dir = config.PHOTOS_DIR
    actual_photos = []
    if os.path.exists(photos_dir):
        for root, dirs, files in os.walk(photos_dir):
            dirs[:] = [d for d in dirs if d != 'thumbs']
            for f in sorted(files):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    full_path = os.path.join(root, f)
                    rel = os.path.relpath(full_path, os.path.dirname(photos_dir))
                    actual_photos.append((f, full_path, f"/static/{rel}"))

    if actual_photos:
        for filename, full_path, photo_url in actual_photos:
            subdir = os.path.relpath(os.path.dirname(full_path), photos_dir)
            if subdir == '.':
                subdir = ''
            try:
                size = os.path.getsize(full_path)
            except OSError:
                size = 0
            db.add_photo(filename, subdir=subdir, uploaded_by='admin', size_bytes=size)

        db.set_setting("done", True)
        db.set_setting("photos_chosen", True)
        print(f"Reconciled {len(actual_photos)} photos from disk")

        # Backfill thumbnails for photos that predate this feature
        thumb_dir = os.path.join(photos_dir, "thumbs")
        os.makedirs(thumb_dir, exist_ok=True)
        for filename, full_path, photo_url in actual_photos:
            thumb = os.path.join(thumb_dir, filename)
            if not os.path.exists(thumb) and os.path.exists(full_path):
                from PIL import Image
                img = Image.open(full_path)
                img.thumbnail((200, 200))
                img.save(thumb, "JPEG", quality=60)
                print(f"Generated thumbnail for {filename}")
    elif db.get_photo_count() == 0:
        db.set_setting("done", False)
        print("No photos on disk")


if __name__ == "__main__":
    # Ensure photos directory exists (but never clear it — photos must survive reboots)
    os.makedirs(config.PHOTOS_DIR, exist_ok=True)

    # Initialize database and migrate from JSON if needed
    db.init_db()
    db.migrate_from_json(config.PHOTOS_DIR)

    reconcile_photos()

    # Generate upload token if not set
    if not db.get_setting("upload_token"):
        import secrets
        token = secrets.token_urlsafe(16)
        db.set_setting("upload_token", token)
        print(f"Upload token: {token}")

    # Initialize sync role
    if db.get_setting("sync_role") == "master":
        if not db.get_setting("sync_children"):
            db.set_setting("sync_children", [])

    # Start child sync loop if configured
    if db.get_setting("sync_role") == "child" and db.get_setting("master_url"):
        from routes.sync_routes import start_sync_loop
        start_sync_loop()

    port = int(os.environ.get("PORT", 3000))
    print(f"Starting app on port {port}")

    # Turn off the reloader to avoid double loading confusion:
    #   debug=True but use_reloader=False => You still get debug logs,
    #   but no double "restart with stat" process.
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode, use_reloader=False)
