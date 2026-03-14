#!/usr/bin/env python3
"""Generate a 'WiFi Disconnected' image for USB photo frames.
Shown when the Pi loses WiFi so the user knows how to reconnect."""

from PIL import Image, ImageDraw, ImageFont
import os
import sys


def generate_wifi_fix_image(output_path=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if output_path is None:
        output_path = os.path.join(script_dir, "wifi-fix.jpg")

    width, height = 1920, 1080

    # Dark gradient background
    img = Image.new('RGB', (width, height), '#0d1117')
    draw = ImageDraw.Draw(img)

    # Try to use a system font, fall back to default
    def get_font(size):
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                return ImageFont.truetype(fp, size)
        return ImageFont.load_default()

    font_title = get_font(96)
    font_body = get_font(52)
    font_hotspot = get_font(110)
    font_note = get_font(38)

    y = 100

    # Title
    draw.text((width // 2, y), "WiFi Disconnected",
              fill="#f87171", font=font_title, anchor="mt")
    y += 150

    # Instructions
    draw.text((width // 2, y), "To fix your photo frame:",
              fill="#cccccc", font=font_body, anchor="mt")
    y += 90

    draw.text((width // 2, y), "1. On your phone, go to WiFi settings",
              fill="#ffffff", font=font_body, anchor="mt")
    y += 80

    draw.text((width // 2, y), "2. Connect to the network called:",
              fill="#ffffff", font=font_body, anchor="mt")
    y += 100

    # Hotspot name — big and green with background
    hotspot_text = "InstaPi-Setup"
    bbox = draw.textbbox((0, 0), hotspot_text, font=font_hotspot)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    rx = width // 2 - tw // 2 - 40
    ry = y - 15
    draw.rounded_rectangle(
        [rx, ry, rx + tw + 80, ry + th + 30],
        radius=20,
        fill="#1a472a",
        outline="#22c55e",
        width=3
    )
    draw.text((width // 2, y), hotspot_text,
              fill="#4ade80", font=font_hotspot, anchor="mt")
    y += th + 80

    draw.text((width // 2, y), "3. A setup page will open on your phone",
              fill="#ffffff", font=font_body, anchor="mt")
    y += 100

    draw.text((width // 2, y), "Your photos will return once WiFi is reconnected.",
              fill="#666666", font=font_note, anchor="mt")

    img.save(output_path, 'JPEG', quality=95)
    print(f"WiFi fix image generated: {output_path}")
    return output_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    generate_wifi_fix_image(out)
