#!/bin/bash
# Update photos on the USB drive — incremental staging with watermark cache.
# Staging dir persists across runs so only NEW photos get watermarked.
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
# Phase 1: Incremental staging (frame stays up during this)
# Staging persists across runs — already-watermarked photos are kept.
# Only new/changed photos are copied and watermarked.
# ============================================================
mkdir -p "$STAGING"

# Track which filenames should be in the final set (for cleanup)
DESIRED_DIR=$(mktemp -d)
NEW_FILES=""

# Copy only NEW photos to staging (skip those already watermarked)
for subdir in "" upload picker album sync sync/picker sync/upload; do
    dir="$PHOTOS_DIR"
    [ -n "$subdir" ] && dir="$PHOTOS_DIR/$subdir"
    [ -d "$dir" ] || continue
    for ext in jpg jpeg png; do
        for f in "$dir"/*."$ext"; do
            [ -f "$f" ] || continue
            fname=$(basename "$f")
            # Skip if already tracked (first copy wins — dedup across subdirs)
            [ -f "$DESIRED_DIR/$fname" ] && continue
            # Skip tiny files (<10KB) — likely broken and can freeze cheap frames
            fsize=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
            [ "$fsize" -lt 10240 ] && continue
            # Mark as desired
            touch "$DESIRED_DIR/$fname"
            # Skip if already in staging (already watermarked from previous run)
            [ -f "$STAGING/$fname" ] && continue
            # New photo — copy to staging for watermarking
            cp "$f" "$STAGING/$fname"
            NEW_FILES="$NEW_FILES $fname"
        done
    done
done

# Remove photos from staging that are no longer on disk
REMOVED=0
for f in "$STAGING"/*; do
    [ -f "$f" ] || continue
    fname=$(basename "$f")
    if [ ! -f "$DESIRED_DIR/$fname" ]; then
        rm "$f"
        REMOVED=$((REMOVED + 1))
    fi
done
[ "$REMOVED" -gt 0 ] && echo "Removed $REMOVED deleted photos from staging"

rm -rf "$DESIRED_DIR"

# Count photos in staging
PHOTO_COUNT=$(ls -1 "$STAGING"/*.jpg "$STAGING"/*.jpeg "$STAGING"/*.png 2>/dev/null | wc -l)

if [ "$PHOTO_COUNT" -eq 0 ]; then
    # Safety: refuse to wipe USB if photos exist on disk but staging is empty
    DISK_COUNT=$(find "$PHOTOS_DIR" -maxdepth 3 -type f \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" \) ! -path "*/thumbs/*" 2>/dev/null | wc -l)
    if [ "$DISK_COUNT" -gt 0 ]; then
        echo "ERROR: 0 photos staged but $DISK_COUNT on disk — aborting to protect frame"
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

# Watermark ONLY new photos (not all — staging already has watermarked copies)
NEW_COUNT=$(echo $NEW_FILES | wc -w)
if [ "$NEW_COUNT" -gt 0 ]; then
    echo "Watermarking $NEW_COUNT new photos..."
    usb_watermark "$STAGING" "$NEW_FILES"
else
    echo "No new photos to watermark"
fi

echo "Staging ready: $PHOTO_COUNT photos ($NEW_COUNT new)"

# ============================================================
# Phase 2: Quick swap (frame down briefly ~5s)
# ============================================================
usb_prepare_and_swap "$IMG_FILE" "$MOUNT_POINT" "$STAGING"

# Phase 3: Staging persists as watermark cache — do NOT delete it
echo "USB drive updated! Frame should refresh."
