#!/bin/bash
# Start USB Mass Storage Gadget
# Called by systemd on boot — prepares photos in staging, then starts gadget.

# Derive paths from script location (systemd doesn't set $HOME reliably)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
USER_HOME="$(dirname "$INSTAPI_DIR")"

IMG_FILE="$USER_HOME/usb_drive.img"
MOUNT_POINT="$USER_HOME/usb_mount"
PHOTOS_DIR="$INSTAPI_DIR/app/static/photos"
QR_PLACEHOLDER="$INSTAPI_DIR/pi-setup/qr-placeholder.jpg"
STAGING="$USER_HOME/usb_staging"

. "$SCRIPT_DIR/usb-gadget-helper.sh"

echo "Using paths: IMG=$IMG_FILE, MOUNT=$MOUNT_POINT"

# ============================================================
# Prepare staging directory
# ============================================================
rm -rf "$STAGING"
mkdir -p "$STAGING"

# Check WiFi mode — if AP mode, stage wifi-fix image only
WIFI_MODE_FILE="/tmp/instapi_wifi_mode"
if [ -f "$WIFI_MODE_FILE" ] && grep -q "ap" "$WIFI_MODE_FILE"; then
    echo "WiFi is in AP mode — staging setup instructions"
    WIFI_FIX="$SCRIPT_DIR/wifi-fix.jpg"
    if [ -f "$WIFI_FIX" ]; then
        cp "$WIFI_FIX" "$STAGING/"
    fi
    usb_prepare_and_swap "$IMG_FILE" "$MOUNT_POINT" "$STAGING"
    rm -rf "$STAGING"
    echo "USB showing WiFi fix image"
    exit 0
fi

# Stage all photos
NEW_FILES=""
for subdir in "" upload picker album sync sync/picker sync/upload; do
    dir="$PHOTOS_DIR"
    [ -n "$subdir" ] && dir="$PHOTOS_DIR/$subdir"
    [ -d "$dir" ] || continue
    for ext in jpg jpeg png; do
        for f in "$dir"/*."$ext"; do
            [ -f "$f" ] || continue
            fname=$(basename "$f")
            [ -f "$STAGING/$fname" ] && continue
            cp "$f" "$STAGING/$fname"
            NEW_FILES="$NEW_FILES $fname"
        done
    done
done

# Count photos
PHOTO_COUNT=$(ls -1 "$STAGING"/*.jpg "$STAGING"/*.jpeg "$STAGING"/*.png 2>/dev/null | wc -l)

if [ "$PHOTO_COUNT" -eq 0 ]; then
    if [ -f "$QR_PLACEHOLDER" ]; then
        cp "$QR_PLACEHOLDER" "$STAGING/"
        echo "Copied QR placeholder"
    fi
fi

# Watermark new photos in staging
if [ -n "$NEW_FILES" ]; then
    ADDED=$(echo "$NEW_FILES" | wc -w)
    echo "Watermarking $ADDED photos..."
    usb_watermark "$STAGING" "$NEW_FILES"
fi

# ============================================================
# Swap: start gadget with prepared content
# ============================================================
usb_prepare_and_swap "$IMG_FILE" "$MOUNT_POINT" "$STAGING"
rm -rf "$STAGING"

echo "USB Gadget started ($PHOTO_COUNT photos)"
