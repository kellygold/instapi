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
. "$SCRIPT_DIR/usb-gadget-helper.sh"

# Check if this Pi is in USB display mode (file or service fallback)
_is_usb_mode() {
    [ -f "$MODE_FILE" ] && grep -q "usb" "$MODE_FILE" && return 0
    [ -f /etc/systemd/system/usb-gadget.service ] && return 0
    return 1
}

# 1. Check internet connectivity (skip if in AP mode)
if [ -f "$WIFI_MODE_FILE" ] && grep -q "ap" "$WIFI_MODE_FILE"; then
    $LOG "In AP mode (WiFi setup), skipping connectivity check"
else
    if ! ping -c 1 -W 5 8.8.8.8 > /dev/null 2>&1; then
        $LOG "Internet unreachable, attempting recovery"
        nmcli device wifi rescan 2>/dev/null || true
        nmcli device connect wlan0 2>/dev/null || true
        sleep 5
        if ! ping -c 1 -W 5 8.8.8.8 > /dev/null 2>&1; then
            $LOG "Still down after nmcli reconnect attempt"
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
                    VENV_PY="$INSTAPI_DIR/app/venv/bin/python3"
                    if [ -f "$VENV_PY" ]; then
                        "$VENV_PY" "$SCRIPT_DIR/generate-wifi-fix-image.py" "$WIFI_FIX_IMAGE" 2>&1 || $LOG "wifi-fix image generation failed"
                    else
                        python3 "$SCRIPT_DIR/generate-wifi-fix-image.py" "$WIFI_FIX_IMAGE" 2>&1 || $LOG "wifi-fix image generation failed (no venv)"
                    fi
                fi

                # In USB mode, swap USB contents to show the fix image
                if _is_usb_mode; then
                    USER_HOME="$(dirname "$INSTAPI_DIR")"
                    IMG_FILE="$USER_HOME/usb_drive.img"
                    MOUNT_POINT="$USER_HOME/usb_mount"
                    STAGING="$USER_HOME/usb_staging"

                    # Back up current USB image for instant restore later
                    [ -f "$IMG_FILE" ] && [ ! -f "${IMG_FILE}.bak" ] && cp "$IMG_FILE" "${IMG_FILE}.bak"

                    rm -rf "$STAGING" && mkdir -p "$STAGING"
                    [ -f "$WIFI_FIX_IMAGE" ] && cp "$WIFI_FIX_IMAGE" "$STAGING/"
                    usb_prepare_and_swap "$IMG_FILE" "$MOUNT_POINT" "$STAGING" true
                    rm -rf "$STAGING"
                    $LOG "USB swapped to WiFi fix image"
                fi

                # Start AP mode
                sudo "$SCRIPT_DIR/wifi-setup.sh" start-ap
                echo "0" > "$WIFI_FAIL_COUNT_FILE"
                touch /tmp/instapi_ap_recovery
            fi
        fi
    else
        # WiFi is up — reset fail counter
        echo "0" > "$WIFI_FAIL_COUNT_FILE"

        # If recovering from AP mode, restore photos to USB
        if [ -f /tmp/instapi_ap_recovery ]; then
            rm -f /tmp/instapi_ap_recovery
            if _is_usb_mode; then
                USER_HOME="$(dirname "$INSTAPI_DIR")"
                IMG_FILE="$USER_HOME/usb_drive.img"
                if [ -f "${IMG_FILE}.bak" ]; then
                    $LOG "WiFi restored — instant USB restore from backup"
                    usb_gadget_stop
                    cp "${IMG_FILE}.bak" "$IMG_FILE"
                    usb_gadget_start "$IMG_FILE"
                    rm -f "${IMG_FILE}.bak"
                else
                    $LOG "WiFi restored after AP recovery, restoring photos to USB"
                    sudo "$SCRIPT_DIR/update-photos.sh"
                fi
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
if _is_usb_mode; then
    if ! systemctl is-active --quiet usb-gadget; then
        $LOG "usb-gadget down, restarting"
        sudo systemctl restart usb-gadget
    fi
