#!/bin/bash
# USB Gadget Mass Storage Setup for Pi Zero 2 W
# Makes the Pi appear as a USB drive to the photo frame

set -e

echo "🔌 Setting up USB Gadget Mode..."

# Enable dwc2 overlay for USB gadget
if ! grep -q "dtoverlay=dwc2" /boot/config.txt; then
    echo "Enabling dwc2 overlay..."
    echo "dtoverlay=dwc2" | sudo tee -a /boot/config.txt
fi

# Load dwc2 and g_mass_storage modules on boot
if ! grep -q "dwc2" /etc/modules; then
    echo "Adding dwc2 to modules..."
    echo "dwc2" | sudo tee -a /etc/modules
fi

# Create the USB storage directory (where photos go)
PHOTOS_DIR="/home/pi/instapi/photos"
mkdir -p "$PHOTOS_DIR"

# Create a disk image file (256MB - enough for photos)
IMG_FILE="/home/pi/usb_drive.img"
MOUNT_POINT="/home/pi/usb_mount"

if [ ! -f "$IMG_FILE" ]; then
    echo "Creating 256MB disk image..."
    dd if=/dev/zero of="$IMG_FILE" bs=1M count=256
    
    # Format as FAT32 (compatible with photo frames)
    mkfs.vfat -F 32 "$IMG_FILE"
fi

# Create mount point
mkdir -p "$MOUNT_POINT"

# Create systemd service to mount and expose USB on boot
sudo tee /etc/systemd/system/usb-gadget.service > /dev/null << 'EOF'
[Unit]
Description=USB Mass Storage Gadget
After=network.target instapi.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/home/pi/instapi/pi-setup/start-usb-gadget.sh
ExecStop=/home/pi/instapi/pi-setup/stop-usb-gadget.sh

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable usb-gadget

echo "✅ USB Gadget configured!"
echo "After reboot, the Pi will appear as a USB drive to the photo frame."
