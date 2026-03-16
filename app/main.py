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
    from photo_ops import walk_photos, generate_thumbnail, compute_md5

    photos_dir = config.PHOTOS_DIR
    actual_filenames = set()
    photo_count = 0
    thumb_dir = os.path.join(photos_dir, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    md5_backfilled = 0

    for filename, full_path, subdir in walk_photos(photos_dir):
        actual_filenames.add(filename)
        try:
            size = os.path.getsize(full_path)
        except OSError:
            size = 0

        # Check if MD5 needs backfilling (only compute if not already in DB)
        existing = db.get_photo(filename)
        if existing and existing["md5"]:
            md5 = existing["md5"]
        else:
            md5 = compute_md5(full_path)
            md5_backfilled += 1
            if md5_backfilled % 50 == 0:
                print(f"[RECONCILE] MD5 backfill progress: {md5_backfilled} photos...")

        db.add_photo(filename, subdir=subdir, uploaded_by='admin',
                     size_bytes=size, md5=md5)

        # Backfill thumbnails for photos that predate this feature
        thumb = os.path.join(thumb_dir, filename)
        if not os.path.exists(thumb) and os.path.exists(full_path):
            generate_thumbnail(full_path, thumb)

        photo_count += 1

    if photo_count > 0:
        db.set_setting("done", True)
        db.set_setting("photos_chosen", True)

    # Remove DB records for files that no longer exist on disk
    removed = 0
    for row in db.get_all_photos():
        if row["filename"] not in actual_filenames:
            db.remove_photo(row["filename"])
            removed += 1
    if removed:
        print(f"Reconciled: removed {removed} stale DB records")

    if photo_count > 0:
        msg = f"Reconciled {photo_count} photos from disk"
        if md5_backfilled:
            msg += f" ({md5_backfilled} MD5s backfilled)"
        print(msg)
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

    # In USB mode, ensure USB content matches disk after startup.
    # This catches: failed USB updates, mode detection fixes, service restarts
    # where sync deleted/added photos but USB was never refreshed.
    from utils import get_display_mode, sync_photos_to_usb
    if get_display_mode() == "usb" and db.get_photo_count() > 0:
        import threading
        def _startup_usb_sync():
            import time
            time.sleep(30)  # let Flask and USB gadget finish starting
            print("[STARTUP] Running USB sync to ensure frame matches disk...", flush=True)
            sync_photos_to_usb()
        threading.Thread(target=_startup_usb_sync, daemon=True).start()

    port = int(os.environ.get("PORT", 3000))
    print(f"Starting app on port {port}")

    # Turn off the reloader to avoid double loading confusion:
    #   debug=True but use_reloader=False => You still get debug logs,
    #   but no double "restart with stat" process.
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode, use_reloader=False)
