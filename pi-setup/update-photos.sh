#!/bin/bash
# Update photos on the USB drive
# Called by Flask app after new photos are downloaded

# Use $HOME for reliable path resolution
USER_HOME="$HOME"
INSTAPI_DIR="$USER_HOME/instapi"

IMG_FILE="$USER_HOME/usb_drive.img"
MOUNT_POINT="$USER_HOME/usb_mount"
PHOTOS_DIR="$INSTAPI_DIR/app/static/photos"
QR_PLACEHOLDER="$INSTAPI_DIR/pi-setup/qr-placeholder.jpg"

echo "DEBUG: USER_HOME=$USER_HOME"
echo "DEBUG: PHOTOS_DIR=$PHOTOS_DIR"
echo "DEBUG: PICKER exists: $([ -d "$PHOTOS_DIR/picker" ] && echo yes || echo no)"

echo "Updating photos on USB drive..."

# Stop the USB gadget (frame will briefly disconnect)
sudo modprobe -r g_mass_storage 2>/dev/null || true

# Create mount point if needed
mkdir -p "$MOUNT_POINT"

# Mount the disk image
sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Clear old photos
rm -f "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png 2>/dev/null || true

# Copy new photos (including from picker subdirectory)
PHOTO_COUNT=0
if [ -d "$PHOTOS_DIR" ]; then
    # Copy from main photos dir
    cp "$PHOTOS_DIR"/*.jpg "$MOUNT_POINT"/ 2>/dev/null && PHOTO_COUNT=$((PHOTO_COUNT + 1))
    cp "$PHOTOS_DIR"/*.jpeg "$MOUNT_POINT"/ 2>/dev/null
    cp "$PHOTOS_DIR"/*.png "$MOUNT_POINT"/ 2>/dev/null
    
    # Copy from picker subdirectory
    if [ -d "$PHOTOS_DIR/picker" ]; then
        cp "$PHOTOS_DIR/picker"/*.jpg "$MOUNT_POINT"/ 2>/dev/null
        cp "$PHOTOS_DIR/picker"/*.jpeg "$MOUNT_POINT"/ 2>/dev/null
        cp "$PHOTOS_DIR/picker"/*.png "$MOUNT_POINT"/ 2>/dev/null
    fi
    
    PHOTO_COUNT=$(ls -1 "$MOUNT_POINT"/*.jpg "$MOUNT_POINT"/*.jpeg "$MOUNT_POINT"/*.png 2>/dev/null | wc -l)
    echo "Copied $PHOTO_COUNT photos"
fi

if [ "$PHOTO_COUNT" -eq 0 ]; then
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
