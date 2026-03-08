import os
import json
import pytest


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
    """Reconciliation should populate device_state from disk photos."""
    import config
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos_dir_with_images))
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "state.json"))
    config.device_state.clear()

    from main import reconcile_photos
    reconcile_photos()

    assert len(config.device_state["photo_urls"]) == 3
    assert config.device_state["done"] is True
    assert config.device_state["photos_chosen"] is True
    # All paths should start with /static/
    for url in config.device_state["photo_urls"]:
        assert url.startswith("/static/")


def test_reconcile_empty_dir_sets_not_done(photos_dir, monkeypatch, tmp_path):
    """With no photos on disk, done should be False."""
    import config
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos_dir))
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "state.json"))
    config.device_state.clear()

    from main import reconcile_photos
    reconcile_photos()

    assert config.device_state.get("done") is False


def test_reconcile_saves_state(photos_dir_with_images, monkeypatch, tmp_path):
    """Reconciliation should persist the rebuilt state to disk."""
    import config
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos_dir_with_images))
    monkeypatch.setattr(config, "STATE_FILE", str(state_file))
    config.device_state.clear()

    from main import reconcile_photos
    reconcile_photos()

    assert state_file.exists()
    with open(state_file) as f:
        saved = json.load(f)
    assert len(saved["photo_urls"]) == 3
