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


def test_check_session_status_ready(app_client, monkeypatch):
    """Should report ready when photos are done."""
    import config
    config.device_state["done"] = True
    config.device_state["photo_urls"] = ["/static/photos/test.jpg"]

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


def test_admin_switch_mode_valid(app_client, tmp_path, monkeypatch):
    """switch_mode should not crash (was NameError before fix)."""
    import routes.admin_routes as admin
    mode_file = str(tmp_path / ".display_mode")
    monkeypatch.setattr(admin, "MODE_FILE", mode_file)

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


def test_get_next_photos_empty(app_client):
    """Should return empty list when no photos."""
    resp = app_client.get("/get_next_photos")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == []


def test_get_next_photos_sequential(app_client):
    """Should return photos in order."""
    import config
    config.device_state["photo_urls"] = ["/a.jpg", "/b.jpg", "/c.jpg"]
    config.device_state["current_index"] = 0

    resp = app_client.get("/get_next_photos?count=2")
    data = resp.get_json()
    assert data == ["/a.jpg", "/b.jpg"]

    # Next call should continue from where we left off
    resp = app_client.get("/get_next_photos?count=2")
    data = resp.get_json()
    assert data == ["/c.jpg", "/a.jpg"]  # wraps around
