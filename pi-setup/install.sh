#!/bin/bash
# InstaPi Installation Script for Raspberry Pi Zero 2 W
# USB Gadget mode - Pi appears as USB drive to photo frame

set -e

echo "🖼️  InstaPi Installer (USB Gadget Mode)"
echo "========================================"
echo ""
echo "This will configure your Pi Zero 2 W to:"
echo "  - Appear as a USB drive to your photo frame"
echo "  - Run a web server for phone setup"
echo "  - Download and serve photos from Google Photos"
echo ""

# Update system
echo "📦 Updating system..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
echo "📦 Installing dependencies..."
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    dosfstools

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

# Setup USB Gadget mode
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

# Install systemd services
echo "⚙️  Installing services..."
sudo cp "$INSTALL_DIR/pi-setup/instapi.service" /etc/systemd/system/
sudo cp "$INSTALL_DIR/pi-setup/usb-gadget.service" /etc/systemd/system/

# Update service paths
sudo sed -i "s|/home/pi|$HOME|g" /etc/systemd/system/instapi.service
sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/instapi.service

sudo systemctl daemon-reload
sudo systemctl enable instapi
sudo systemctl enable usb-gadget

# Generate initial QR code placeholder
echo "📱 Generating QR code placeholder..."
cd "$INSTALL_DIR/app"
source venv/bin/activate
python3 << 'PYEOF'
import qrcode
from PIL import Image, ImageDraw, ImageFont
import socket

# Get Pi's IP address
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

# Create QR code
qr = qrcode.QRCode(box_size=10, border=4)
qr.add_data(url)
qr.make(fit=True)
qr_img = qr.make_image(fill_color="black", back_color="white")

# Create final image (1024x600 for typical frames)
img = Image.new('RGB', (1024, 600), 'black')
qr_size = min(400, img.height - 100)
qr_img = qr_img.resize((qr_size, qr_size))

# Center QR code
x = (img.width - qr_size) // 2
y = (img.height - qr_size) // 2 - 30
img.paste(qr_img, (x, y))

# Add text
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
except:
    font = ImageFont.load_default()
    small_font = font

draw.text((img.width//2, y + qr_size + 30), "Scan to set up InstaPi", fill="white", font=font, anchor="mm")
draw.text((img.width//2, y + qr_size + 70), url, fill="gray", font=small_font, anchor="mm")

img.save('/home/pi/instapi/pi-setup/qr-placeholder.jpg', 'JPEG', quality=95)
print(f"QR code generated for {url}")
PYEOF

echo ""
echo "✅ Installation complete!"
echo ""
echo "┌────────────────────────────────────────┐"
echo "│           NEXT STEPS                   │"
echo "├────────────────────────────────────────┤"
echo "│ 1. Copy secrets.json to:               │"
echo "│    $INSTALL_DIR/app/secrets.json       │"
echo "│                                        │"
echo "│ 2. Reboot: sudo reboot                 │"
echo "│                                        │"
echo "│ 3. Plug Pi into photo frame's USB port │"
echo "│                                        │"
echo "│ 4. Frame will show QR code - scan it!  │"
echo "└────────────────────────────────────────┘"
echo ""
