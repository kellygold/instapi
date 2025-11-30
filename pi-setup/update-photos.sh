#!/bin/bash
# Update photos on the USB drive
# Called by Flask app after new photos are downloaded

IMG_FILE="/home/pi/usb_drive.img"
MOUNT_POINT="/home/pi/usb_mount"
PHOTOS_DIR="/home/pi/instapi/app/static/photos"

echo "Updating photos on USB drive..."

# Stop the USB gadget (frame will briefly disconnect)
sudo modprobe -r g_mass_storage 2>/dev/null || true

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
    cp /home/pi/instapi/pi-setup/qr-placeholder.jpg "$MOUNT_POINT"/
    echo "No photos yet, showing QR code"
fi

# Sync and unmount
sync
sudo umount "$MOUNT_POINT"

# Restart USB gadget
sudo modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1 ro=0

echo "✅ USB drive updated! Frame should refresh."
