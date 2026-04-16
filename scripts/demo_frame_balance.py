import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
APP_DIR = REPO_ROOT / "app"
DEMO_ROOT = APP_DIR / ".demo_frame_balance"
DEMO_DB_PATH = DEMO_ROOT / "instapi-demo.db"
SECRETS_PATH = APP_DIR / "secrets.json"
SECRETS_TEMPLATE_PATH = APP_DIR / "secrets.json.template"
STATIC_PHOTOS_DIR = APP_DIR / "static" / "photos"
STATIC_SYNC_UPLOAD_DIR = STATIC_PHOTOS_DIR / "sync" / "upload"
STATIC_THUMBS_DIR = STATIC_PHOTOS_DIR / "thumbs"
DEMO_PREFIX = "demo_"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def ensure_demo_environment():
    """Point the app at isolated demo state before app modules import.

    This switches the process to a throwaway demo DB, keeps startup from
    reconciling any pre-existing real photo library into that DB, and sets
    a predictable admin password for quick local testing.
    """
    os.environ.setdefault("INSTAPI_ADMIN_PASSWORD", "test123")
    os.environ["INSTAPI_PHOTOS_DIR"] = str(STATIC_PHOTOS_DIR)
    os.environ["INSTAPI_DB_PATH"] = str(DEMO_DB_PATH)
    os.environ["INSTAPI_SKIP_RECONCILE"] = "1"


ensure_demo_environment()

import config
import db
from frame_balance import (
    FRAME_BALANCE_ENABLED_KEY,
    FRAME_BALANCE_WEIGHTS_KEY,
    rebuild_balanced_playlist,
)
from photo_ops import compute_md5, generate_thumbnail
from PIL import Image, ImageDraw


UPLOADER_SPECS = [
    {"name": "Michael", "count": 6, "color": (229, 115, 115), "weight": 50},
    {"name": "Kelly", "count": 4, "color": (79, 195, 247), "weight": 30},
    {"name": "Ana", "count": 2, "color": (129, 199, 132), "weight": 20},
]


def ensure_secrets_file():
    """Create `secrets.json` from the template when local dev has none.

    The demo path does not need working Google credentials, but several app
    routes expect the file to exist. Copying the template avoids startup noise
    on a fresh checkout.
    """
    if not SECRETS_PATH.exists() and SECRETS_TEMPLATE_PATH.exists():
        shutil.copy2(SECRETS_TEMPLATE_PATH, SECRETS_PATH)


def force_hdmi_mode():
    """Force the app into HDMI mode so the demo avoids USB sync scripts.

    The browser-testing workflow only needs the web slideshow and admin UI.
    Writing `hdmi` to the mode file keeps the app from invoking USB-only shell
    scripts during startup or balance updates.
    """
    Path(config.MODE_FILE).write_text("hdmi", encoding="utf-8")


def reset_demo_data():
    """Clear prior demo-only state and prepare fresh directories.

    This removes the throwaway demo database and any previously generated
    `demo_*.jpg` assets, then recreates the directories needed for a clean run.
    Real user photos are left alone because only demo-prefixed files are
    deleted from the shared static photo folders.
    """
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    STATIC_SYNC_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_THUMBS_DIR.mkdir(parents=True, exist_ok=True)

    for directory in (STATIC_SYNC_UPLOAD_DIR, STATIC_THUMBS_DIR):
        for path in directory.glob(f"{DEMO_PREFIX}*.jpg"):
            path.unlink(missing_ok=True)


def create_demo_image(target_path, uploader, index, color):
    """Generate a synthetic JPEG that is easy to identify in the UI.

    Example: calling this with uploader `Kelly` and index `0` produces a
    colored card-like image labeled `Kelly` and `Synthetic upload #1`.
    That makes it obvious in the slideshow which uploader a frame-balance slot
    came from.
    """
    img = Image.new("RGB", (1600, 900), color)
    draw = ImageDraw.Draw(img)
    accent = tuple(max(channel - 40, 0) for channel in color)

    draw.rectangle((50, 50, 1550, 850), outline=(255, 255, 255), width=8)
    draw.rectangle((90, 90, 1510, 320), fill=accent)
    draw.text((130, 130), f"{uploader}", fill=(255, 255, 255))
    draw.text((130, 210), f"Synthetic upload #{index + 1}", fill=(255, 255, 255))
    draw.text((130, 290), "Frame balance metadata demo", fill=(245, 245, 245))

    for stripe in range(6):
        top = 390 + stripe * 65
        draw.rounded_rectangle(
            (140, top, 1460, top + 34),
            radius=12,
            fill=((255 - stripe * 20), (255 - stripe * 15), (255 - stripe * 10)),
        )

    img.save(target_path, "JPEG", quality=92)


