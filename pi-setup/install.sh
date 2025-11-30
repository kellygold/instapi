#!/bin/bash
# InstaPi Installation Script for Raspberry Pi
# Supports both USB Gadget mode and HDMI Kiosk mode

set -e

echo ""
echo "🖼️  InstaPi Installer"
echo "====================="
echo ""
echo "How is your Pi connected to the display?"
echo ""
echo "  1) USB  - Pi plugs into a photo frame's USB port"
echo "           (Frame reads photos like a USB stick)"
echo ""
echo "  2) HDMI - Pi drives a screen directly"
echo "           (Works with any monitor, TV, or Pi screen)"
echo ""
read -p "Choose [1/2]: " MODE_CHOICE

case $MODE_CHOICE in
    1) DISPLAY_MODE="usb" ;;
    2) DISPLAY_MODE="hdmi" ;;
    *) echo "Invalid choice. Defaulting to HDMI mode."; DISPLAY_MODE="hdmi" ;;
esac

echo ""
echo "Selected: $DISPLAY_MODE mode"
echo ""

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

# Install mode-specific dependencies
if [ "$DISPLAY_MODE" = "usb" ]; then
    sudo apt install -y dosfstools
elif [ "$DISPLAY_MODE" = "hdmi" ]; then
    sudo apt install -y --no-install-recommends \
        xserver-xorg \
        x11-xserver-utils \
        xinit \
        chromium-browser \
        unclutter
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

# Setup Python environment
echo "🐍 Setting up Python environment..."
cd "$INSTALL_DIR/app"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Make scripts executable
chmod +x "$INSTALL_DIR/pi-setup/"*.sh

# Save the mode for later reference
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

    # Generate QR code image for USB drive
    echo "📱 Generating QR code image..."
    cd "$INSTALL_DIR/app"
    source venv/bin/activate
    python3 << PYEOF
import qrcode
from PIL import Image, ImageDraw, ImageFont
import socket
import os

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except:
        ip = '?.?.?.?'
    finally:
        s.close()
    return ip

ip = get_ip()
url = f"http://{ip}:3000"

qr = qrcode.QRCode(box_size=10, border=4)
qr.add_data(url)
qr.make(fit=True)
qr_img = qr.make_image(fill_color="black", back_color="white")

img = Image.new('RGB', (1024, 600), 'black')
qr_size = min(400, img.height - 100)
qr_img = qr_img.resize((qr_size, qr_size))

x = (img.width - qr_size) // 2
y = (img.height - qr_size) // 2 - 30
img.paste(qr_img, (x, y))

draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
except:
    font = ImageFont.load_default()
    small_font = font

draw.text((img.width//2, y + qr_size + 30), "Scan to set up InstaPi", fill="white", font=font, anchor="mm")
draw.text((img.width//2, y + qr_size + 70), url, fill="gray", font=small_font, anchor="mm")

save_path = os.path.expanduser("~/instapi/pi-setup/qr-placeholder.jpg")
img.save(save_path, 'JPEG', quality=95)
print(f"QR code generated for {url}")
PYEOF

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
