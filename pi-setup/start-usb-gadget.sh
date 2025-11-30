#!/bin/bash
# Start USB Mass Storage Gadget
# Called by systemd on boot

IMG_FILE="/home/pi/usb_drive.img"
MOUNT_POINT="/home/pi/usb_mount"
PHOTOS_DIR="/home/pi/instapi/app/static/photos"

# Mount the disk image so we can write to it
sudo mount -o loop "$IMG_FILE" "$MOUNT_POINT"

# Sync photos from app to USB drive
if [ -d "$PHOTOS_DIR" ]; then
    cp -r "$PHOTOS_DIR"/* "$MOUNT_POINT"/ 2>/dev/null || true
fi

# If no photos yet, copy the QR code placeholder
if [ -z "$(ls -A $MOUNT_POINT 2>/dev/null)" ]; then
    cp /home/pi/instapi/pi-setup/qr-placeholder.jpg "$MOUNT_POINT"/
fi

# Sync and unmount (frame needs exclusive access)
sync
sudo umount "$MOUNT_POINT"

# Load the USB mass storage gadget
sudo modprobe g_mass_storage file="$IMG_FILE" stall=0 removable=1 ro=0

echo "USB Gadget started - Pi is now a USB drive"
