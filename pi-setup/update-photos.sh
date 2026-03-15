#!/bin/bash
# Update photos on the USB drive — incremental add/remove, no reformat
# Called by Flask app after new photos are uploaded or synced

# Set PATH since web app context has minimal PATH
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Derive paths from script location (systemd/sudo don't set $HOME reliably)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
USER_HOME="$(dirname "$INSTAPI_DIR")"

IMG_FILE="$USER_HOME/usb_drive.img"
MOUNT_POINT="$USER_HOME/usb_mount"
PHOTOS_DIR="$INSTAPI_DIR/app/static/photos"
QR_PLACEHOLDER="$INSTAPI_DIR/pi-setup/qr-placeholder.jpg"

. "$SCRIPT_DIR/usb-gadget-helper.sh"

echo "Updating photos on USB drive..."

# Stop the USB gadget
usb_gadget_stop

# Create mount point if needed
mkdir -p "$MOUNT_POINT"

# Mount the existing image (no reformat — preserves existing photos)
sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Build list of photo filenames that SHOULD be on USB
EXPECTED=$(mktemp)
for subdir in "" upload picker album sync sync/picker sync/upload; do
    dir="$PHOTOS_DIR"
    [ -n "$subdir" ] && dir="$PHOTOS_DIR/$subdir"
    [ -d "$dir" ] || continue
    for ext in jpg jpeg png; do
        for f in "$dir"/*."$ext"; do
            [ -f "$f" ] && basename "$f" >> "$EXPECTED"
        done
    done
done

# Remove QR placeholder from expected (it's a fallback, not a photo)
sed -i '/^qr-placeholder\.jpg$/d' "$EXPECTED" 2>/dev/null

# Delete files from USB that are no longer in source
DELETED=0
for f in "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png; do
    [ -f "$f" ] || continue
    fname=$(basename "$f")
    if ! grep -qx "$fname" "$EXPECTED"; then
        sudo rm "$f"
        DELETED=$((DELETED + 1))
    fi
done

# Copy new or changed files (skip if same name + same size already on USB)
ADDED=0
for subdir in "" upload picker album sync sync/picker sync/upload; do
    dir="$PHOTOS_DIR"
    [ -n "$subdir" ] && dir="$PHOTOS_DIR/$subdir"
    [ -d "$dir" ] || continue
    for ext in jpg jpeg png; do
        for f in "$dir"/*."$ext"; do
            [ -f "$f" ] || continue
            fname=$(basename "$f")
            dest="$MOUNT_POINT/$fname"
            if [ ! -f "$dest" ]; then
                sudo cp "$f" "$dest"
                ADDED=$((ADDED + 1))
            elif [ "$(stat -c%s "$f" 2>/dev/null)" != "$(stat -c%s "$dest" 2>/dev/null)" ]; then
                sudo cp "$f" "$dest"
                ADDED=$((ADDED + 1))
            fi
        done
    done
done

# Count final photos on USB
PHOTO_COUNT=$(ls -1 "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png 2>/dev/null | wc -l)

if [ "$PHOTO_COUNT" -eq 0 ]; then
    # No photos, show QR placeholder
    if [ -f "$QR_PLACEHOLDER" ]; then
        sudo cp "$QR_PLACEHOLDER" "$MOUNT_POINT"/
    fi
    echo "No photos yet, showing QR code"
else
    # Remove QR placeholder if real photos exist
    sudo rm -f "$MOUNT_POINT/qr-placeholder.jpg" 2>/dev/null
fi

echo "Added $ADDED, removed $DELETED, total $PHOTO_COUNT photos"

# Watermark USB copies with this Pi's unique QR code
# (each Pi has its own URL — child points to master with child token)
if [ "$PHOTO_COUNT" -gt 0 ]; then
    echo "Watermarking USB photos..."
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

# Sync and unmount
sync
sudo umount "$MOUNT_POINT"

# Restart USB gadget
usb_gadget_start "$IMG_FILE"

echo "✅ USB drive updated! Frame should refresh."