fi

# 5. Check Flask is actually responding
if ! curl -s -o /dev/null --max-time 5 http://localhost:3000/ 2>/dev/null; then
    $LOG "Flask not responding, restarting instapi"
    sudo systemctl restart instapi
    # In USB mode, refresh the frame after Flask restart
    if _is_usb_mode; then
        sleep 5
        $LOG "Refreshing USB frame after Flask restart"
        sudo systemctl restart usb-gadget
    fi
fi

# 6. Verify USB drive actually has photos (self-correcting)
if _is_usb_mode; then
    USER_HOME="$(dirname "$INSTAPI_DIR")"
    IMG_FILE="$USER_HOME/usb_drive.img"
    MOUNT_POINT="$USER_HOME/usb_mount"
    PHOTOS_DIR="$INSTAPI_DIR/app/static/photos"

    # Only check if gadget is loaded and no update is in progress
    if ! lsmod | grep -q g_mass_storage; then
        : # Gadget not loaded, skip
    elif ! flock -n /tmp/usb_update.lock true 2>/dev/null; then
        $LOG "USB update in progress, skipping photo count check"
    else
        # Temporarily stop gadget, mount, count, unmount, restart
        usb_gadget_stop
        if usb_mount "$IMG_FILE" "$MOUNT_POINT" 2>/dev/null; then
            USB_PHOTO_COUNT=$(find "$MOUNT_POINT" -maxdepth 1 -type f \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" \) ! -name "qr-placeholder.jpg" ! -name "wifi-fix.jpg" 2>/dev/null | wc -l)
            usb_unmount "$MOUNT_POINT" 2>/dev/null
        else
            USB_PHOTO_COUNT=-1
        fi
        usb_gadget_start "$IMG_FILE"

        if [ "$USB_PHOTO_COUNT" -ge 0 ]; then
            DISK_PHOTO_COUNT=$(find "$PHOTOS_DIR" -maxdepth 3 -type f \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" \) ! -path "*/thumbs/*" 2>/dev/null | wc -l)

            if [ "$USB_PHOTO_COUNT" -eq 0 ] && [ "$DISK_PHOTO_COUNT" -gt 0 ]; then
                $LOG "SELF-HEAL: USB has 0 photos but disk has $DISK_PHOTO_COUNT — triggering update"
                sudo "$SCRIPT_DIR/update-photos.sh" &
            elif [ "$USB_PHOTO_COUNT" -gt 0 ] && [ "$DISK_PHOTO_COUNT" -gt 0 ]; then
                DIFF=$((DISK_PHOTO_COUNT - USB_PHOTO_COUNT))
                if [ "$DIFF" -gt "$((DISK_PHOTO_COUNT / 5))" ]; then
                    $LOG "SELF-HEAL: USB has $USB_PHOTO_COUNT but disk has $DISK_PHOTO_COUNT — triggering update"
                    sudo "$SCRIPT_DIR/update-photos.sh" &
                fi
            fi
        fi
    fi

    # 7. Re-seat USB gadget periodically to work around frame caching bugs
    GADGET_UPTIME_FILE="/tmp/instapi_gadget_start"
    if lsmod | grep -q g_mass_storage; then
        if [ ! -f "$GADGET_UPTIME_FILE" ]; then
            echo "$(date +%s)" > "$GADGET_UPTIME_FILE"
        else
            GADGET_START=$(cat "$GADGET_UPTIME_FILE")
            NOW=$(date +%s)
            GADGET_AGE=$(( NOW - GADGET_START ))
            # Re-seat every 6 hours to work around frame caching bugs
            if [ "$GADGET_AGE" -gt 21600 ]; then
                $LOG "Periodic USB re-seat (${GADGET_AGE}s uptime)"
                usb_gadget_stop
                usb_gadget_start "$IMG_FILE"
                echo "$(date +%s)" > "$GADGET_UPTIME_FILE"
            fi
        fi
    fi
fi
