#!/bin/bash
# InstaPi Installation Script for Raspberry Pi

set -e

echo "🖼️  InstaPi Installer"
echo "===================="

# Update system
echo "Updating system..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
echo "Installing dependencies..."
sudo apt install -y python3 python3-pip python3-venv chromium-browser unclutter

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

# Install systemd service
echo "Installing systemd service..."
sudo cp "$INSTALL_DIR/pi-setup/instapi.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable instapi

echo ""
echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "1. Copy your secrets.json to $INSTALL_DIR/app/secrets.json"
echo "2. Run: sudo systemctl start instapi"
echo "3. Open Chromium to http://localhost:3000"
echo ""
echo "For kiosk mode, run: $INSTALL_DIR/pi-setup/kiosk.sh"
