# config.py
import os
import json

# NOTE: As of March 31, 2025, photoslibrary.readonly was REMOVED by Google.
# You can no longer browse user albums - only the Photo Picker works now.
SCOPES = [
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
]

PICKER_API_BASE_URL = "https://photospicker.googleapis.com/v1"

device_state = {}

PHOTOS_DIR = os.path.join(os.getcwd(), 'static', 'photos')

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
