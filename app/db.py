import sqlite3
import json
import os
import threading
from config import IMAGE_EXTENSIONS

DB_PATH = os.environ.get('INSTAPI_DB_PATH',
          os.path.join(os.path.dirname(__file__), 'instapi.db'))

_local = threading.local()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT UNIQUE NOT NULL,
    subdir      TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL DEFAULT 'admin',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    size_bytes  INTEGER DEFAULT 0,
    md5         TEXT
);

CREATE INDEX IF NOT EXISTS idx_photos_uploader ON photos(uploaded_by);
CREATE INDEX IF NOT EXISTS idx_photos_subdir ON photos(subdir);

CREATE TABLE IF NOT EXISTS sync_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    result         TEXT NOT NULL,
    photos_added   INTEGER DEFAULT 0,
    photos_removed INTEGER DEFAULT 0,
    duration_s     REAL DEFAULT 0,
    error          TEXT
);
"""


def get_db():
    """Get thread-local DB connection."""
    if not hasattr(_local, 'conn') or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db():
    """Create tables if they don't exist."""
    db = get_db()
    db.executescript(SCHEMA_SQL)
    db.commit()


# --- Settings helpers ---

def get_setting(key, default=None):
    """Get a setting value. Returns deserialized JSON."""
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row["value"])


def set_setting(key, value):
    """Set a setting value. Serializes to JSON."""
    get_db().execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, json.dumps(value))
    )
    get_db().commit()


def delete_setting(key):
    """Delete a setting."""
    get_db().execute("DELETE FROM settings WHERE key=?", (key,))
    get_db().commit()


def get_all_settings():
    """Get all settings as a dict."""
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: json.loads(row["value"]) for row in rows}


def clear_all_settings():
    """Clear all settings (for factory reset)."""
    get_db().execute("DELETE FROM settings")
    get_db().commit()


# --- Photos helpers ---

def add_photo(filename, subdir='', uploaded_by='admin', size_bytes=0, md5=None):
    """Add a photo record. Updates size/md5 if exists, preserves uploaded_by."""
    db = get_db()
    existing = db.execute("SELECT id FROM photos WHERE filename=?", (filename,)).fetchone()
    if existing:
        db.execute(
            "UPDATE photos SET subdir=?, size_bytes=?, md5=? WHERE filename=?",
            (subdir, size_bytes, md5, filename)
        )
    else:
        db.execute(
            "INSERT INTO photos (filename, subdir, uploaded_by, size_bytes, md5) VALUES (?, ?, ?, ?, ?)",
            (filename, subdir, uploaded_by, size_bytes, md5)
        )
    get_db().commit()


def remove_photo(filename):
    """Remove a photo record."""
    get_db().execute("DELETE FROM photos WHERE filename=?", (filename,))
    get_db().commit()


def get_photo(filename):
    """Get a photo record by filename."""
    return get_db().execute("SELECT * FROM photos WHERE filename=?", (filename,)).fetchone()


def get_all_photos():
    """Get all photo records."""
    return get_db().execute("SELECT * FROM photos ORDER BY created_at DESC").fetchall()


def get_photos_by_uploader(uploader):
    """Get photos uploaded by a specific user."""
    return get_db().execute("SELECT * FROM photos WHERE uploaded_by=?", (uploader,)).fetchall()


def get_photo_count():
    """Get total photo count."""
    return get_db().execute("SELECT COUNT(*) FROM photos").fetchone()[0]


def get_upload_meta():
    """Get upload metadata as a dict (compatibility with old upload_meta.json)."""
    rows = get_db().execute("SELECT filename, uploaded_by FROM photos").fetchall()
    return {row["filename"]: row["uploaded_by"] for row in rows}


def get_photo_urls():
    """Build slideshow URL list from photos table.

    This is the single source of truth for which photos the slideshow shows.
    Replaces the old photo_urls setting which could drift out of sync.
    """
    rows = get_db().execute(
        "SELECT filename, subdir FROM photos ORDER BY created_at"
    ).fetchall()
    urls = []
    for row in rows:
        if row["subdir"]:
            urls.append(f"/static/photos/{row['subdir']}/{row['filename']}")
        else:
            urls.append(f"/static/photos/{row['filename']}")
    return urls


def clear_all_photos():
    """Clear all photo records (for factory reset)."""
    get_db().execute("DELETE FROM photos")
    get_db().commit()


# --- Sync log helpers ---