def seed_demo_photos():
    """Populate the demo DB and static photo folders with test content.

    The seeded rows mimic child-frame sync data by storing photos under the
    `sync/upload` subdir and setting `uploaded_by` metadata for each uploader.

    Example seeded mix:
    - Michael: 6 photos, target weight 50
    - Kelly: 4 photos, target weight 30
    - Ana: 2 photos, target weight 20

    After inserting the photos, the function enables frame balance and builds
    the initial balanced playlist so the browser demo is ready immediately.
    """
    db.init_db()
    created_at = datetime(2026, 4, 1, 9, 0, 0)
    weights = {}

    for uploader_index, spec in enumerate(UPLOADER_SPECS):
        uploader = spec["name"]
        weights[uploader] = spec["weight"]
        for index in range(spec["count"]):
            filename = f"{DEMO_PREFIX}{uploader.lower()}_{index + 1:02d}.jpg"
            subdir = "sync/upload"
            photo_path = STATIC_SYNC_UPLOAD_DIR / filename
            thumb_path = STATIC_THUMBS_DIR / filename
            create_demo_image(photo_path, uploader, index, spec["color"])
            generate_thumbnail(str(photo_path), str(thumb_path))
            file_md5 = compute_md5(str(photo_path))
            db.add_photo(
                filename,
                subdir=subdir,
                uploaded_by=uploader,
                size_bytes=photo_path.stat().st_size,
                md5=file_md5,
                created_at=(created_at + timedelta(minutes=uploader_index * 20 + index)).strftime("%Y-%m-%d %H:%M:%S"),
            )

    db.set_setting("done", True)
    db.set_setting("photos_chosen", True)
    db.set_setting("sync_role", "child")
    db.set_setting("sync_label", "Demo Child")
    db.set_setting("upload_token", "demo-upload-token")
    db.set_setting("slideshow_slide_duration", 5)
    db.set_setting("slideshow_transition", "fade")
    db.set_setting("slideshow_shuffle", False)
    db.set_setting("slideshow_ken_burns", False)
    db.set_setting(FRAME_BALANCE_ENABLED_KEY, True)
    db.set_setting(FRAME_BALANCE_WEIGHTS_KEY, weights)
    rebuild_balanced_playlist(force=True)


def launch_main():
    """Print quick-start info, then replace this process with `main.py`.

    `os.execv(...)` is used instead of spawning a child process so the terminal
    behaves like a normal app launch: one process, one stream of logs, and
    Ctrl-C stops the Flask server directly.
    """
    os.chdir(APP_DIR)
    print("Demo data ready.")
    print(f"Photos dir: {STATIC_PHOTOS_DIR}")
    print(f"DB path: {DEMO_DB_PATH}")
    print(f"Admin password: {os.environ['INSTAPI_ADMIN_PASSWORD']}")
    print("Open http://localhost:3000/admin to test the frame balance UI.")
    print("Open http://localhost:3000/slideshow to verify the balanced playlist.")
    os.execv(sys.executable, [sys.executable, str(APP_DIR / "main.py")])


def main():
    """Run the full demo setup flow, then start the Flask app.

    Order matters here:
    1. Ensure config files exist
    2. Force HDMI mode
    3. Reset old demo state
    4. Seed synthetic photos + metadata
    5. Hand off to `main.py`
    """
    ensure_secrets_file()
    force_hdmi_mode()
    reset_demo_data()
    seed_demo_photos()
    launch_main()


if __name__ == "__main__":
    main()
