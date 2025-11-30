#!/bin/bash
# Reset USB image to show only QR placeholder
# This is called by admin panel to go back to setup screen on USB photo frames

set -e

# Get script directory to find paths dynamically
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Dynamic paths
IMG_FILE="$HOME/usb_drive.img"
MOUNT_POINT="$HOME/usb_mount"
QR_PLACEHOLDER="$SCRIPT_DIR/qr-placeholder.jpg"

echo "Resetting USB image to setup QR..."
echo "Script dir: $SCRIPT_DIR"
echo "QR placeholder: $QR_PLACEHOLDER"

# Stop USB gadget
/usr/bin/sudo /sbin/modprobe -r g_mass_storage 2>/dev/null || true

# Mount the image
/bin/mkdir -p "$MOUNT_POINT"
/usr/bin/sudo /bin/mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Clear all photos
/usr/bin/sudo /bin/rm -f "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png 2>/dev/null || true

# Copy QR placeholder
if [ -f "$QR_PLACEHOLDER" ]; then
    /usr/bin/sudo /bin/cp "$QR_PLACEHOLDER" "$MOUNT_POINT/001_scan_to_setup.jpg"
    echo "QR placeholder copied"
else
    echo "Warning: QR placeholder not found at $QR_PLACEHOLDER"
fi

# Sync and unmount
/bin/sync
/usr/bin/sudo /bin/umount "$MOUNT_POINT"

# Restart USB gadget
/usr/bin/sudo /sbin/modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1

echo "USB reset complete - frame should show QR code"
