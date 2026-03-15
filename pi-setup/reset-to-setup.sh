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

. "$SCRIPT_DIR/usb-gadget-helper.sh"

echo "Resetting USB image to setup QR..."

# Stop USB gadget
usb_gadget_stop

# Reformat the FAT32 image (clean filesystem avoids stale FAT entries that confuse frames)
/usr/bin/sudo /sbin/mkfs.fat -F 32 "$IMG_FILE" > /dev/null

# Mount the fresh image
usb_mount "$IMG_FILE" "$MOUNT_POINT"

# Copy QR placeholder
if [ -f "$QR_PLACEHOLDER" ]; then
    /usr/bin/sudo /bin/cp "$QR_PLACEHOLDER" "$MOUNT_POINT/001_scan_to_setup.jpg"
    echo "QR placeholder copied"
else
    echo "Warning: QR placeholder not found at $QR_PLACEHOLDER"
fi

# Unmount
usb_unmount "$MOUNT_POINT"

# Restart USB gadget
usb_gadget_start "$IMG_FILE"

echo "USB reset complete - frame should show QR code"
