#!/bin/bash
# Reset USB image to show only QR placeholder
# This is called by admin panel to go back to setup screen on USB photo frames

# Set PATH since web app context has minimal PATH
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

set -e

# Derive paths from script location (systemd/sudo don't set $HOME reliably)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
USER_HOME="$(dirname "$INSTAPI_DIR")"

IMG_FILE="$USER_HOME/usb_drive.img"
MOUNT_POINT="$USER_HOME/usb_mount"
QR_PLACEHOLDER="$SCRIPT_DIR/qr-placeholder.jpg"

echo "USER_HOME=$USER_HOME"

echo "Resetting USB image to setup QR..."
echo "Script dir: $SCRIPT_DIR"
echo "QR placeholder: $QR_PLACEHOLDER"

# Stop USB gadget
/usr/bin/sudo /sbin/modprobe -r g_mass_storage 2>/dev/null || true

# Reformat the FAT32 image (clean filesystem avoids stale FAT entries that confuse frames)
/usr/bin/sudo /sbin/mkfs.fat -F 32 "$IMG_FILE" > /dev/null

# Mount the fresh image
/bin/mkdir -p "$MOUNT_POINT"
/usr/bin/sudo /bin/mount -o loop "$IMG_FILE" "$MOUNT_POINT"

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
/usr/bin/sudo /sbin/modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1 ro=0

echo "USB reset complete - frame should show QR code"
