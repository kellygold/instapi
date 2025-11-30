#!/usr/bin/env python3
"""Generate a branded QR placeholder image for USB photo frames."""

import qrcode
from PIL import Image, ImageDraw, ImageFont
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
    """Generate a branded QR code image."""
    
    if url is None:
        ip = get_ip()
        url = f"http://{ip}:3000"
    
    if output_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(script_dir, "qr-placeholder.jpg")
    
    # Create canvas (landscape for photo frames)
    width, height = 1920, 1080
    img = Image.new('RGB', (width, height), '#0a0a0a')
    draw = ImageDraw.Draw(img)
    
    # Generate QR code
    qr = qrcode.QRCode(box_size=12, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    
    # Size and position QR (left side, large)
    qr_size = 600
    qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
    qr_x = width // 4 - qr_size // 2
    qr_y = height // 2 - qr_size // 2
    
    # Add white background with padding for QR
    padding = 30
    qr_bg = Image.new('RGB', (qr_size + padding*2, qr_size + padding*2), 'white')
    img.paste(qr_bg, (qr_x - padding, qr_y - padding))
    img.paste(qr_img, (qr_x, qr_y))
    
    # Try to load fonts
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
        font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except:
        font_large = ImageFont.load_default()
        font_medium = font_large
        font_small = font_large
    
    # Right side text
    text_x = width * 3 // 5
    
    # Title
    draw.text((text_x, height // 3), "InstaPi", fill="#4ade80", font=font_large)
    
    # Subtitle
    draw.text((text_x, height // 3 + 90), "Your digital photo frame", fill="#666666", font=font_medium)
    
    # Instructions
    instructions = [
        "1. Scan QR code with your phone",
        "2. Sign in with Google", 
        "3. Pick your photos"
    ]
    
    y_offset = height // 2 + 20
    for i, instruction in enumerate(instructions):
        draw.text((text_x, y_offset + i * 50), instruction, fill="#cccccc", font=font_small)
    
    # URL at bottom
    draw.text((width // 2, height - 60), url, fill="#666666", font=font_small, anchor="mm")
    
    # Save
    img.save(output_path, 'JPEG', quality=95)
    print(f"QR placeholder generated: {output_path}")
    print(f"URL: {url}")
    return output_path

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    generate_qr_placeholder(url=url)
