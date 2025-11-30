# config.py
import os

# NOTE: As of March 31, 2025, photoslibrary.readonly was REMOVED by Google.
# You can no longer browse user albums - only the Photo Picker works now.
SCOPES = [
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
]

PICKER_API_BASE_URL = "https://photospicker.googleapis.com/v1"

device_state = {}

PHOTOS_DIR = os.path.join(os.getcwd(), 'static', 'photos')
