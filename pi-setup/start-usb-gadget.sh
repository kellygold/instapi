#!/bin/bash
# Start USB Mass Storage Gadget
# Called by systemd on boot

# Get the actual user's home directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_HOME="$(dirname "$(dirname "$SCRIPT_DIR")")"

IMG_FILE="$USER_HOME/usb_drive.img"
MOUNT_POINT="$USER_HOME/usb_mount"
PHOTOS_DIR="$USER_HOME/instapi/app/static/photos"
QR_PLACEHOLDER="$USER_HOME/instapi/pi-setup/qr-placeholder.jpg"

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
