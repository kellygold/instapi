#!/usr/bin/env python3
"""Generate QR placeholder by overlaying QR on background screenshot."""

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
    """Overlay QR on background screenshot."""
    
    if url is None:
        ip = get_ip()
        url = f"http://{ip}:3000"
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if output_path is None:
        output_path = os.path.join(script_dir, "qr-placeholder.jpg")
    
    width, height = 1920, 1080
    
    # Load background (screenshot of web UI without QR)
    bg_path = os.path.join(script_dir, "background.png")
    if not os.path.exists(bg_path):
        bg_path = os.path.join(script_dir, "background.jpg")
    if os.path.exists(bg_path):
        img = Image.open(bg_path).convert('RGB').resize((width, height), Image.Resampling.LANCZOS)
    else:
        img = Image.new('RGB', (width, height), '#0d1117')
    
    # Generate QR code
    qr = qrcode.QRCode(box_size=14, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    
    # QR size - 50% larger than original
    qr_size = 630
    qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
    
    # Position QR in center of left half (where qr-wrapper is on web)
    qr_x = width // 4 - qr_size // 2
    qr_y = (height - qr_size) // 2
    img.paste(qr_img, (qr_x, qr_y))
    
    # Save
    img.save(output_path, 'JPEG', quality=95)
    print(f"QR placeholder generated: {output_path}")
    print(f"URL: {url}")
    return output_path

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    generate_qr_placeholder(url=url)
