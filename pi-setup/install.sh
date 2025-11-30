#!/bin/bash
# InstaPi Installation Script for Raspberry Pi
# Optimized for Pi Zero 2 W with Raspberry Pi OS Lite

set -e

echo "🖼️  InstaPi Installer (Pi Zero 2 W Optimized)"
echo "=============================================="

# Update system
echo "Updating system..."
sudo apt update && sudo apt upgrade -y

# Install minimal X11 + Chromium (no full desktop)
echo "Installing minimal display server + browser..."
sudo apt install -y --no-install-recommends \
    xserver-xorg \
    x11-xserver-utils \
    xinit \
    chromium-browser \
    unclutter \
    python3 \
    python3-pip \
    python3-venv \
    git

# Reduce GPU memory for headless-ish operation (optional, more RAM for app)
if ! grep -q "gpu_mem=" /boot/config.txt; then
    echo "Setting GPU memory to 64MB..."
    echo "gpu_mem=64" | sudo tee -a /boot/config.txt
fi

# Disable screen blanking
if ! grep -q "consoleblank=0" /boot/cmdline.txt; then
    echo "Disabling screen blanking..."
    sudo sed -i 's/$/ consoleblank=0/' /boot/cmdline.txt
fi

# Clone or update repo
INSTALL_DIR="$HOME/instapi"
if [ -d "$INSTALL_DIR" ]; then
    echo "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull
else
    echo "Cloning InstaPi..."
    git clone https://github.com/kellygold/instapi.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Setup Python environment
echo "Setting up Python environment..."
cd "$INSTALL_DIR/app"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install systemd services
echo "Installing systemd services..."
sudo cp "$INSTALL_DIR/pi-setup/instapi.service" /etc/systemd/system/
sudo cp "$INSTALL_DIR/pi-setup/instapi-kiosk.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable instapi
sudo systemctl enable instapi-kiosk

echo ""
echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "1. Copy your secrets.json to $INSTALL_DIR/app/secrets.json"
echo "2. Reboot: sudo reboot"
echo ""
echo "After reboot, InstaPi will auto-start in kiosk mode!"
