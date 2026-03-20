#!/bin/bash
# Update photos on the USB drive — prepare in staging, then quick swap.
# Frame stays up during preparation, only goes down briefly for the swap.
# Called by Flask app after new photos are uploaded or synced.

# Set PATH since web app context has minimal PATH
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Derive paths from script location (systemd/sudo don't set $HOME reliably)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
USER_HOME="$(dirname "$INSTAPI_DIR")"

IMG_FILE="$USER_HOME/usb_drive.img"
MOUNT_POINT="$USER_HOME/usb_mount"
PHOTOS_DIR="$INSTAPI_DIR/app/static/photos"
QR_PLACEHOLDER="$INSTAPI_DIR/pi-setup/qr-placeholder.jpg"
STAGING="$USER_HOME/usb_staging"

. "$SCRIPT_DIR/usb-gadget-helper.sh"

echo "Updating photos on USB drive..."

# ============================================================
# Phase 1: Prepare in staging (frame stays up during this)
# ============================================================
rm -rf "$STAGING"
mkdir -p "$STAGING"

# Copy all current photos to staging
for subdir in "" upload picker album sync sync/picker sync/upload; do
    dir="$PHOTOS_DIR"
    [ -n "$subdir" ] && dir="$PHOTOS_DIR/$subdir"
    [ -d "$dir" ] || continue
    for ext in jpg jpeg png; do
        for f in "$dir"/*."$ext"; do
            [ -f "$f" ] || continue
            fname=$(basename "$f")
            # Skip if already in staging (first copy wins)
            [ -f "$STAGING/$fname" ] && continue
            cp "$f" "$STAGING/$fname"
        done
    done
done

# Count photos in staging
PHOTO_COUNT=$(ls -1 "$STAGING"/*.jpg "$STAGING"/*.jpeg "$STAGING"/*.png 2>/dev/null | wc -l)

if [ "$PHOTO_COUNT" -eq 0 ]; then
    # Safety: refuse to wipe USB if photos exist on disk but staging is empty
    DISK_COUNT=$(find "$PHOTOS_DIR" -maxdepth 3 -type f \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" \) ! -path "*/thumbs/*" 2>/dev/null | wc -l)
    if [ "$DISK_COUNT" -gt 0 ]; then
        echo "ERROR: 0 photos staged but $DISK_COUNT on disk — aborting to protect frame"
        rm -rf "$STAGING"
        exit 1
    fi
    # Truly no photos anywhere — stage QR placeholder
    if [ -f "$QR_PLACEHOLDER" ]; then
        cp "$QR_PLACEHOLDER" "$STAGING/"
    fi
    echo "No photos, staging QR placeholder"
else
    # Remove QR placeholder from staging if real photos exist
    rm -f "$STAGING/qr-placeholder.jpg" 2>/dev/null
fi

# Watermark all photos in staging (they're fresh copies from source, unwatermarked)
# Frame stays up during this — watermarking happens on staging copies, not USB
echo "Watermarking photos in staging..."
usb_watermark "$STAGING" "all"

echo "Staging ready: $PHOTO_COUNT photos"

# ============================================================
# Phase 2: Quick swap (frame down briefly ~5s)
# ============================================================
usb_prepare_and_swap "$IMG_FILE" "$MOUNT_POINT" "$STAGING"

# ============================================================
# Phase 3: Cleanup
# ============================================================
rm -rf "$STAGING"

echo "USB drive updated! Frame should refresh."
