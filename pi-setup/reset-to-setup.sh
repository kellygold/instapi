#!/bin/bash
# Reset USB image to show only QR placeholder
# This is called by admin panel to go back to setup screen on USB photo frames

set -e

# Use $HOME for paths
IMG_FILE="$HOME/usb_drive.img"
MOUNT_POINT="$HOME/usb_mount"
QR_PLACEHOLDER="$HOME/instapi/pi-setup/qr-placeholder.jpg"

echo "Resetting USB image to setup QR..."

# Stop USB gadget
sudo modprobe -r g_mass_storage 2>/dev/null || true

# Mount the image
mkdir -p "$MOUNT_POINT"
sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Clear all photos
sudo rm -f "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png 2>/dev/null || true

# Copy QR placeholder
if [ -f "$QR_PLACEHOLDER" ]; then
    sudo cp "$QR_PLACEHOLDER" "$MOUNT_POINT/001_scan_to_setup.jpg"
    echo "QR placeholder copied"
else
    echo "Warning: QR placeholder not found at $QR_PLACEHOLDER"
fi

# Sync and unmount
sync
sudo umount "$MOUNT_POINT"

# Restart USB gadget
sudo modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1

echo "USB reset complete - frame should show QR code"
