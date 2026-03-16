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


def test_load_slideshow_config_defaults(tmp_path, monkeypatch):
    """Should return defaults when no settings exist in DB."""
    import config
    import db

    # Use isolated DB
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    defaults = config.load_slideshow_config()
    assert defaults["slide_duration"] == 5
    assert defaults["transition"] == "fade"
    assert defaults["shuffle"] is False
    assert defaults["ken_burns"] is False

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_save_and_load_slideshow_config(tmp_path, monkeypatch):
    """Slideshow config should round-trip through save/load."""
    import config
    import db

    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    config.save_slideshow_config({
        "slide_duration": 10,
        "transition": "slide",
        "shuffle": True,
        "ken_burns": True,
    })

    loaded = config.load_slideshow_config()
    assert loaded["slide_duration"] == 10
    assert loaded["transition"] == "slide"
    assert loaded["shuffle"] is True
    assert loaded["ken_burns"] is True

    if hasattr(db._local, 'conn') and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_image_extensions_constant():
    """IMAGE_EXTENSIONS should include the standard image formats."""
    import config
    assert '.jpg' in config.IMAGE_EXTENSIONS
    assert '.jpeg' in config.IMAGE_EXTENSIONS
    assert '.png' in config.IMAGE_EXTENSIONS
    assert '.gif' in config.IMAGE_EXTENSIONS


def test_secrets_lazy_loading(tmp_path, monkeypatch):
    """Secrets should be loaded lazily and cached."""
    import config

    # Point to a test secrets file
    test_secrets = {"web": {"redirect_uris": ["http://test.local/callback"]}, "flask_secret": "test123"}
    secrets_file = tmp_path / "test_secrets.json"
    secrets_file.write_text(json.dumps(test_secrets))

    monkeypatch.setattr(config, "SECRETS_PATH", str(secrets_file))
    config._secrets_cache = None  # Force reload

    secrets = config.get_secrets()
    assert secrets["flask_secret"] == "test123"
    assert config.get_redirect_uri() == "http://test.local/callback"
    assert config.get_base_url() == "http://test.local"
    assert config.get_flask_secret() == "test123"

    config._secrets_cache = None  # Reset


def test_secrets_missing_file(tmp_path, monkeypatch):
    """Missing secrets.json should return empty dict, not crash."""
    import config
    monkeypatch.setattr(config, "SECRETS_PATH", str(tmp_path / "nonexistent.json"))
    config._secrets_cache = None

    secrets = config.get_secrets()
    assert secrets == {}
    assert config.get_redirect_uri() == ""
    assert config.get_base_url() == ""
    assert config.get_flask_secret() is None

    config._secrets_cache = None
