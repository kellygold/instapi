# photo_ops.py - Central photo lifecycle operations
#
# All photo manipulation (thumbnail, MD5, directory walking, file deletion)
# lives here. Every flow that modifies photos should use these functions
# to ensure consistent behavior across upload, sync, picker, and admin paths.

import os
import hashlib
import threading
from PIL import Image
from config import IMAGE_EXTENSIONS, THUMBNAIL_SIZE, THUMBNAIL_QUALITY, PHOTOS_DIR
import db


def compute_md5(file_path, chunk_size=8192):
    """Compute MD5 hash of a file. Returns hex digest string."""
    h = hashlib.md5()
    with open(file_path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b''):
            h.update(chunk)
    return h.hexdigest()


def generate_thumbnail(source_path, thumb_path):
    """Generate a 200x200 JPEG thumbnail. Logs and continues on error."""
    try:
        img = Image.open(source_path)
        img.thumbnail(THUMBNAIL_SIZE)
        img.save(thumb_path, "JPEG", quality=THUMBNAIL_QUALITY)
    except Exception as e:
        print(f"[THUMB] Failed for {source_path}: {e}")


def walk_photos(directory, exclude_dirs=('thumbs', '.staging')):
    """Walk a directory tree yielding image files.

    Yields (filename, full_path, subdir) tuples where subdir is the
    relative path from directory ('' for root level).

    Args:
        directory: Root directory to walk
        exclude_dirs: Directory names to skip (default: thumbs, .staging)
    """
    if not os.path.exists(directory):
        return
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for f in sorted(files):
            if f.lower().endswith(IMAGE_EXTENSIONS):
                full_path = os.path.join(root, f)
                subdir = os.path.relpath(root, directory)
                if subdir == '.':
                    subdir = ''
                yield f, full_path, subdir


def delete_photo_files(filename, photos_dir=None):
    """Delete a photo file, its thumbnail, and its DB record.

    Searches subdirectories to find the file. Returns True if file was found
    and deleted, False if file was not found (DB record is still removed).
    """
    if photos_dir is None:
        photos_dir = PHOTOS_DIR

    # Find and delete the photo file
    deleted = False
    for subdir in ["upload", "picker", "album", ""]:
        file_path = os.path.join(photos_dir, subdir, filename) if subdir else os.path.join(photos_dir, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
            deleted = True
            break

    # Also check sync subdirectories
    if not deleted:
        sync_dir = os.path.join(photos_dir, "sync")
        if os.path.exists(sync_dir):
            for root, dirs, files in os.walk(sync_dir):
                if filename in files:
                    os.remove(os.path.join(root, filename))
                    deleted = True
                    break

    # Remove thumbnail
    thumb_path = os.path.join(photos_dir, "thumbs", filename)
    if os.path.exists(thumb_path):
        os.remove(thumb_path)

    # Remove from DB
    db.remove_photo(filename)

    return deleted


def notify_photos_changed():
    """Call once after a batch of photo adds/deletes to ensure consistent state.

    This is the lifecycle hook that every photo-modifying flow must call
    AFTER its batch is complete (not per-photo). It:
    - Sets done/photos_chosen flags based on photo count
    - Triggers USB sync if in USB mode

    Do NOT call this per-photo in a loop. Call it once after the loop.
    """
    from utils import get_display_mode, sync_photos_to_usb

    count = db.get_photo_count()
    if count > 0:
        db.set_setting("done", True)
        db.set_setting("photos_chosen", True)
    else:
        db.set_setting("done", False)
        db.set_setting("photos_chosen", False)

    if get_display_mode() == "usb":
        sync_photos_to_usb()  # Handles 0 photos too (shows QR placeholder)
