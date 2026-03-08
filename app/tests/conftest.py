import os
import sys
import json
import pytest

# Add app directory to path so imports work
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

# Change cwd to app dir so secrets.json is found by modules that do open("secrets.json")
os.chdir(APP_DIR)


@pytest.fixture(autouse=True)
def mock_secrets():
    """Create a mock secrets.json so modules can import without real credentials."""
    secrets = {
        "web": {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "redirect_uris": ["http://localhost:3000/oauth2callback"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }
    secrets_path = os.path.join(APP_DIR, "secrets.json")
    had_secrets = os.path.exists(secrets_path)

    if not had_secrets:
        with open(secrets_path, "w") as f:
            json.dump(secrets, f)

    yield secrets

    if not had_secrets and os.path.exists(secrets_path):
        os.remove(secrets_path)


@pytest.fixture
def photos_dir(tmp_path):
    """Create a temporary photos directory with some test images."""
    photos = tmp_path / "static" / "photos"
    photos.mkdir(parents=True)
    return photos


@pytest.fixture
def photos_dir_with_images(photos_dir):
    """Photos directory pre-populated with fake image files."""
    picker_dir = photos_dir / "picker"
    picker_dir.mkdir()
    for i in range(3):
        (picker_dir / f"picker_{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return photos_dir


@pytest.fixture
def app_client(monkeypatch, tmp_path, mock_secrets):
    """Flask test client with isolated config and routes registered."""
    # Set up isolated photos dir and state file
    photos = tmp_path / "static" / "photos"
    photos.mkdir(parents=True)
    state_file = tmp_path / "device_state.json"
    display_mode_file = tmp_path / ".display_mode"
    display_mode_file.write_text("usb")

    import config
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos))
    monkeypatch.setattr(config, "STATE_FILE", str(state_file))
    monkeypatch.setattr(config, "SLIDESHOW_CONFIG_PATH", str(tmp_path / "slideshow_config.json"))

    # Clear device state for each test
    config.device_state.clear()

    from app import app

    # Import route modules to register routes with the app
    import routes.base_routes
    import routes.picker_routes
    import routes.admin_routes

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

    config.device_state.clear()
