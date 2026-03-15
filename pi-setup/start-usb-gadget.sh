#!/bin/bash
# Start USB Mass Storage Gadget
# Called by systemd on boot — syncs photos incrementally, then loads gadget

# Derive paths from script location (systemd doesn't set $HOME reliably)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
USER_HOME="$(dirname "$INSTAPI_DIR")"

IMG_FILE="$USER_HOME/usb_drive.img"
MOUNT_POINT="$USER_HOME/usb_mount"
PHOTOS_DIR="$INSTAPI_DIR/app/static/photos"
QR_PLACEHOLDER="$INSTAPI_DIR/pi-setup/qr-placeholder.jpg"

echo "Using paths: IMG=$IMG_FILE, MOUNT=$MOUNT_POINT"

# Create mount point if needed
mkdir -p "$MOUNT_POINT"

# Mount the existing disk image (photos from last session are preserved)
sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Incremental sync: add new photos, skip existing ones
ADDED=0
for subdir in "" upload picker album sync sync/picker sync/upload; do
    dir="$PHOTOS_DIR"
    [ -n "$subdir" ] && dir="$PHOTOS_DIR/$subdir"
    [ -d "$dir" ] || continue
    for ext in jpg jpeg png; do
        for f in "$dir"/*."$ext"; do
            [ -f "$f" ] || continue
            dest="$MOUNT_POINT/$(basename "$f")"
            if [ ! -f "$dest" ]; then
                cp "$f" "$dest" 2>/dev/null && ADDED=$((ADDED + 1))
            fi
        done
    done
done
[ "$ADDED" -gt 0 ] && echo "Added $ADDED new photos to USB"

# If no photos at all, show QR placeholder
PHOTO_COUNT=$(ls -1 "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png 2>/dev/null | wc -l)
if [ "$PHOTO_COUNT" -eq 0 ]; then
    if [ -f "$QR_PLACEHOLDER" ]; then
        cp "$QR_PLACEHOLDER" "$MOUNT_POINT"/
        echo "Copied QR placeholder"
    else
        echo "WARNING: No QR placeholder found at $QR_PLACEHOLDER"
    fi
fi

# Watermark new USB copies with this Pi's unique QR code
if [ "$ADDED" -gt 0 ]; then
    echo "Watermarking new photos..."
    APP_DIR="$INSTAPI_DIR/app"
    VENV="$APP_DIR/venv/bin/python3"
    if [ -f "$VENV" ]; then
        cd "$APP_DIR"
        "$VENV" -c "
from utils import add_qr_watermark
import glob, os
mount = '$MOUNT_POINT'
for f in glob.glob(os.path.join(mount, '*.jpg')) + glob.glob(os.path.join(mount, '*.jpeg')) + glob.glob(os.path.join(mount, '*.png')):
    if 'qr-placeholder' not in f:
        add_qr_watermark(f)
print(f'Watermarked photos on USB')
" 2>/dev/null || echo "Watermark failed (non-fatal)"
    fi
fi

# Sync and unmount (frame needs exclusive access)
sync
sudo umount "$MOUNT_POINT"

# Load the USB mass storage gadget
sudo modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1 ro=0

echo "USB Gadget started - Pi is now a USB drive ($PHOTO_COUNT photos)"
