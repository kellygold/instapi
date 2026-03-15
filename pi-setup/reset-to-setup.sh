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
STAGING="$USER_HOME/usb_staging"

. "$SCRIPT_DIR/usb-gadget-helper.sh"

echo "Resetting USB image to setup QR..."

# Prepare staging with just the QR placeholder
rm -rf "$STAGING"
mkdir -p "$STAGING"
if [ -f "$QR_PLACEHOLDER" ]; then
    cp "$QR_PLACEHOLDER" "$STAGING/001_scan_to_setup.jpg"
    echo "QR placeholder staged"
else
    echo "Warning: QR placeholder not found at $QR_PLACEHOLDER"
fi

# Quick swap with reformat (clean filesystem)
usb_prepare_and_swap "$IMG_FILE" "$MOUNT_POINT" "$STAGING" true

# Cleanup
rm -rf "$STAGING"

echo "USB reset complete - frame should show QR code"