def add_sync_log(result, photos_added=0, photos_removed=0, duration_s=0, error=None):
    """Add a sync log entry and prune old entries."""
    db = get_db()
    db.execute(
        "INSERT INTO sync_log (result, photos_added, photos_removed, duration_s, error) VALUES (?, ?, ?, ?, ?)",
        (result, photos_added, photos_removed, duration_s, error)
    )
    # Keep last 50 entries
    db.execute("DELETE FROM sync_log WHERE id NOT IN (SELECT id FROM sync_log ORDER BY id DESC LIMIT 50)")
    db.commit()


def get_sync_history(limit=5):
    """Get recent sync history."""
    rows = get_db().execute(
        "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_last_sync():
    """Get the most recent sync log entry."""
    row = get_db().execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# --- Migration ---

def migrate_from_json(photos_dir):
    """One-time migration from JSON files to SQLite."""
    db = get_db()

    # Skip if already migrated (settings table has data)
    if db.execute("SELECT COUNT(*) FROM settings").fetchone()[0] > 0:
        return False

    print("[MIGRATION] Migrating from JSON files to SQLite...")
    migrated_files = []

    # 1. Migrate device_state.json
    state_file = os.environ.get('INSTAPI_STATE_FILE',
                 os.path.join(os.path.dirname(__file__), 'device_state.json'))
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)

        # Migrate sync_history separately to sync_log table
        sync_history = state.pop("sync_history", [])
        for entry in sync_history:
            db.execute(
                "INSERT INTO sync_log (timestamp, result, photos_added, photos_removed, duration_s, error) VALUES (?, ?, ?, ?, ?, ?)",
                (entry.get("timestamp"), entry.get("result", "unknown"),
                 entry.get("photos_added", 0), entry.get("photos_removed", 0),
                 entry.get("duration_s", 0), entry.get("error"))
            )

        # Everything else goes to settings
        # Skip transient keys that shouldn't be persisted
        skip_keys = {"photo_urls", "done", "photos_chosen", "current_index",
                     "downloading", "download_total", "download_completed",
                     "credentials", "auth_url", "picking_session_id", "picker_url",
                     "sync_in_progress", "sync_total", "sync_completed", "sync_phase",
                     "upload_processing", "upload_total", "upload_completed"}
        for key, value in state.items():
            if key not in skip_keys:
                set_setting(key, value)

        migrated_files.append(state_file)
        print(f"[MIGRATION] Migrated device_state.json ({len(state)} settings, {len(sync_history)} sync entries)")

    # 2. Migrate slideshow_config.json
    config_file = os.path.join(os.path.dirname(__file__), 'slideshow_config.json')
    if os.path.exists(config_file):
        with open(config_file) as f:
            config = json.load(f)
        for key, value in config.items():
            set_setting(f"slideshow_{key}", value)
        migrated_files.append(config_file)
        print(f"[MIGRATION] Migrated slideshow_config.json ({len(config)} settings)")

    # 3. Migrate upload_meta.json
    meta_file = os.path.join(photos_dir, 'upload_meta.json')
    meta = {}
    if os.path.exists(meta_file):
        with open(meta_file) as f:
            meta = json.load(f)
        migrated_files.append(meta_file)
        print(f"[MIGRATION] Migrated upload_meta.json ({len(meta)} entries)")

    # 4. Reconcile photos on disk
    photo_count = 0
    if os.path.exists(photos_dir):
        for root, dirs, files in os.walk(photos_dir):
            dirs[:] = [d for d in dirs if d not in ('thumbs', '.staging')]
            for f in files:
                if f.lower().endswith(IMAGE_EXTENSIONS):
                    subdir = os.path.relpath(root, photos_dir)
                    if subdir == '.':
                        subdir = ''
                    uploader = meta.get(f, 'admin')
                    full_path = os.path.join(root, f)
                    size = os.path.getsize(full_path) if os.path.exists(full_path) else 0
                    db.execute(
                        "INSERT OR IGNORE INTO photos (filename, subdir, uploaded_by, size_bytes) VALUES (?, ?, ?, ?)",
                        (f, subdir, uploader, size)
                    )
                    photo_count += 1

    db.commit()
    print(f"[MIGRATION] Reconciled {photo_count} photos from disk")

    # Rename old files (safety net - don't delete)
    for f in migrated_files:
        if os.path.exists(f):
            os.rename(f, f + '.migrated')
            print(f"[MIGRATION] Renamed {f} -> {f}.migrated")

    print("[MIGRATION] Complete!")
    return True
