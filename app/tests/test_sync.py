import os
import hashlib
import pytest


def _init_test_db(monkeypatch, tmp_path):
    """Initialize an isolated test DB. Returns the db module."""
    import db
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()
    return db


@pytest.fixture
def sync_master_client(app_client):
    """App client configured as master with one child token."""
    import db
    db.set_setting("sync_role", "master")
    db.set_setting("sync_children", [
        {"label": "Gramma", "token": "test-child-token-123"}
    ])
    return app_client


@pytest.fixture
def sync_child_client(app_client):
    """App client configured as child."""
    import db
    db.set_setting("sync_role", "child")
    db.set_setting("master_url", "https://master.example.com")
    db.set_setting("sync_token", "test-child-token-123")
    return app_client


@pytest.fixture
def master_with_photos(sync_master_client, monkeypatch, tmp_path):
    """Master client with actual photos on disk and in DB."""
    import config
    import db
    photos_dir = config.PHOTOS_DIR

    # Create some test photos
    picker_dir = os.path.join(photos_dir, "picker")
    os.makedirs(picker_dir, exist_ok=True)
    for i in range(3):
        content = b"\xff\xd8\xff\xe0" + f"photo{i}".encode() + b"\x00" * 100
        path = os.path.join(picker_dir, f"test_{i}.jpg")
        with open(path, "wb") as f:
            f.write(content)
        md5 = hashlib.md5(content).hexdigest()
        db.add_photo(f"test_{i}.jpg", subdir="picker", uploaded_by="admin",
                     size_bytes=len(content), md5=md5)

    # Create thumbs dir (should be excluded from manifest)
    thumbs_dir = os.path.join(photos_dir, "thumbs")
    os.makedirs(thumbs_dir, exist_ok=True)
    with open(os.path.join(thumbs_dir, "thumb.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

    # Mark manifest dirty so it rebuilds
    from routes.sync_routes import mark_manifest_dirty
    mark_manifest_dirty()

    return sync_master_client


# ============== MASTER TESTS ==============

def test_manifest_requires_token(sync_master_client):
    """Manifest should require valid token."""
    resp = sync_master_client.get("/sync/manifest")
    assert resp.status_code == 403

    resp = sync_master_client.get("/sync/manifest?token=wrong")
    assert resp.status_code == 403


def test_manifest_returns_photos(master_with_photos):
    """Manifest should return photo list with paths and md5s."""
    resp = master_with_photos.get("/sync/manifest?token=test-child-token-123")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "photos" in data
    assert data["photo_count"] == 3
    for photo in data["photos"]:
        assert "path" in photo
        assert "size" in photo
        assert "md5" in photo
        assert photo["path"].startswith("picker/test_")


def test_manifest_excludes_thumbs(master_with_photos):
    """Thumbs directory should not appear in manifest."""
    resp = master_with_photos.get("/sync/manifest?token=test-child-token-123")
    data = resp.get_json()
    for photo in data["photos"]:
        assert not photo["path"].startswith("thumbs")


def test_manifest_excludes_sync_dir(master_with_photos):
    """Sync directory should not appear in manifest."""
    import config
    import db

    sync_dir = os.path.join(config.PHOTOS_DIR, "sync", "picker")
    os.makedirs(sync_dir, exist_ok=True)
    with open(os.path.join(sync_dir, "synced.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
    # Add to DB with sync subdir
    db.add_photo("synced.jpg", subdir="sync/picker", uploaded_by="sync")

    from routes.sync_routes import mark_manifest_dirty
    mark_manifest_dirty()

    resp = master_with_photos.get("/sync/manifest?token=test-child-token-123")
    data = resp.get_json()
    for photo in data["photos"]:
        assert not photo["path"].startswith("sync")


def test_manifest_empty_when_no_photos(sync_master_client):
    """Should return empty list when no photos."""
    from routes.sync_routes import mark_manifest_dirty
    mark_manifest_dirty()

    resp = sync_master_client.get("/sync/manifest?token=test-child-token-123")
    data = resp.get_json()
    assert data["photos"] == []
    assert data["photo_count"] == 0


def test_photo_download_requires_token(master_with_photos):
    """Photo download should require valid token."""
    resp = master_with_photos.get("/sync/photo/picker/test_0.jpg")
    assert resp.status_code == 403

    resp = master_with_photos.get("/sync/photo/picker/test_0.jpg?token=wrong")
    assert resp.status_code == 403


def test_photo_download_serves_file(master_with_photos):
    """Should serve actual photo content."""
    resp = master_with_photos.get("/sync/photo/picker/test_0.jpg?token=test-child-token-123")
    assert resp.status_code == 200
    assert resp.data.startswith(b"\xff\xd8\xff\xe0")


def test_photo_download_rejects_traversal(master_with_photos):
    """Should reject path traversal attempts."""
    resp = master_with_photos.get("/sync/photo/../../../etc/passwd?token=test-child-token-123")
    assert resp.status_code in (403, 404)


def test_photo_download_rejects_thumbs(master_with_photos):
    """Should not serve from thumbs directory."""
    resp = master_with_photos.get("/sync/photo/thumbs/thumb.jpg?token=test-child-token-123")
    assert resp.status_code == 403


# ============== MASTER ADMIN TESTS ==============

def test_add_child_frame(sync_master_client):
    """Should generate token for new child."""
    resp = sync_master_client.post(
        "/admin/sync_add_child",
        json={"label": "Dad"},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is True
    assert data["child"]["label"] == "Dad"
    assert len(data["child"]["token"]) > 10


def test_add_child_requires_label(sync_master_client):
    """Should reject empty label."""
    resp = sync_master_client.post(
        "/admin/sync_add_child",
        json={"label": ""},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is False


def test_remove_child_frame(sync_master_client):
    """Should remove child by token."""
    import db
    assert len(db.get_setting("sync_children")) == 1

    resp = sync_master_client.post(
        "/admin/sync_remove_child",
        json={"token": "test-child-token-123"},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is True
    assert len(db.get_setting("sync_children")) == 0


def test_list_children(sync_master_client):
    """Should return list of children."""
    resp = sync_master_client.get("/admin/sync_children")
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["label"] == "Gramma"


# ============== CHILD TESTS ==============

def test_sync_config_saves_role(app_client):
    """POST sync_config should persist role to DB."""
    import db
    resp = app_client.post(
        "/admin/sync_config",
        json={"sync_role": "master"},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is True
    assert db.get_setting("sync_role") == "master"


def test_sync_config_validates_role(app_client):
    """Should reject invalid role values."""
    resp = app_client.post(
        "/admin/sync_config",
        json={"sync_role": "invalid"},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is False


def test_sync_config_child_requires_url(app_client):
    """Child role should require master URL and token."""
    resp = app_client.post(
        "/admin/sync_config",
        json={"sync_role": "child"},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is False
    assert "required" in data["error"].lower()


def test_sync_now_returns_immediately(sync_child_client, monkeypatch):
    """Sync now should return 200 without blocking."""
    import routes.sync_routes as sr
    monkeypatch.setattr(sr, "run_sync_cycle", lambda: None)

    resp = sync_child_client.post("/admin/sync_now")
    data = resp.get_json()
    assert data["success"] is True


def test_sync_status_returns_state(sync_child_client):
    """Should return expected JSON structure."""
    resp = sync_child_client.get("/admin/sync_status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["sync_role"] == "child"
    assert data["master_url"] == "https://master.example.com"
    assert "sync_in_progress" in data
    assert "synced_photo_count" in data


def test_sync_not_master_returns_404(app_client):
    """Manifest endpoint should 404 when not configured as master."""
    resp = app_client.get("/sync/manifest?token=anything")
    assert resp.status_code == 404


# ============== SYNC ALGORITHM TESTS ==============

def test_sync_downloads_new_photos(monkeypatch, tmp_path):
    """Sync cycle should download photos from master manifest."""
    import config
    import routes.sync_routes as sr
    db = _init_test_db(monkeypatch, tmp_path)

    photos_dir = str(tmp_path / "photos")
    os.makedirs(photos_dir, exist_ok=True)
    monkeypatch.setattr(config, "PHOTOS_DIR", photos_dir)

    db.set_setting("sync_role", "child")
    db.set_setting("master_url", "https://master.test")
    db.set_setting("sync_token", "tok123")

    photo_content = b"\xff\xd8\xff\xe0" + b"\x00" * 200

    class MockResp:
        def __init__(self, status_code, data=None, content=None):
            self.status_code = status_code
            self._data = data
            self.content = content or b""
        def json(self):
            return self._data

    def mock_get(url, **kwargs):
        if "/sync/manifest" in url:
            return MockResp(200, data={
                "photos": [
                    {"path": "upload/photo_1.jpg", "size": 204, "md5": "abc123"},
                    {"path": "upload/photo_2.jpg", "size": 204, "md5": "def456"},
                ],
                "photo_count": 2,
                "timestamp": 1000
            })
        elif "/sync/photo/" in url:
            return MockResp(200, content=photo_content)
        return MockResp(404)

    monkeypatch.setattr("routes.sync_routes.requests.get", mock_get)
    monkeypatch.setattr(sr, "sync_photos_to_usb", lambda: None)
    monkeypatch.setattr(sr, "get_display_mode", lambda: "hdmi")

    sr.run_sync_cycle()

    sync_dir = os.path.join(photos_dir, "sync", "upload")
    assert os.path.exists(os.path.join(sync_dir, "photo_1.jpg"))
    assert os.path.exists(os.path.join(sync_dir, "photo_2.jpg"))
    assert db.get_setting("last_sync_result") == "success"

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_sync_deletes_removed_photos(monkeypatch, tmp_path):
    """Photos removed from master should be deleted locally."""
    import config
    import routes.sync_routes as sr
    db = _init_test_db(monkeypatch, tmp_path)

    photos_dir = str(tmp_path / "photos")
    sync_dir = os.path.join(photos_dir, "sync", "upload")
    os.makedirs(sync_dir, exist_ok=True)
    monkeypatch.setattr(config, "PHOTOS_DIR", photos_dir)

    # Create a local photo that's not in master (stored under sync/upload subdir)
    old_content = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    old_photo = os.path.join(sync_dir, "old_photo.jpg")
    with open(old_photo, "wb") as f:
        f.write(old_content)
    md5 = hashlib.md5(old_content).hexdigest()
    db.add_photo("old_photo.jpg", subdir="sync/upload", uploaded_by="sync", md5=md5)

    db.set_setting("sync_role", "child")
    db.set_setting("master_url", "https://master.test")
    db.set_setting("sync_token", "tok123")

    class MockResp:
        def __init__(self, status_code, data=None):
            self.status_code = status_code
            self._data = data
        def json(self):
            return self._data

    def mock_get(url, **kwargs):
        if "/sync/manifest" in url:
            return MockResp(200, data={"photos": [], "photo_count": 0, "timestamp": 1000})
        return MockResp(404)

    monkeypatch.setattr("routes.sync_routes.requests.get", mock_get)
    monkeypatch.setattr(sr, "sync_photos_to_usb", lambda: None)
    monkeypatch.setattr(sr, "get_display_mode", lambda: "hdmi")

    sr.run_sync_cycle()

    assert not os.path.exists(old_photo)

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_sync_skips_existing_photos(monkeypatch, tmp_path):
    """Photos with matching md5 should not be re-downloaded."""
    import config
    import routes.sync_routes as sr
    db = _init_test_db(monkeypatch, tmp_path)

    photos_dir = str(tmp_path / "photos")
    sync_dir = os.path.join(photos_dir, "sync", "upload")
    os.makedirs(sync_dir, exist_ok=True)
    monkeypatch.setattr(config, "PHOTOS_DIR", photos_dir)

    content = b"\xff\xd8\xff\xe0" + b"existing" + b"\x00" * 100
    existing = os.path.join(sync_dir, "existing.jpg")
    with open(existing, "wb") as f:
        f.write(content)
    md5 = hashlib.md5(content).hexdigest()
    db.add_photo("existing.jpg", subdir="sync/upload", uploaded_by="sync", md5=md5)

    db.set_setting("sync_role", "child")
    db.set_setting("master_url", "https://master.test")
    db.set_setting("sync_token", "tok123")

    download_calls = []

    class MockResp:
        def __init__(self, status_code, data=None, content=None):
            self.status_code = status_code
            self._data = data
            self.content = content or b""
        def json(self):
            return self._data

    def mock_get(url, **kwargs):
        if "/sync/manifest" in url:
            return MockResp(200, data={
                "photos": [{"path": "upload/existing.jpg", "size": len(content), "md5": md5}],
                "photo_count": 1, "timestamp": 1000
            })
        elif "/sync/photo/" in url:
            download_calls.append(url)
            return MockResp(200, content=content)
        return MockResp(404)

    monkeypatch.setattr("routes.sync_routes.requests.get", mock_get)
    monkeypatch.setattr(sr, "sync_photos_to_usb", lambda: None)
    monkeypatch.setattr(sr, "get_display_mode", lambda: "hdmi")

    sr.run_sync_cycle()

    assert len(download_calls) == 0

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_sync_handles_master_unreachable(monkeypatch, tmp_path):
    """Should handle network errors gracefully."""
    import config
    import routes.sync_routes as sr
    import requests as req
    db = _init_test_db(monkeypatch, tmp_path)

    photos_dir = str(tmp_path / "photos")
    os.makedirs(photos_dir, exist_ok=True)
    monkeypatch.setattr(config, "PHOTOS_DIR", photos_dir)

    db.set_setting("sync_role", "child")
    db.set_setting("master_url", "https://master.test")
    db.set_setting("sync_token", "tok123")

    def mock_get(url, **kwargs):
        raise req.ConnectionError("Connection refused")

    monkeypatch.setattr("routes.sync_routes.requests.get", mock_get)

    sr.run_sync_cycle()

    assert db.get_setting("last_sync_result") == "error"
    assert db.get_setting("sync_error")

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_sync_handles_disk_full(monkeypatch, tmp_path):
    """Should stop downloading when disk is full."""
    import config
    import routes.sync_routes as sr
    db = _init_test_db(monkeypatch, tmp_path)

    photos_dir = str(tmp_path / "photos")
    os.makedirs(photos_dir, exist_ok=True)
    monkeypatch.setattr(config, "PHOTOS_DIR", photos_dir)

    db.set_setting("sync_role", "child")
    db.set_setting("master_url", "https://master.test")
    db.set_setting("sync_token", "tok123")

    class MockResp:
        def __init__(self, status_code, data=None):
            self.status_code = status_code
            self._data = data
        def json(self):
            return self._data

    def mock_get(url, **kwargs):
        if "/sync/manifest" in url:
            return MockResp(200, data={
                "photos": [{"path": "upload/big.jpg", "size": 1000, "md5": "abc"}],
                "photo_count": 1, "timestamp": 1000
            })
        return MockResp(404)

    monkeypatch.setattr("routes.sync_routes.requests.get", mock_get)

    import collections
    DiskUsage = collections.namedtuple('DiskUsage', ['total', 'used', 'free'])
    monkeypatch.setattr("routes.sync_routes.shutil.disk_usage", lambda p: DiskUsage(100, 90, 10))

    sr.run_sync_cycle()

    assert db.get_setting("last_sync_result") == "error"
    assert "Disk full" in db.get_setting("sync_error", "")

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_sync_preserves_subdirs(monkeypatch, tmp_path):
    """Synced photos should preserve subdirectory structure."""
    import config
    import routes.sync_routes as sr
    db = _init_test_db(monkeypatch, tmp_path)

    photos_dir = str(tmp_path / "photos")
    os.makedirs(photos_dir, exist_ok=True)
    monkeypatch.setattr(config, "PHOTOS_DIR", photos_dir)

    db.set_setting("sync_role", "child")
    db.set_setting("master_url", "https://master.test")
    db.set_setting("sync_token", "tok123")

    photo_content = b"\xff\xd8\xff\xe0" + b"\x00" * 100

    class MockResp:
        def __init__(self, status_code, data=None, content=None):
            self.status_code = status_code
            self._data = data
            self.content = content or b""
        def json(self):
            return self._data

    def mock_get(url, **kwargs):
        if "/sync/manifest" in url:
            return MockResp(200, data={
                "photos": [
                    {"path": "picker/from_picker.jpg", "size": 104, "md5": "aaa"},
                    {"path": "upload/from_upload.jpg", "size": 104, "md5": "bbb"},
                ],
                "photo_count": 2, "timestamp": 1000
            })
        elif "/sync/photo/" in url:
            return MockResp(200, content=photo_content)
        return MockResp(404)

    monkeypatch.setattr("routes.sync_routes.requests.get", mock_get)
    monkeypatch.setattr(sr, "sync_photos_to_usb", lambda: None)
    monkeypatch.setattr(sr, "get_display_mode", lambda: "hdmi")

    sr.run_sync_cycle()

    assert os.path.exists(os.path.join(photos_dir, "sync", "picker", "from_picker.jpg"))
    assert os.path.exists(os.path.join(photos_dir, "sync", "upload", "from_upload.jpg"))

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


# ============== DB SETTINGS TESTS ==============

def test_sync_settings_round_trip(tmp_path, monkeypatch):
    """Sync settings should survive DB save/load."""
    db = _init_test_db(monkeypatch, tmp_path)

    db.set_setting("sync_role", "master")
    db.set_setting("sync_children", [{"label": "Test", "token": "abc"}])

    assert db.get_setting("sync_role") == "master"
    assert db.get_setting("sync_children") == [{"label": "Test", "token": "abc"}]

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
