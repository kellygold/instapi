# config.py
import os

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
