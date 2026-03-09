# config.py
import os
import json

SCOPES = [
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
    "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
    "https://www.googleapis.com/auth/photoslibrary.edit.appcreateddata",
]

PICKER_API_BASE_URL = "https://photospicker.googleapis.com/v1"
LIBRARY_API_BASE_URL = "https://photoslibrary.googleapis.com/v1"

device_state = {}

PHOTOS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'photos')
STATE_FILE = os.path.join(os.path.dirname(__file__), 'device_state.json')

# Keys safe to persist
# refresh_token is long-lived and needed for auto-sync (access tokens are short-lived and NOT stored)
_PERSISTABLE_KEYS = {"photo_urls", "done", "photos_chosen", "current_index",
                     "refresh_token", "album_id", "synced_media_ids",
                     "upload_token"}


def save_device_state():
    """Persist device_state to disk (only safe keys, no credentials)."""
    try:
        data = {k: v for k, v in device_state.items() if k in _PERSISTABLE_KEYS}
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving device state: {e}")


def load_device_state():
    """Load device_state from disk."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k in _PERSISTABLE_KEYS:
                    device_state[k] = v
    except Exception as e:
        print(f"Error loading device state: {e}")


load_device_state()

# Slideshow config
SLIDESHOW_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'slideshow_config.json')

DEFAULT_SLIDESHOW_CONFIG = {
    "slide_duration": 5,
    "transition": "fade",
    "shuffle": False,
    "ken_burns": False
}

def load_slideshow_config():
    """Load slideshow settings from JSON file."""
    try:
        if os.path.exists(SLIDESHOW_CONFIG_PATH):
            with open(SLIDESHOW_CONFIG_PATH, 'r') as f:
                return {**DEFAULT_SLIDESHOW_CONFIG, **json.load(f)}
    except Exception as e:
        print(f"Error loading slideshow config: {e}")
    return DEFAULT_SLIDESHOW_CONFIG.copy()

def save_slideshow_config(config):
    """Save slideshow settings to JSON file."""
    try:
        with open(SLIDESHOW_CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving slideshow config: {e}")
        return False
