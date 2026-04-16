#!/bin/bash
# Update photos on the USB drive - incremental staging with watermark cache.
# Staging dir persists across runs so only NEW photos get watermarked.
# Frame stays up during preparation, only goes down briefly for the swap.
# Called by Flask app after new photos are uploaded or synced.

# Set PATH since web app context has minimal PATH
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Derive paths from script location (systemd/sudo don't set $HOME reliably)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
USER_HOME="$(dirname "$INSTAPI_DIR")"

IMG_FILE="${IMG_FILE:-$USER_HOME/usb_drive.img}"
MOUNT_POINT="${MOUNT_POINT:-$USER_HOME/usb_mount}"
PHOTOS_DIR="${PHOTOS_DIR:-$INSTAPI_DIR/app/static/photos}"
FRAME_EXPORT_DIR="${FRAME_EXPORT_DIR:-$INSTAPI_DIR/app/frame_export}"
FRAME_EXPORT_MANIFEST="${FRAME_EXPORT_MANIFEST:-$FRAME_EXPORT_DIR/manifest.json}"
INSTAPI_DB_PATH="${INSTAPI_DB_PATH:-$INSTAPI_DIR/app/instapi.db}"
QR_PLACEHOLDER="${QR_PLACEHOLDER:-$INSTAPI_DIR/pi-setup/qr-placeholder.jpg}"
STAGING="${STAGING:-$USER_HOME/usb_staging}"
STAGING_MANIFEST="${STAGING_MANIFEST:-$STAGING/.manifest.json}"
USB_HELPER_PATH="${USB_HELPER_PATH:-$SCRIPT_DIR/usb-gadget-helper.sh}"

. "$USB_HELPER_PATH"

echo "Updating photos on USB drive..."

# ============================================================
# Phase 1: Incremental staging (frame stays up during this)
# Staging persists across runs - already-watermarked photos are kept.
# Only new/changed photos are copied and watermarked.
# ============================================================
mkdir -p "$STAGING"

# Track which filenames should be in the final set (for cleanup)
DESIRED_DIR=$(mktemp -d)
NEW_FILES=""
USING_BALANCED_EXPORT=0
BALANCED_STAGING_ENTRIES=$(mktemp)
FRAME_BALANCE_ACTIVE=$(python3 - "$INSTAPI_DB_PATH" <<'PY'
import json
import sqlite3
import sys

db_path = sys.argv[1]
sync_role = None
frame_balance_enabled = False

try:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN (?, ?)",
        ("sync_role", "frame_balance_enabled"),
    ).fetchall()
    conn.close()
    values = {}
    for key, raw_value in rows:
        try:
            values[key] = json.loads(raw_value)
        except Exception:
            values[key] = raw_value
    sync_role = values.get("sync_role")
    frame_balance_enabled = bool(values.get("frame_balance_enabled", False))
except Exception:
    pass

sys.stdout.write("1" if sync_role == "child" and frame_balance_enabled else "0")
PY
)

if [ "$FRAME_BALANCE_ACTIVE" = "1" ] && [ -f "$FRAME_EXPORT_MANIFEST" ]; then
    echo "Using balanced frame export from $FRAME_EXPORT_DIR"
    USING_BALANCED_EXPORT=1
    BALANCED_ACTIONS=$(mktemp)
    python3 - "$FRAME_EXPORT_MANIFEST" "$STAGING_MANIFEST" > "$BALANCED_ACTIONS" <<'PY'
import json
import sys

frame_manifest_path, staging_manifest_path = sys.argv[1:3]
with open(frame_manifest_path, encoding="utf-8") as fh:
    frame_manifest = json.load(fh)

staging_entries = {}
try:
    with open(staging_manifest_path, encoding="utf-8") as fh:
        staging_manifest = json.load(fh)
    entries = staging_manifest.get("entries", {})
    if isinstance(entries, dict):
        staging_entries = entries
except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
    pass

for entry in frame_manifest.get("entries", []):
    export_name = entry.get("export_name")
    source_signature = entry.get("source_signature", "")
    if not export_name:
        continue
    action = "reuse" if staging_entries.get(export_name) == source_signature else "copy"
    print(f"{export_name}\t{source_signature}\t{action}")
