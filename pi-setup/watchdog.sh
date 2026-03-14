#!/bin/bash
# InstaPi Watchdog — monitors all services and self-heals
# Runs via cron every 5 minutes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
LOG="logger -t instapi-watchdog"
WIFI_MODE_FILE="/tmp/instapi_wifi_mode"
WIFI_FAIL_COUNT_FILE="/tmp/wifi_fail_count"
WIFI_FIX_IMAGE="$SCRIPT_DIR/wifi-fix.jpg"
MODE_FILE="$INSTAPI_DIR/.display_mode"

# 1. Check internet connectivity (skip if in AP mode)
if [ -f "$WIFI_MODE_FILE" ] && grep -q "ap" "$WIFI_MODE_FILE"; then
    $LOG "In AP mode (WiFi setup), skipping connectivity check"
else
    if ! ping -c 1 -W 5 8.8.8.8 > /dev/null 2>&1; then
        $LOG "Internet unreachable, attempting recovery"
        wpa_cli -i wlan0 reconfigure > /dev/null 2>&1
        sleep 5
        if ! ping -c 1 -W 5 8.8.8.8 > /dev/null 2>&1; then
            $LOG "Still down, restarting dhcpcd"
            sudo systemctl restart dhcpcd
            sleep 5

            # Track consecutive failures
            FAIL_COUNT=0
            [ -f "$WIFI_FAIL_COUNT_FILE" ] && FAIL_COUNT=$(cat "$WIFI_FAIL_COUNT_FILE")
            FAIL_COUNT=$((FAIL_COUNT + 1))
            echo "$FAIL_COUNT" > "$WIFI_FAIL_COUNT_FILE"

            if [ "$FAIL_COUNT" -ge 3 ]; then
                $LOG "WiFi down for 3+ checks, escalating to AP mode"

                # Generate WiFi fix image if not already present
                if [ ! -f "$WIFI_FIX_IMAGE" ]; then
                    python3 "$SCRIPT_DIR/generate-wifi-fix-image.py" "$WIFI_FIX_IMAGE" 2>/dev/null || true
                fi

                # In USB mode, swap USB contents to show the fix image
                if [ -f "$MODE_FILE" ] && grep -q usb "$MODE_FILE"; then
                    USER_HOME="$(dirname "$INSTAPI_DIR")"
                    IMG_FILE="$USER_HOME/usb_drive.img"
                    MOUNT_POINT="$USER_HOME/usb_mount"

                    sudo modprobe -r g_mass_storage 2>/dev/null || true
                    sleep 2
                    sudo mkfs.fat -F 32 "$IMG_FILE" > /dev/null 2>&1
                    mkdir -p "$MOUNT_POINT"
                    sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"
                    [ -f "$WIFI_FIX_IMAGE" ] && sudo cp "$WIFI_FIX_IMAGE" "$MOUNT_POINT"/
                    sync
                    sudo umount "$MOUNT_POINT"
                    sudo modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1 ro=0
                    $LOG "USB swapped to WiFi fix image"
                fi

                # Start AP mode
                sudo "$SCRIPT_DIR/wifi-setup.sh" start-ap
                echo "0" > "$WIFI_FAIL_COUNT_FILE"
            fi
        fi
    else
        # WiFi is up — reset fail counter
        if [ -f "$WIFI_FAIL_COUNT_FILE" ]; then
            OLD_COUNT=$(cat "$WIFI_FAIL_COUNT_FILE")
            echo "0" > "$WIFI_FAIL_COUNT_FILE"

            # If we were recovering from AP mode, restore photos
            if [ "$OLD_COUNT" -ge 3 ] && [ -f "$MODE_FILE" ] && grep -q usb "$MODE_FILE"; then
                $LOG "WiFi restored after AP recovery, restoring photos to USB"
                sudo "$SCRIPT_DIR/update-photos.sh"
            fi
        fi
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
