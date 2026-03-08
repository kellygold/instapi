# main.py
import os
from app import app  # Import from app.py to avoid circular imports
from config import PHOTOS_DIR, device_state, save_device_state

# Import route files
import routes.base_routes
import routes.picker_routes
import routes.admin_routes


def reconcile_photos():
    """Rebuild photo_urls from what's actually on disk.

    This ensures the slideshow and admin panel reflect reality after a reboot,
    even if device_state.json was lost or out of sync.
    """
    actual_photos = []
    if os.path.exists(PHOTOS_DIR):
        for root, dirs, files in os.walk(PHOTOS_DIR):
            for f in sorted(files):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    rel = os.path.relpath(os.path.join(root, f), os.path.dirname(PHOTOS_DIR))
                    actual_photos.append(f"/static/{rel}")

    if actual_photos:
        device_state["photo_urls"] = actual_photos
        device_state["done"] = True
        device_state["photos_chosen"] = True
        save_device_state()
        print(f"Reconciled {len(actual_photos)} photos from disk")
    elif not device_state.get("photo_urls"):
        device_state["done"] = False
        print("No photos on disk")


if __name__ == "__main__":
    # Ensure photos directory exists (but never clear it — photos must survive reboots)
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    reconcile_photos()

    port = int(os.environ.get("PORT", 3000))
    print(f"Starting app on port {port}")

    # Turn off the reloader to avoid double loading confusion:
    #   debug=True but use_reloader=False => You still get debug logs,
    #   but no double "restart with stat" process.
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
