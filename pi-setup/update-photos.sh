#!/bin/bash
# Update photos on the USB drive
# Called by Flask app after new photos are downloaded

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

echo "DEBUG: USER_HOME=$USER_HOME"
echo "DEBUG: PHOTOS_DIR=$PHOTOS_DIR"
echo "DEBUG: PICKER exists: $([ -d "$PHOTOS_DIR/picker" ] && echo yes || echo no)"

echo "Updating photos on USB drive..."

# Stop the USB gadget (frame needs time to fully deregister the device)
sudo modprobe -r g_mass_storage 2>/dev/null || true
sleep 3

# Reformat the FAT32 image (clean filesystem avoids stale FAT entries that confuse frames)
sudo mkfs.fat -F 32 "$IMG_FILE" > /dev/null

# Create mount point if needed
mkdir -p "$MOUNT_POINT"

# Mount the fresh image
sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Copy new photos (including from picker subdirectory, excluding thumbs)
PHOTO_COUNT=0
if [ -d "$PHOTOS_DIR" ]; then
    # Copy from main photos dir
    sudo cp "$PHOTOS_DIR"/*.jpg "$MOUNT_POINT"/ 2>/dev/null && PHOTO_COUNT=$((PHOTO_COUNT + 1))
    sudo cp "$PHOTOS_DIR"/*.jpeg "$MOUNT_POINT"/ 2>/dev/null
    sudo cp "$PHOTOS_DIR"/*.png "$MOUNT_POINT"/ 2>/dev/null

    # Copy from subdirectories (picker, upload, album, sync and its subdirs)
    for subdir in picker upload album sync sync/picker sync/upload; do
        if [ -d "$PHOTOS_DIR/$subdir" ]; then
            sudo cp "$PHOTOS_DIR/$subdir"/*.jpg "$MOUNT_POINT"/ 2>/dev/null
            sudo cp "$PHOTOS_DIR/$subdir"/*.jpeg "$MOUNT_POINT"/ 2>/dev/null
            sudo cp "$PHOTOS_DIR/$subdir"/*.png "$MOUNT_POINT"/ 2>/dev/null
        fi
    done

    PHOTO_COUNT=$(ls -1 "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png 2>/dev/null | wc -l)
    echo "Copied $PHOTO_COUNT photos"
fi

if [ "$PHOTO_COUNT" -eq 0 ]; then
    # No photos yet, show QR placeholder
    if [ -f "$QR_PLACEHOLDER" ]; then
        sudo cp "$QR_PLACEHOLDER" "$MOUNT_POINT"/
    fi
    echo "No photos yet, showing QR code"
fi

# Sync and unmount
sync
sudo umount "$MOUNT_POINT"

# Restart USB gadget
sudo modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1 ro=0

echo "✅ USB drive updated! Frame should refresh."
