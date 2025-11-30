# main.py
import os
import shutil
from app import app  # Import from app.py to avoid circular imports
from config import PHOTOS_DIR

# Import route files
import routes.base_routes
import routes.picker_routes
import routes.admin_routes

if __name__ == "__main__":
    # Clear photos directory on startup
    if os.path.exists(PHOTOS_DIR):
        for f in os.listdir(PHOTOS_DIR):
            full_path = os.path.join(PHOTOS_DIR, f)
            if os.path.isfile(full_path):
                os.remove(full_path)
            else:
                shutil.rmtree(full_path)
    else:
        os.makedirs(PHOTOS_DIR, exist_ok=True)

    print("Starting app on port 3000")

    # Turn off the reloader to avoid double loading confusion:
    #   debug=True but use_reloader=False => You still get debug logs,
    #   but no double "restart with stat" process.
    app.run(host="0.0.0.0", port=3000, debug=True, use_reloader=False)
