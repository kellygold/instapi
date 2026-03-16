import os
import hashlib
import threading
import pytest


def _init_test_db(monkeypatch, tmp_path):
    """Initialize an isolated test DB."""
    import db
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()
    return db


def _make_test_image(path, content=None):
    """Create a minimal valid JPEG file."""
    if content is None:
        content = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return content


# ============== compute_md5 ==============

def test_compute_md5_known_file(tmp_path):
    """MD5 should match hashlib.md5 for known content."""
    from photo_ops import compute_md5
    content = b"hello world test content"
    path = str(tmp_path / "test.bin")
    with open(path, "wb") as f:
        f.write(content)

    result = compute_md5(path)
    expected = hashlib.md5(content).hexdigest()
    assert result == expected
    assert len(result) == 32


def test_compute_md5_consistent(tmp_path):
    """Same file should always return same MD5."""
    from photo_ops import compute_md5
    path = str(tmp_path / "test.jpg")
    _make_test_image(path)

    md5_1 = compute_md5(path)
    md5_2 = compute_md5(path)
    assert md5_1 == md5_2


def test_compute_md5_different_files(tmp_path):
    """Different files should return different MD5s."""
    from photo_ops import compute_md5
    path1 = str(tmp_path / "a.jpg")
    path2 = str(tmp_path / "b.jpg")
    _make_test_image(path1, b"\xff\xd8\xff\xe0" + b"aaa")
    _make_test_image(path2, b"\xff\xd8\xff\xe0" + b"bbb")

    assert compute_md5(path1) != compute_md5(path2)


# ============== generate_thumbnail ==============

def test_generate_thumbnail_creates_file(tmp_path):
    """Should create a thumbnail file."""
    from photo_ops import generate_thumbnail
    from PIL import Image

    # Create a 400x300 test image
    src = str(tmp_path / "source.jpg")
    img = Image.new("RGB", (400, 300), "red")
    img.save(src, "JPEG")

    thumb = str(tmp_path / "thumb.jpg")
    generate_thumbnail(src, thumb)

    assert os.path.exists(thumb)
    t = Image.open(thumb)
    assert max(t.size) <= 200


def test_generate_thumbnail_corrupt_image(tmp_path):
    """Should not crash on corrupt image, just log error."""
    from photo_ops import generate_thumbnail

    src = str(tmp_path / "corrupt.jpg")
    with open(src, "wb") as f:
        f.write(b"not a real image")

    thumb = str(tmp_path / "thumb.jpg")
    generate_thumbnail(src, thumb)  # Should not raise

    # Thumbnail should not be created
    assert not os.path.exists(thumb)


# ============== walk_photos ==============

def test_walk_photos_finds_all_extensions(tmp_path):
    """Should find .jpg, .jpeg, .png, .gif files."""
    from photo_ops import walk_photos

    for ext in [".jpg", ".jpeg", ".png", ".gif"]:
        _make_test_image(str(tmp_path / f"test{ext}"))

    results = list(walk_photos(str(tmp_path)))
    filenames = {r[0] for r in results}
    assert filenames == {"test.jpg", "test.jpeg", "test.png", "test.gif"}


def test_walk_photos_excludes_thumbs(tmp_path):
    """Should skip thumbs/ directory."""
    from photo_ops import walk_photos

    _make_test_image(str(tmp_path / "upload" / "photo.jpg"))
    _make_test_image(str(tmp_path / "thumbs" / "photo.jpg"))

    results = list(walk_photos(str(tmp_path)))
    assert len(results) == 1
    assert results[0][2] == "upload"  # subdir


def test_walk_photos_excludes_staging(tmp_path):
    """Should skip .staging/ directory."""
    from photo_ops import walk_photos

    _make_test_image(str(tmp_path / "upload" / "photo.jpg"))
    _make_test_image(str(tmp_path / ".staging" / "staged.jpg"))

    results = list(walk_photos(str(tmp_path)))
    assert len(results) == 1


def test_walk_photos_returns_subdir(tmp_path):
    """Should return correct subdir for nested photos."""
    from photo_ops import walk_photos

    _make_test_image(str(tmp_path / "root.jpg"))
    _make_test_image(str(tmp_path / "upload" / "uploaded.jpg"))
    _make_test_image(str(tmp_path / "sync" / "picker" / "synced.jpg"))

    results = {r[0]: r[2] for r in walk_photos(str(tmp_path))}
    assert results["root.jpg"] == ""
    assert results["uploaded.jpg"] == "upload"
    assert results["synced.jpg"] == os.path.join("sync", "picker")


