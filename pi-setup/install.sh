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
    git \
    curl

# Install core dependencies (both modes)
# NetworkManager is already on Trixie — no hostapd/dnsmasq needed
echo "📦 Installing core dependencies..."
sudo apt install -y dosfstools

# Install HDMI/kiosk dependencies only if needed
if [ "$DISPLAY_MODE" = "hdmi" ] || [ -z "$DISPLAY_MODE" ]; then
    echo "📦 Installing display dependencies (HDMI mode)..."
    sudo apt install -y --no-install-recommends \
        xserver-xorg \
        x11-xserver-utils \
        xinit \
        chromium \
        unclutter
else
    echo "⏩ Skipping display packages (USB mode — no screen needed)"
fi

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

# ==========================================
# CONFIGURATION: Read creds from config file or prompt
# ==========================================
echo ""
echo "🔐 Configuring credentials..."

# Check home dir first (SCP'd before install), then repo dir
if [ -f "$HOME/instapi-setup.conf" ]; then
    echo "Found ~/instapi-setup.conf — reading pre-seeded config..."
    source "$HOME/instapi-setup.conf"
    # Copy into repo dir for future reference
    cp "$HOME/instapi-setup.conf" "$INSTALL_DIR/instapi-setup.conf" 2>/dev/null || true
elif [ -f "$INSTALL_DIR/instapi-setup.conf" ]; then
    echo "Found instapi-setup.conf — reading shared credentials..."
    source "$INSTALL_DIR/instapi-setup.conf"
fi

if [ "$SYNC_ROLE" = "child" ]; then
    echo "Child frame mode — skipping Google OAuth (not needed)"
    CLIENT_ID="unused"
    CLIENT_SECRET="unused"
    # ngrok domain comes from config file, no prompt needed
elif [ -n "$CLIENT_ID" ]; then
    # Config file had OAuth creds but no NGROK_DOMAIN — prompt for it
    if [ -z "$NGROK_DOMAIN" ]; then
        read -p "Enter ngrok domain for this frame (e.g. mom-instapi.ngrok.dev): " NGROK_DOMAIN
    fi
else
    echo "No instapi-setup.conf found. Setting up from scratch."
    echo "(See README for Google Cloud setup instructions)"
    echo ""
    read -p "Google OAuth Client ID: " CLIENT_ID
    read -p "Google OAuth Client Secret: " CLIENT_SECRET
    read -p "ngrok authtoken (press enter to skip ngrok): " NGROK_TOKEN
    if [ -n "$NGROK_TOKEN" ]; then
        read -p "ngrok domain (e.g. my-frame.ngrok.dev): " NGROK_DOMAIN
    fi
fi

# ==========================================
# GENERATE SECRETS.JSON
# ==========================================
echo "🔑 Generating secrets.json..."
FLASK_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(24))")

if [ -n "$NGROK_DOMAIN" ]; then
    REDIRECT_URI="https://$NGROK_DOMAIN/oauth2callback"
else
    REDIRECT_URI="http://localhost:3000/oauth2callback"
fi

cat > "$INSTALL_DIR/app/secrets.json" << EOFJSON
{
  "flask_secret": "$FLASK_SECRET",
  "web": {
    "client_id": "$CLIENT_ID",
    "project_id": "instapi",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_secret": "$CLIENT_SECRET",
    "redirect_uris": [
      "$REDIRECT_URI"
    ]
  }
}
EOFJSON
echo "secrets.json created with redirect URI: $REDIRECT_URI"

# ==========================================
# PYTHON ENVIRONMENT
# ==========================================
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

# ==========================================
# NGROK SETUP (if configured)
# ==========================================
if [ -n "$NGROK_TOKEN" ] && [ -n "$NGROK_DOMAIN" ]; then
    echo "🌐 Setting up ngrok..."

    # Install ngrok if not present
    if ! command -v ngrok &> /dev/null; then
        echo "Installing ngrok..."
        curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | \
            sudo tee /etc/apt/trusted.gpg.d/ngrok.asc > /dev/null
        echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | \
            sudo tee /etc/apt/sources.list.d/ngrok.list > /dev/null
        sudo apt update && sudo apt install -y ngrok
    fi

    # Configure authtoken
    ngrok config add-authtoken "$NGROK_TOKEN"

    # Create systemd service
    cat > /tmp/ngrok.service << EOFSERVICE
[Unit]
Description=ngrok tunnel for InstaPi
After=network-online.target instapi.service
Wants=network-online.target

[Service]
Type=simple
User=$USER
ExecStart=/usr/local/bin/ngrok http 3000 --domain $NGROK_DOMAIN
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOFSERVICE
    sudo mv /tmp/ngrok.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable ngrok
    echo "ngrok configured: $NGROK_DOMAIN"
else
    echo "⏩ Skipping ngrok (local-only mode)"
fi

