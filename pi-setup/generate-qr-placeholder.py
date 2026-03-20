#!/usr/bin/env python3
"""Generate QR placeholder by overlaying QR on background screenshot."""

import qrcode
from PIL import Image
import os
import sys

def _get_upload_url():
    """Get the upload URL from the app (same URL used for watermarks)."""
    try:
        from utils import get_upload_url
        return get_upload_url()
    except Exception:
        pass
    return None

def generate_qr_placeholder(output_path=None, url=None):
    """Overlay QR on background screenshot."""

    if url is None:
        # Placeholder QR points to this frame's admin page (not upload)
        try:
            import db
            db.init_db()
            # Use ngrok URL if available, otherwise local
            ngrok_domain = None
            # Check if ngrok service has a domain configured
            import subprocess
            result = subprocess.run(['grep', 'domain', '/etc/systemd/system/ngrok.service'],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                for part in result.stdout.split():
                    if '.ngrok.dev' in part:
                        ngrok_domain = part
                        break
            if ngrok_domain:
                url = f"https://{ngrok_domain}/admin"
            else:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    s.connect(('8.8.8.8', 80))
                    ip = s.getsockname()[0]
                except Exception:
                    ip = 'instapi.local'
                finally:
                    s.close()
                url = f"http://{ip}:3000/admin"
        except Exception:
            url = "http://instapi.local:3000/admin"
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if output_path is None:
        output_path = os.path.join(script_dir, "qr-placeholder.jpg")
    
    width, height = 1920, 1080

    # Dark background
    img = Image.new('RGB', (width, height), '#0d1117')

    # Generate QR code
    qr = qrcode.QRCode(box_size=14, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="white", back_color="#0d1117")

    # QR size
    qr_size = 600
    qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)

    # Add text
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img)

    # Try to load a nice font, fall back to default
    font_large = None
    font_small = None
    for font_path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"]:
        if os.path.exists(font_path):
            font_large = ImageFont.truetype(font_path, 64)
            font_small = ImageFont.truetype(font_path, 36)
            break
    if font_large is None:
        try:
            font_large = ImageFont.truetype("DejaVuSans-Bold", 64)
            font_small = ImageFont.truetype("DejaVuSans-Bold", 36)
        except Exception:
            font_large = ImageFont.load_default()
            font_small = font_large

    # Title text
    title = "Scan to Set Up Frame"
    subtitle = "InstaPi"
    bbox = draw.textbbox((0, 0), title, font=font_large)
    tw = bbox[2] - bbox[0]

    # Layout: QR on left, text on right
    qr_x = width // 4 - qr_size // 2
    qr_y = (height - qr_size) // 2
    img.paste(qr_img, (qr_x, qr_y))

    text_x = width // 2 + 60
    draw.text((text_x, height // 2 - 80), title, fill="white", font=font_large)
    draw.text((text_x, height // 2 + 10), subtitle, fill="#8b949e", font=font_small)

    # Save
    img.save(output_path, 'JPEG', quality=95)
    print(f"QR placeholder generated: {output_path}")
    print(f"URL: {url}")
    return output_path

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    generate_qr_placeholder(url=url)
