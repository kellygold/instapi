# config.py
import os
import json

SCOPES = [
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
]

PICKER_API_BASE_URL = "https://photospicker.googleapis.com/v1"

# Sync constants
DEFAULT_SYNC_INTERVAL = 1800  # 30 minutes
SYNC_DIR_NAME = "sync"

PHOTOS_DIR = os.environ.get('INSTAPI_PHOTOS_DIR',
             os.path.join(os.path.dirname(__file__), 'static', 'photos'))
STATE_FILE = os.environ.get('INSTAPI_STATE_FILE',
             os.path.join(os.path.dirname(__file__), 'device_state.json'))

# Shared constants -- single source of truth
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif')
THUMBNAIL_SIZE = (200, 200)
THUMBNAIL_QUALITY = 60
MODE_FILE = os.path.join(os.path.dirname(__file__), '..', '.display_mode')

# Secrets loading -- lazy singleton, loaded once on first access
SECRETS_PATH = os.environ.get('INSTAPI_SECRETS_PATH',
               os.path.join(os.path.dirname(__file__), 'secrets.json'))

_secrets_cache = None

def get_secrets():
    """Load secrets.json once, cache result."""
    global _secrets_cache
    if _secrets_cache is None:
        try:
            with open(SECRETS_PATH) as f:
                _secrets_cache = json.load(f)
        except FileNotFoundError:
            _secrets_cache = {}
    return _secrets_cache

def get_redirect_uri():
    """Get the first redirect URI from secrets.json."""
    uris = get_secrets().get("web", {}).get("redirect_uris", [])
    return uris[0] if uris else ""

def get_base_url():
    """Derive base URL from redirect URI (e.g. https://xxx.ngrok-free.dev)."""
    uri = get_redirect_uri()
    return uri.rsplit("/", 1)[0] if uri else ""

def get_flask_secret():
    """Get Flask secret key from secrets.json, with fallback."""
    return get_secrets().get("flask_secret", None)

# Slideshow config (backed by db.py settings table)
DEFAULT_SLIDESHOW_CONFIG = {
    "slideshow_slide_duration": 5,
    "slideshow_transition": "fade",
    "slideshow_shuffle": False,
    "slideshow_ken_burns": False,
}

def load_slideshow_config():
    """Load slideshow settings from DB."""
    from db import get_setting
    result = {}
    for key, default in DEFAULT_SLIDESHOW_CONFIG.items():
        short_key = key.replace("slideshow_", "")
        result[short_key] = get_setting(key, default)
    return result

def save_slideshow_config(config):
    """Save slideshow settings to DB."""
    from db import set_setting
    for key, value in config.items():
        set_setting(f"slideshow_{key}", value)
    return True
