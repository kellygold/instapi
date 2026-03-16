import os
import json
import pytest


def test_index_returns_200(app_client):
    """Home page should return 200."""
    resp = app_client.get("/")
    assert resp.status_code == 200


def test_auth_status_unauthenticated(app_client):
    """Should report not authenticated when no credentials."""
    resp = app_client.get("/auth_status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["authenticated"] is False


def test_check_session_status_not_ready(app_client):
    """Should report not ready when no photos selected."""
    resp = app_client.get("/check_session_status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ready"] is False
    assert data["photo_count"] == 0


def test_check_session_status_ready(app_client):
    """Should report ready when photos are done."""
    import db
    db.set_setting("done", True)
    db.add_photo("test.jpg", subdir="upload", uploaded_by="admin")

    resp = app_client.get("/check_session_status")
    data = resp.get_json()
    assert data["ready"] is True
    assert data["photo_count"] == 1


def test_admin_photos_empty(app_client):
    """Should return empty list when no photos."""
    resp = app_client.get("/admin/photos")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == []


def test_admin_settings_get(app_client):
    """Should return slideshow settings."""
    resp = app_client.get("/admin/settings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "slide_duration" in data
    assert "transition" in data


def test_admin_settings_post(app_client):
    """Should update slideshow settings."""
    resp = app_client.post(
        "/admin/settings",
        json={"slide_duration": 10, "transition": "slide"},
        content_type="application/json"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["config"]["slide_duration"] == 10


def test_admin_switch_mode_valid(app_client, monkeypatch):
    """switch_mode should write mode file."""
    import config
    import tempfile
    mode_file = os.path.join(tempfile.mkdtemp(), ".display_mode_test")
    monkeypatch.setattr(config, "MODE_FILE", mode_file)

    resp = app_client.post(
        "/admin/switch_mode",
        json={"mode": "usb"},
        content_type="application/json"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["mode"] == "usb"

    # Verify file was written
    with open(mode_file) as f:
        assert f.read() == "usb"


def test_admin_switch_mode_invalid(app_client):
    """Should reject invalid mode."""
    resp = app_client.post(
        "/admin/switch_mode",
        json={"mode": "invalid"},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is False


def test_admin_delete_photo_rejects_bad_path(app_client):
    """Should reject paths outside static/photos/."""
    resp = app_client.post(
        "/admin/delete_photo",
        json={"path": "/etc/passwd"},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is False
    assert "Invalid" in data["error"]


def test_download_status_default(app_client):
    """Should return not-downloading state by default."""
    resp = app_client.get("/admin/download_status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["downloading"] is False
    assert data["download_total"] == 0
    assert data["download_completed"] == 0
    assert data["photo_count"] == 0


def test_download_status_during_download(app_client):
    """Should reflect download progress from DB settings."""
    import db
    db.set_setting("downloading", True)
    db.set_setting("download_total", 10)
    db.set_setting("download_completed", 3)

    resp = app_client.get("/admin/download_status")
    data = resp.get_json()
    assert data["downloading"] is True
    assert data["download_total"] == 10
    assert data["download_completed"] == 3


def test_done_redirects_to_admin(app_client):
    """The /done route should redirect to /admin."""
    resp = app_client.get("/done")
    assert resp.status_code == 302
    assert "/admin" in resp.headers["Location"]


def test_get_next_photos_empty(app_client):
    """Should return empty list when no photos."""
    resp = app_client.get("/get_next_photos")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == []


def test_get_next_photos_sequential(app_client, monkeypatch):
    """Should return photos in order from DB."""
    import db
    import config

    # Create actual photo files so walk_photos can find them
    photos_dir = config.PHOTOS_DIR
    upload_dir = os.path.join(photos_dir, "upload")
    os.makedirs(upload_dir, exist_ok=True)

    for name in ["a.jpg", "b.jpg", "c.jpg"]:
        # Write minimal JPEG
        with open(os.path.join(upload_dir, name), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        db.add_photo(name, subdir="upload", uploaded_by="admin")

    db.set_setting("current_index", 0)

    resp = app_client.get("/get_next_photos?count=2")
    data = resp.get_json()
    assert len(data) == 2

    # Next call should continue from where we left off
    resp = app_client.get("/get_next_photos?count=2")
    data = resp.get_json()
    assert len(data) == 2
