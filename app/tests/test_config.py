import os
import json
import pytest


def test_photos_dir_uses_file_not_getcwd():
    """PHOTOS_DIR must use __file__ so it works regardless of cwd."""
    import config
    # Should be relative to config.py, not os.getcwd()
    expected_base = os.path.dirname(os.path.abspath(config.__file__))
    expected = os.path.join(expected_base, "static", "photos")
    assert os.path.abspath(config.PHOTOS_DIR) == expected


def test_save_and_load_device_state(tmp_path, monkeypatch):
    """State should round-trip through save/load."""
    import config

    state_file = str(tmp_path / "device_state.json")
    monkeypatch.setattr(config, "STATE_FILE", state_file)

    config.device_state.clear()
    config.device_state["photo_urls"] = ["/static/photos/picker/test.jpg"]
    config.device_state["done"] = True
    config.device_state["current_index"] = 2

    config.save_device_state()

    # Verify file was written
    assert os.path.exists(state_file)
    with open(state_file) as f:
        saved = json.load(f)
    assert saved["photo_urls"] == ["/static/photos/picker/test.jpg"]
    assert saved["done"] is True
    assert saved["current_index"] == 2

    # Clear and reload
    config.device_state.clear()
    config.load_device_state()
    assert config.device_state["photo_urls"] == ["/static/photos/picker/test.jpg"]
    assert config.device_state["done"] is True


def test_save_device_state_excludes_credentials(tmp_path, monkeypatch):
    """Credentials must never be persisted to disk."""
    import config

    state_file = str(tmp_path / "device_state.json")
    monkeypatch.setattr(config, "STATE_FILE", state_file)

    config.device_state.clear()
    config.device_state["credentials"] = {"token": "secret-token"}
    config.device_state["photo_urls"] = ["/static/photos/test.jpg"]
    config.device_state["done"] = True

    config.save_device_state()

    with open(state_file) as f:
        saved = json.load(f)
    assert "credentials" not in saved
    assert "photo_urls" in saved


def test_load_slideshow_config_defaults(tmp_path, monkeypatch):
    """Should return defaults when no config file exists."""
    import config
    monkeypatch.setattr(config, "SLIDESHOW_CONFIG_PATH", str(tmp_path / "nonexistent.json"))
    defaults = config.load_slideshow_config()
    assert defaults["slide_duration"] == 5
    assert defaults["transition"] == "fade"
    assert defaults["shuffle"] is False
    assert defaults["ken_burns"] is False


def test_load_device_state_ignores_unknown_keys(tmp_path, monkeypatch):
    """Loading state with extra keys should not inject them."""
    import config

    state_file = str(tmp_path / "device_state.json")
    monkeypatch.setattr(config, "STATE_FILE", state_file)

    with open(state_file, "w") as f:
        json.dump({"photo_urls": [], "evil_key": "injected"}, f)

    config.device_state.clear()
    config.load_device_state()
    assert "evil_key" not in config.device_state
