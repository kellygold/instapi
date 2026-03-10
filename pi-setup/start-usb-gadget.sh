#!/bin/bash
# Start USB Mass Storage Gadget
# Called by systemd on boot

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

# Mount the disk image so we can write to it
sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Sync photos from app to USB drive
if [ -d "$PHOTOS_DIR" ] && [ -n "$(ls -A $PHOTOS_DIR 2>/dev/null)" ]; then
    cp "$PHOTOS_DIR"/*.jpg "$MOUNT_POINT"/ 2>/dev/null || true
    cp "$PHOTOS_DIR"/*.jpeg "$MOUNT_POINT"/ 2>/dev/null || true
    cp "$PHOTOS_DIR"/*.png "$MOUNT_POINT"/ 2>/dev/null || true
fi

# If no photos yet, copy the QR code placeholder
if [ -z "$(ls -A $MOUNT_POINT/*.jpg 2>/dev/null)" ]; then
    if [ -f "$QR_PLACEHOLDER" ]; then
        cp "$QR_PLACEHOLDER" "$MOUNT_POINT"/
        echo "Copied QR placeholder"
    else
        echo "WARNING: No QR placeholder found at $QR_PLACEHOLDER"
    fi
fi

# Sync and unmount (frame needs exclusive access)
sync
sudo umount "$MOUNT_POINT"

# Load the USB mass storage gadget
sudo modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1 ro=0

echo "USB Gadget started - Pi is now a USB drive"
