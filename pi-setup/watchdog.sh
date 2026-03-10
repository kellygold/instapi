#!/bin/bash
# InstaPi Watchdog — monitors all services and self-heals
# Runs via cron every 5 minutes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
LOG="logger -t instapi-watchdog"

# 1. Check internet connectivity
if ! ping -c 1 -W 5 8.8.8.8 > /dev/null 2>&1; then
    $LOG "Internet unreachable, attempting recovery"
    wpa_cli -i wlan0 reconfigure > /dev/null 2>&1
    sleep 5
    if ! ping -c 1 -W 5 8.8.8.8 > /dev/null 2>&1; then
        $LOG "Still down, restarting dhcpcd"
        sudo systemctl restart dhcpcd
    fi
fi

# 2. Check instapi service
if ! systemctl is-active --quiet instapi; then
    $LOG "instapi down, restarting"
    sudo systemctl restart instapi
fi

# 3. Check ngrok service
if ! systemctl is-active --quiet ngrok; then
    $LOG "ngrok down, restarting"
    sudo systemctl restart ngrok
fi

# 4. Check usb-gadget (USB mode only)
MODE_FILE="$INSTAPI_DIR/.display_mode"
if [ -f "$MODE_FILE" ] && grep -q usb "$MODE_FILE"; then
    if ! systemctl is-active --quiet usb-gadget; then
        $LOG "usb-gadget down, restarting"
        sudo systemctl restart usb-gadget
    fi
fi

# 5. Check Flask is actually responding
if ! curl -s -o /dev/null --max-time 5 http://localhost:3000/ 2>/dev/null; then
    $LOG "Flask not responding, restarting instapi"
    sudo systemctl restart instapi
fi
