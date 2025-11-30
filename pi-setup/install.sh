#!/bin/bash
# InstaPi Installation Script for Raspberry Pi
# Supports both USB Gadget mode and HDMI Kiosk mode
#
# Usage: 
#   ./install.sh         # Install all, configure later
#   ./install.sh usb     # Install and configure USB mode
#   ./install.sh hdmi    # Install and configure HDMI mode

set -e

echo ""
echo "🖼️  InstaPi Installer"
echo "====================="
echo ""

# Check if mode passed as argument
DISPLAY_MODE=""
if [ "$1" = "usb" ] || [ "$1" = "hdmi" ]; then
    DISPLAY_MODE="$1"
    echo "Mode: $DISPLAY_MODE (from argument)"
elif [ -n "$1" ]; then
    echo "Unknown mode: $1"
    echo "Usage: ./install.sh [usb|hdmi]"
    exit 1
fi

# Update system
echo "📦 Updating system..."
sudo apt update && sudo apt upgrade -y

# Install base dependencies
echo "📦 Installing dependencies..."
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    git

# Install ALL dependencies (both modes) - small footprint, no prompts
echo "📦 Installing display dependencies..."
sudo apt install -y dosfstools
sudo apt install -y --no-install-recommends \
    xserver-xorg \
    x11-xserver-utils \
    xinit \
    chromium-browser \
    unclutter

# Clone or update repo
INSTALL_DIR="$HOME/instapi"
if [ -d "$INSTALL_DIR" ]; then
    echo "📥 Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull
else
    echo "📥 Cloning InstaPi..."
    git clone https://github.com/kellygold/instapi.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Setup Python environment
echo "🐍 Setting up Python environment..."
cd "$INSTALL_DIR/app"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Make scripts executable
chmod +x "$INSTALL_DIR/pi-setup/"*.sh

# Install sudoers rules for admin panel
echo "🔐 Setting up admin permissions..."
sudo cp "$INSTALL_DIR/pi-setup/instapi-sudoers" /etc/sudoers.d/instapi
sudo chmod 440 /etc/sudoers.d/instapi

# Generate QR placeholder image with this Pi's IP
echo "📱 Generating QR code placeholder..."
cd "$INSTALL_DIR/app"
source venv/bin/activate
python3 ../pi-setup/generate-qr-placeholder.py

# ==========================================
# MODE-SPECIFIC SETUP (only if mode specified)
# ==========================================
if [ -z "$DISPLAY_MODE" ]; then
    echo ""
    echo "✅ Base installation complete!"
    echo ""
    echo "To configure display mode, run one of:"
    echo "  cd ~/instapi/pi-setup && ./install.sh usb"
    echo "  cd ~/instapi/pi-setup && ./install.sh hdmi"
    echo ""
    exit 0
fi

# Save the mode
echo "$DISPLAY_MODE" > "$INSTALL_DIR/.display_mode"

# ==========================================
# USB GADGET MODE SETUP
# ==========================================
if [ "$DISPLAY_MODE" = "usb" ]; then
    echo "🔌 Configuring USB Gadget mode..."

    # Enable dwc2 overlay for USB gadget
    if ! grep -q "dtoverlay=dwc2" /boot/config.txt; then
        echo "dtoverlay=dwc2" | sudo tee -a /boot/config.txt
    fi

    # Load dwc2 module on boot
    if ! grep -q "dwc2" /etc/modules; then
        echo "dwc2" | sudo tee -a /etc/modules
    fi

    # Create USB disk image (256MB FAT32)
    IMG_FILE="$HOME/usb_drive.img"
    if [ ! -f "$IMG_FILE" ]; then
        echo "💾 Creating 256MB USB disk image..."
        dd if=/dev/zero of="$IMG_FILE" bs=1M count=256
        mkfs.vfat -F 32 "$IMG_FILE"
    fi

    # Create mount point
    mkdir -p "$HOME/usb_mount"

    # Install systemd services for USB mode
    echo "⚙️  Installing services..."
    sudo cp "$INSTALL_DIR/pi-setup/instapi.service" /etc/systemd/system/
    sudo cp "$INSTALL_DIR/pi-setup/usb-gadget.service" /etc/systemd/system/

    # Update service paths
    sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/instapi.service
    sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/usb-gadget.service
    sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/instapi.service

    sudo systemctl daemon-reload
    sudo systemctl enable instapi
    sudo systemctl enable usb-gadget

    # QR placeholder already generated in base install

    NEXT_STEPS="
│ 3. Plug Pi into photo frame's USB port │
│                                        │
│ 4. Frame will show QR code - scan it!  │"

# ==========================================
# HDMI KIOSK MODE SETUP
# ==========================================
elif [ "$DISPLAY_MODE" = "hdmi" ]; then
    echo "🖥️  Configuring HDMI Kiosk mode..."

    # Reduce GPU memory (more RAM for browser)
    if ! grep -q "gpu_mem=" /boot/config.txt; then
        echo "gpu_mem=128" | sudo tee -a /boot/config.txt
    fi

    # Disable screen blanking
    if ! grep -q "consoleblank=0" /boot/cmdline.txt; then
        sudo sed -i 's/$/ consoleblank=0/' /boot/cmdline.txt
    fi

    # Install systemd services for HDMI mode
    echo "⚙️  Installing services..."
    sudo cp "$INSTALL_DIR/pi-setup/instapi.service" /etc/systemd/system/
    sudo cp "$INSTALL_DIR/pi-setup/instapi-kiosk.service" /etc/systemd/system/

    # Update service paths
    sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/instapi.service
    sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/instapi-kiosk.service
    sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/instapi.service
    sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/instapi-kiosk.service

    sudo systemctl daemon-reload
    sudo systemctl enable instapi
    sudo systemctl enable instapi-kiosk

    NEXT_STEPS="
│ 3. Connect HDMI screen to Pi           │
│                                        │
│ 4. Screen will show QR code - scan it! │"
fi

echo ""
echo "✅ Installation complete! (Mode: $DISPLAY_MODE)"
echo ""
echo "┌────────────────────────────────────────┐"
echo "│           NEXT STEPS                   │"
echo "├────────────────────────────────────────┤"
echo "│ 1. Copy secrets.json to:               │"
echo "│    $INSTALL_DIR/app/secrets.json       │"
echo "│                                        │"
echo "│ 2. Reboot: sudo reboot                 │"
echo "$NEXT_STEPS"
echo "└────────────────────────────────────────┘"
echo ""
