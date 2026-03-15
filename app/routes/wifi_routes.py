# routes/wifi_routes.py
import os
import json
import subprocess
from flask import jsonify, request, redirect, render_template
from app import app

WIFI_MODE_FILE = "/tmp/instapi_wifi_mode"
WIFI_SCAN_FILE = "/tmp/wifi_scan.json"
WIFI_SETUP_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "pi-setup", "wifi-setup.sh")
SUDO = "/usr/bin/sudo"


def is_ap_mode():
    """Check if Pi is in AP mode (serving setup hotspot)."""
    try:
        with open(WIFI_MODE_FILE) as f:
            return f.read().strip() == "ap"
    except FileNotFoundError:
        return False


@app.before_request
def wifi_setup_redirect():
    """In AP mode, redirect everything to the wifi-setup page."""
    if not is_ap_mode():
        return None
    # Allow wifi-setup routes, static files, and captive portal triggers
    allowed = ("/wifi-setup", "/static/", "/hotspot-detect", "/generate_204",
               "/connecttest", "/favicon")
    if any(request.path.startswith(p) for p in allowed):
        return None
    return redirect("/wifi-setup")


# --- Captive portal triggers ---
# iOS, Android, and Windows probe these URLs to detect captive portals.
# By redirecting instead of returning the expected response, we trigger
# the OS captive portal sheet.

@app.route("/hotspot-detect.html")
def captive_apple():
    return redirect("/wifi-setup")


@app.route("/generate_204")
def captive_android():
    return redirect("/wifi-setup")


@app.route("/connecttest.txt")
def captive_windows():
    return redirect("/wifi-setup")


# Also catch the common Android variant
@app.route("/gen_204")
def captive_android_alt():
    return redirect("/wifi-setup")


# --- WiFi setup page ---

@app.route("/wifi-setup")
def wifi_setup_page():
    """Render the WiFi setup page."""
    kiosk = request.args.get("kiosk", "0") == "1"
    return render_template("wifi_setup.html", kiosk=kiosk)


@app.route("/wifi-setup/scan")
def wifi_scan():
    """Return cached WiFi scan results."""
    try:
        with open(WIFI_SCAN_FILE) as f:
            networks = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        networks = []
    return jsonify(networks)


@app.route("/wifi-setup/scan", methods=["POST"])
def wifi_rescan():
    """Trigger a new WiFi scan."""
    try:
        subprocess.run(
            [SUDO, WIFI_SETUP_SCRIPT, "scan"],
            timeout=15, capture_output=True
        )
        with open(WIFI_SCAN_FILE) as f:
            networks = json.load(f)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(networks)


@app.route("/wifi-setup/connect", methods=["POST"])
def wifi_connect():
    """Connect to a WiFi network."""
    data = request.get_json()
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "")

    if not ssid:
        return jsonify({"success": False, "error": "SSID required"})

    try:
        # Pass SSID and password as separate arguments (no shell injection)
        args = [SUDO, WIFI_SETUP_SCRIPT, "connect", ssid]
        if password:
            args.append(password)

        result = subprocess.run(
            args, timeout=30, capture_output=True, text=True
        )

        if result.returncode == 0:
            return jsonify({"success": True})
        else:
            return jsonify({
                "success": False,
                "error": "Could not connect. Check your password and try again."
            })
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "error": "Connection timed out. Please try again."
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/wifi-setup/status")
def wifi_status():
    """Return current WiFi state."""
    return jsonify({
        "ap_mode": is_ap_mode(),
    })
