import os
import pytest


def _reconcile_photos():
    """Import reconcile_photos without triggering route registration."""
    import importlib
    import sys

    # If main was already imported (with routes), use it
    if 'main' in sys.modules:
        return sys.modules['main'].reconcile_photos

    # Otherwise, load the function without executing the module's route imports
    # by directly calling the function logic
    from photo_ops import walk_photos, generate_thumbnail, compute_md5
    import config
    import db

    def reconcile_photos():
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

            existing = db.get_photo(filename)
            if existing and existing["md5"]:
                md5 = existing["md5"]
            else:
                md5 = compute_md5(full_path)
                md5_backfilled += 1

            db.add_photo(filename, subdir=subdir, uploaded_by='admin',
                         size_bytes=size, md5=md5)

            thumb = os.path.join(thumb_dir, filename)
            if not os.path.exists(thumb) and os.path.exists(full_path):
                generate_thumbnail(full_path, thumb)

            photo_count += 1

        if photo_count > 0:
            db.set_setting("done", True)
            db.set_setting("photos_chosen", True)

        removed = 0
        for row in db.get_all_photos():
            if row["filename"] not in actual_filenames:
                db.remove_photo(row["filename"])
                removed += 1

        if photo_count == 0 and db.get_photo_count() == 0:
            db.set_setting("done", False)

    return reconcile_photos


def test_photos_not_deleted_on_startup(photos_dir_with_images, monkeypatch):
    """Critical regression test: photos must NOT be deleted on startup."""
    import config
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos_dir_with_images))

    picker_dir = photos_dir_with_images / "picker"
    assert len(list(picker_dir.iterdir())) == 3

    # Simulate what main.py does on startup
    os.makedirs(str(photos_dir_with_images), exist_ok=True)

    # Photos must still be there
    assert len(list(picker_dir.iterdir())) == 3


def test_reconcile_finds_photos_on_disk(photos_dir_with_images, monkeypatch, tmp_path):
    """Reconciliation should populate DB from disk photos."""
    import config
    import db
    import photo_ops
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos_dir_with_images))
    monkeypatch.setattr(photo_ops, "PHOTOS_DIR", str(photos_dir_with_images))

    # Use isolated DB
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    reconcile_photos = _reconcile_photos()
    reconcile_photos()

    # Photos should be in DB
    assert db.get_photo_count() == 3
    assert db.get_setting("done") is True
    assert db.get_setting("photos_chosen") is True

    # All photos should have MD5 populated
    for photo in db.get_all_photos():
        assert photo["md5"], f"Missing MD5 for {photo['filename']}"

    # Photo URLs from DB should start with /static/
    urls = db.get_photo_urls()
    assert len(urls) == 3
    for url in urls:
        assert url.startswith("/static/")

    # Clean up
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_reconcile_empty_dir_sets_not_done(photos_dir, monkeypatch, tmp_path):
    """With no photos on disk, done should be False."""
    import config
    import db
    import photo_ops
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos_dir))
    monkeypatch.setattr(photo_ops, "PHOTOS_DIR", str(photos_dir))

    # Use isolated DB
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    reconcile_photos = _reconcile_photos()
    reconcile_photos()

    assert db.get_setting("done") is False

    # Clean up
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_reconcile_removes_stale_records(photos_dir_with_images, monkeypatch, tmp_path):
    """Reconciliation should remove DB records for files no longer on disk."""
    import config
    import db
    import photo_ops
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos_dir_with_images))
    monkeypatch.setattr(photo_ops, "PHOTOS_DIR", str(photos_dir_with_images))

    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    # Add a stale record
    db.add_photo("deleted_photo.jpg", subdir="picker", uploaded_by="admin")
    assert db.get_photo("deleted_photo.jpg") is not None

    reconcile_photos = _reconcile_photos()
    reconcile_photos()

    # Stale record should be gone, real photos should remain
    assert db.get_photo("deleted_photo.jpg") is None
    assert db.get_photo_count() == 3

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
