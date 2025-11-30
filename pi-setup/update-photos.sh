#!/bin/bash
# Update photos on the USB drive
# Called by Flask app after new photos are downloaded

# Get the actual user's home directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_HOME="$(dirname "$(dirname "$SCRIPT_DIR")")"

IMG_FILE="$USER_HOME/usb_drive.img"
MOUNT_POINT="$USER_HOME/usb_mount"
PHOTOS_DIR="$USER_HOME/instapi/app/static/photos"
QR_PLACEHOLDER="$USER_HOME/instapi/pi-setup/qr-placeholder.jpg"

echo "Updating photos on USB drive..."

# Stop the USB gadget (frame will briefly disconnect)
sudo modprobe -r g_mass_storage 2>/dev/null || true

# Create mount point if needed
mkdir -p "$MOUNT_POINT"

# Mount the disk image
sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Clear old photos
rm -f "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png 2>/dev/null || true

# Copy new photos
if [ -d "$PHOTOS_DIR" ] && [ -n "$(ls -A $PHOTOS_DIR 2>/dev/null)" ]; then
    cp "$PHOTOS_DIR"/*.jpg "$MOUNT_POINT"/ 2>/dev/null || true
    cp "$PHOTOS_DIR"/*.jpeg "$MOUNT_POINT"/ 2>/dev/null || true
    cp "$PHOTOS_DIR"/*.png "$MOUNT_POINT"/ 2>/dev/null || true
    echo "Copied $(ls -1 $MOUNT_POINT | wc -l) photos"
else
    # No photos yet, show QR placeholder
    if [ -f "$QR_PLACEHOLDER" ]; then
        cp "$QR_PLACEHOLDER" "$MOUNT_POINT"/
    fi
    echo "No photos yet, showing QR code"
fi

# Sync and unmount
sync
sudo umount "$MOUNT_POINT"

# Restart USB gadget
sudo modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1 ro=0

echo "✅ USB drive updated! Frame should refresh."