# ==========================================
# WATCHDOG CRON
# ==========================================
echo "🛡️  Installing watchdog..."
(crontab -l 2>/dev/null | grep -v watchdog; echo "*/5 * * * * $INSTALL_DIR/pi-setup/watchdog.sh") | crontab -

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

    # Enable dwc2 overlay for USB gadget (Trixie uses /boot/firmware/, older uses /boot/)
    BOOT_CONFIG="/boot/firmware/config.txt"
    [ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"

    # Remove any existing dwc2 line (may have dr_mode=host which breaks gadget)
    sudo sed -i '/dtoverlay=dwc2/d' "$BOOT_CONFIG"
    echo "dtoverlay=dwc2" | sudo tee -a "$BOOT_CONFIG"

    # Load dwc2 module on boot (Trixie uses modules-load.d, older uses /etc/modules)
    echo "dwc2" | sudo tee /etc/modules-load.d/dwc2.conf
    if ! grep -q "dwc2" /etc/modules 2>/dev/null; then
        echo "dwc2" | sudo tee -a /etc/modules
    fi

    # Also add to kernel cmdline for reliable early loading
    CMDLINE_FILE="$BOOT_CONFIG"
    [ -f "/boot/firmware/cmdline.txt" ] && CMDLINE_FILE="/boot/firmware/cmdline.txt"
    [ -f "/boot/cmdline.txt" ] && [ ! -f "/boot/firmware/cmdline.txt" ] && CMDLINE_FILE="/boot/cmdline.txt"
    if ! grep -q "modules-load=dwc2" "$CMDLINE_FILE" 2>/dev/null; then
        sudo sed -i 's/$/ modules-load=dwc2/' "$CMDLINE_FILE"
    fi

    # Create USB disk image (256MB FAT32)
    IMG_FILE="$HOME/usb_drive.img"
    if [ ! -f "$IMG_FILE" ]; then
        echo "💾 Creating 256MB USB disk image..."
        dd if=/dev/zero of="$IMG_FILE" bs=1M count=256
        /usr/sbin/mkfs.fat -F 32 "$IMG_FILE"
    fi

    # Create mount point
    mkdir -p "$HOME/usb_mount"

    # Install systemd services for USB mode
    echo "⚙️  Installing services..."
    sudo cp "$INSTALL_DIR/pi-setup/instapi.service" /etc/systemd/system/
    sudo cp "$INSTALL_DIR/pi-setup/usb-gadget.service" /etc/systemd/system/
    sudo cp "$INSTALL_DIR/pi-setup/instapi-wifi.service" /etc/systemd/system/

    # Update service paths
    sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/instapi.service
    sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/usb-gadget.service
    sudo sed -i "s|/home/instapi|$HOME|g" /etc/systemd/system/instapi-wifi.service
    sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/instapi.service


    sudo systemctl daemon-reload
    sudo systemctl enable instapi
    sudo systemctl enable usb-gadget
    sudo systemctl enable instapi-wifi

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
    sudo cp "$INSTALL_DIR/pi-setup/instapi-wifi.service" /etc/systemd/system/

    # Update service paths
    sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/instapi.service
    sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/instapi-kiosk.service
    sudo sed -i "s|/home/instapi|$HOME|g" /etc/systemd/system/instapi-wifi.service
    sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/instapi.service
    sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/instapi-kiosk.service


    sudo systemctl daemon-reload
    sudo systemctl enable instapi
    sudo systemctl enable instapi-kiosk
    sudo systemctl enable instapi-wifi

    NEXT_STEPS="
│ 3. Connect HDMI screen to Pi           │
│                                        │
│ 4. Screen will show QR code - scan it! │"
fi

# ==========================================
# PRE-SEED SYNC CONFIG (for child frames)
# ==========================================
if [ "$SYNC_ROLE" = "child" ] && [ -n "$SYNC_TOKEN" ] && [ -n "$MASTER_URL" ]; then
    echo "🔗 Configuring as child frame syncing from $MASTER_URL..."
    cat > "$INSTALL_DIR/app/device_state.json" << EOFSTATE
{
  "sync_role": "child",
  "master_url": "$MASTER_URL",
  "sync_token": "$SYNC_TOKEN",
  "sync_interval": 1800
}
EOFSTATE
    echo "device_state.json pre-seeded with child sync config"
fi

echo ""
echo "✅ Installation complete! (Mode: $DISPLAY_MODE)"
echo ""
echo "┌────────────────────────────────────────┐"
echo "│           READY TO GO!                 │"
echo "├────────────────────────────────────────┤"
echo "│ 1. Reboot: sudo reboot                │"
echo "│                                        │"
echo "│ 2. Wait for Pi to start up            │"
echo "$NEXT_STEPS"
echo "│                                        │"
if [ -n "$NGROK_DOMAIN" ]; then
echo "│ Remote: https://$NGROK_DOMAIN │"
fi
echo "└────────────────────────────────────────┘"
echo ""