PY

    while IFS=$'\t' read -r fname source_signature action; do
        [ -n "$fname" ] || continue
        f="$FRAME_EXPORT_DIR/$fname"
        [ -f "$f" ] || continue
        fsize=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
        [ "$fsize" -lt 10240 ] && continue
        touch "$DESIRED_DIR/$fname"
        printf '%s\t%s\n' "$fname" "$source_signature" >> "$BALANCED_STAGING_ENTRIES"
        if [ "$action" = "reuse" ] && [ -f "$STAGING/$fname" ]; then
            continue
        fi
        cp "$f" "$STAGING/$fname"
        NEW_FILES="$NEW_FILES $fname"
    done < "$BALANCED_ACTIONS"
    rm -f "$BALANCED_ACTIONS"
else
    # Copy only NEW photos to staging (skip those already watermarked)
    for subdir in "" upload picker album sync sync/picker sync/upload; do
        dir="$PHOTOS_DIR"
        [ -n "$subdir" ] && dir="$PHOTOS_DIR/$subdir"
        [ -d "$dir" ] || continue
        for ext in jpg jpeg png; do
            for f in "$dir"/*."$ext"; do
                [ -f "$f" ] || continue
                fname=$(basename "$f")
                # Skip if already tracked (first copy wins - dedup across subdirs)
                [ -f "$DESIRED_DIR/$fname" ] && continue
                # Skip tiny files (<10KB) - likely broken and can freeze cheap frames
                fsize=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
                [ "$fsize" -lt 10240 ] && continue
                # Mark as desired
                touch "$DESIRED_DIR/$fname"
                # Skip if already in staging (already watermarked from previous run)
                [ -f "$STAGING/$fname" ] && continue
                # New photo - copy to staging for watermarking
                cp "$f" "$STAGING/$fname"
                NEW_FILES="$NEW_FILES $fname"
            done
        done
    done
fi

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

if [ "$USING_BALANCED_EXPORT" -eq 1 ]; then
    python3 - "$BALANCED_STAGING_ENTRIES" "$STAGING_MANIFEST" <<'PY'
import json
import sys

entries_path, staging_manifest_path = sys.argv[1:3]
entries = {}
with open(entries_path, encoding="utf-8") as fh:
    for raw_line in fh:
        line = raw_line.rstrip("\n")
        if not line:
            continue
        filename, source_signature = line.split("\t", 1)
        entries[filename] = source_signature

with open(staging_manifest_path, "w", encoding="utf-8") as fh:
    json.dump({"version": 1, "entries": entries}, fh, indent=2, sort_keys=True)
PY
else
    rm -f "$STAGING_MANIFEST"
fi

rm -f "$BALANCED_STAGING_ENTRIES"
rm -rf "$DESIRED_DIR"

# Count photos in staging
PHOTO_COUNT=$(ls -1 "$STAGING"/*.jpg "$STAGING"/*.jpeg "$STAGING"/*.png 2>/dev/null | wc -l)

if [ "$PHOTO_COUNT" -eq 0 ]; then
    # Safety: refuse to wipe USB if photos exist on disk but staging is empty
    DISK_COUNT=$(find "$PHOTOS_DIR" -maxdepth 3 -type f \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" \) ! -path "*/thumbs/*" 2>/dev/null | wc -l)
    if [ "$DISK_COUNT" -gt 0 ]; then
        echo "ERROR: 0 photos staged but $DISK_COUNT on disk - aborting to protect frame"
        exit 1
    fi
    # Truly no photos anywhere - stage QR placeholder
    if [ -f "$QR_PLACEHOLDER" ]; then
        cp "$QR_PLACEHOLDER" "$STAGING/"
    fi
    echo "No photos, staging QR placeholder"
else
    # Remove QR placeholder from staging if real photos exist
    rm -f "$STAGING/qr-placeholder.jpg" 2>/dev/null
fi

# Watermark ONLY new photos (not all - staging already has watermarked copies)
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

# Phase 3: Staging persists as watermark cache - do NOT delete it
echo "USB drive updated! Frame should refresh."
