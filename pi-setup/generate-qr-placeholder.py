#!/usr/bin/env python3
"""Generate QR + logo placeholder on branded background for USB photo frames."""

import qrcode
from PIL import Image
import socket
import os
import sys

def get_ip():
    """Get local IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except:
        ip = '?.?.?.?'
    finally:
        s.close()
    return ip

def generate_qr_placeholder(output_path=None, url=None):
    """Generate QR + logo on branded background."""
    
    if url is None:
        ip = get_ip()
        url = f"http://{ip}:3000"
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if output_path is None:
        output_path = os.path.join(script_dir, "qr-placeholder.jpg")
    
    width, height = 1920, 1080
    
    # Try to use pre-made background, otherwise create solid
    bg_path = os.path.join(script_dir, "background.png")
    if not os.path.exists(bg_path):
        bg_path = os.path.join(script_dir, "background.jpg")
    if os.path.exists(bg_path):
        img = Image.open(bg_path).convert('RGB').resize((width, height), Image.Resampling.LANCZOS)
    else:
        img = Image.new('RGB', (width, height), '#0d1117')
    
    # Generate QR code
    qr = qrcode.QRCode(box_size=12, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    
    # Size QR - not too big
    qr_size = 500
    qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
    
    # Position QR (left third, centered vertically)
    qr_x = width // 4 - qr_size // 2
    qr_y = (height - qr_size) // 2
    img.paste(qr_img, (qr_x, qr_y))
    
    # Try to load and paste logo (right side - further right)
    logo_path = os.path.join(script_dir, "..", "app", "static", "instapi_logo_full.jpg")
    if os.path.exists(logo_path):
        logo = Image.open(logo_path)
        # Scale logo to ~350px
        logo_size = 350
        logo.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
        logo_x = width * 3 // 4 - logo.width // 2  # 75% across
        logo_y = (height - logo.height) // 2
        img.paste(logo, (logo_x, logo_y))
    
    # Save
    img.save(output_path, 'JPEG', quality=95)
    print(f"QR placeholder generated: {output_path}")
    print(f"URL: {url}")
    return output_path

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    generate_qr_placeholder(url=url)