def test_walk_photos_nonexistent_dir():
    """Should return empty for nonexistent directory."""
    from photo_ops import walk_photos
    results = list(walk_photos("/nonexistent/path"))
    assert results == []


# ============== delete_photo_files ==============

def test_delete_photo_files_removes_all(tmp_path, monkeypatch):
    """Should remove file, thumbnail, and DB record."""
    db = _init_test_db(monkeypatch, tmp_path)
    from photo_ops import delete_photo_files

    photos_dir = str(tmp_path / "photos")
    _make_test_image(os.path.join(photos_dir, "upload", "test.jpg"))
    _make_test_image(os.path.join(photos_dir, "thumbs", "test.jpg"))
    db.add_photo("test.jpg", subdir="upload", uploaded_by="admin")

    deleted = delete_photo_files("test.jpg", photos_dir)

    assert deleted is True
    assert not os.path.exists(os.path.join(photos_dir, "upload", "test.jpg"))
    assert not os.path.exists(os.path.join(photos_dir, "thumbs", "test.jpg"))
    assert db.get_photo("test.jpg") is None

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_delete_photo_files_missing_file(tmp_path, monkeypatch):
    """Should handle missing file gracefully, still remove DB record."""
    db = _init_test_db(monkeypatch, tmp_path)
    from photo_ops import delete_photo_files

    photos_dir = str(tmp_path / "photos")
    os.makedirs(os.path.join(photos_dir, "upload"), exist_ok=True)
    db.add_photo("missing.jpg", subdir="upload", uploaded_by="admin")

    deleted = delete_photo_files("missing.jpg", photos_dir)

    assert deleted is False
    assert db.get_photo("missing.jpg") is None

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


# ============== notify_photos_changed ==============

def test_notify_photos_changed_sets_flags(tmp_path, monkeypatch):
    """Should set done/photos_chosen based on photo count."""
    db = _init_test_db(monkeypatch, tmp_path)
    from photo_ops import notify_photos_changed
    from utils import get_display_mode
    monkeypatch.setattr("utils.get_display_mode", lambda: "hdmi")

    # No photos -> done=False
    notify_photos_changed()
    assert db.get_setting("done") is False

    # Add a photo -> done=True
    db.add_photo("test.jpg", subdir="upload", uploaded_by="admin")
    notify_photos_changed()
    assert db.get_setting("done") is True
    assert db.get_setting("photos_chosen") is True

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_notify_photos_changed_hdmi_no_sync(tmp_path, monkeypatch):
    """In HDMI mode, should NOT call sync_photos_to_usb."""
    db = _init_test_db(monkeypatch, tmp_path)
    from photo_ops import notify_photos_changed

    sync_called = []
    monkeypatch.setattr("utils.get_display_mode", lambda: "hdmi")
    monkeypatch.setattr("utils.sync_photos_to_usb", lambda: sync_called.append(True))

    db.add_photo("test.jpg", subdir="upload", uploaded_by="admin")
    notify_photos_changed()

    assert len(sync_called) == 0

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_notify_photos_changed_usb_triggers_sync(tmp_path, monkeypatch):
    """In USB mode with photos, should call sync_photos_to_usb."""
    db = _init_test_db(monkeypatch, tmp_path)
    from photo_ops import notify_photos_changed

    sync_called = []
    monkeypatch.setattr("utils.get_display_mode", lambda: "usb")
    monkeypatch.setattr("utils.sync_photos_to_usb", lambda: sync_called.append(True))

    db.add_photo("test.jpg", subdir="upload", uploaded_by="admin")
    notify_photos_changed()

    assert len(sync_called) == 1

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


# ============== USB sync lock ==============

def test_usb_sync_lock_prevents_concurrent(monkeypatch):
    """Second sync call should be skipped when lock is held."""
    from utils import _usb_sync_lock

    # Acquire the lock manually
    _usb_sync_lock.acquire()
    try:
        from utils import sync_photos_to_usb
        monkeypatch.setattr("utils.get_display_mode", lambda: "usb")

        # This should skip because lock is held
        sync_photos_to_usb()  # Should not block or crash
    finally:
        _usb_sync_lock.release()
