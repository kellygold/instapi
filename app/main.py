# main.py
import os
from app import app  # Import from app.py to avoid circular imports
from config import PHOTOS_DIR

# Import route files
import routes.base_routes
import routes.picker_routes
import routes.admin_routes

if __name__ == "__main__":
    # Ensure photos directory exists (but never clear it — photos must survive reboots)
    os.makedirs(PHOTOS_DIR, exist_ok=True)

    port = int(os.environ.get("PORT", 3000))
    print(f"Starting app on port {port}")

    # Turn off the reloader to avoid double loading confusion:
    #   debug=True but use_reloader=False => You still get debug logs,
    #   but no double "restart with stat" process.
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
