# auth.py
import datetime
import requests
from config import device_state

def refresh_token():
    """Refresh the access token if it's expired or about to expire."""
    credentials = device_state.get("credentials")
    if not credentials or not credentials.get("refresh_token"):
        print("No credentials or refresh token available.")
        return False

    expiry_time = credentials.get("expiry_time")
    if expiry_time and datetime.datetime.utcnow() < expiry_time - datetime.timedelta(minutes=5):
        return True

    refresh_payload = {
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "refresh_token": credentials["refresh_token"],
        "grant_type": "refresh_token",
    }
    response = requests.post(credentials["token_uri"], data=refresh_payload)
    if response.status_code == 200:
        new_token_data = response.json()
        credentials["token"] = new_token_data["access_token"]
        credentials["expiry_time"] = datetime.datetime.utcnow() + datetime.timedelta(
            seconds=new_token_data["expires_in"]
        )
        device_state["credentials"] = credentials
        print("Token refreshed successfully.")
        return True
    else:
        print("Failed to refresh token:", response.status_code, response.text)
        return False
