#!/bin/bash
# Shared USB gadget helper — consistent timing and patterns across all scripts.
# Source this file: . "$(dirname "$0")/usb-gadget-helper.sh"
#
# Tune these delays if your photo frame needs more/less time:
USB_STOP_DELAY=1    # seconds after removing gadget
USB_START_DELAY=2   # seconds between double-connect (forces frame to re-read)

usb_gadget_stop() {
    sudo modprobe -r g_mass_storage 2>/dev/null || true
    sleep "$USB_STOP_DELAY"
}

usb_gadget_start() {
    local img_file="$1"
    # Double-connect: first connect warms up the frame, second forces
    # a fresh directory read. Fixes frames that cache USB contents.
    sudo modprobe g_mass_storage file="$img_file" stall=0 removable=1 ro=0
    sleep "$USB_START_DELAY"
    sudo modprobe -r g_mass_storage 2>/dev/null || true
    sleep "$USB_START_DELAY"
    sudo modprobe g_mass_storage file="$img_file" stall=0 removable=1 ro=0
}

usb_mount() {
    local img_file="$1"
    local mount_point="$2"
    mkdir -p "$mount_point"
    sudo mount -o loop "$img_file" "$mount_point"
}

usb_unmount() {
    local mount_point="$1"
    sync
    sudo umount "$mount_point"
}

# Main entry point: prepare files in staging, then do a quick swap on the USB.
# Caller prepares $staging_dir with the COMPLETE set of files that should be on USB.
# This function handles: stop gadget → mount → sync from staging → unmount → start gadget.
# Frame is only down during this function (~5s), not during preparation.
usb_prepare_and_swap() {
    local img_file="$1"
    local mount_point="$2"
    local staging_dir="$3"
    local reformat="${4:-false}"

    # Acquire lock to prevent concurrent USB operations.
    # Use world-writable lock so both root (systemd) and instapi (Flask) can acquire it.
    local lockfile="/tmp/usb_update.lock"
    touch "$lockfile" 2>/dev/null; chmod 666 "$lockfile" 2>/dev/null
    exec 9>"$lockfile"
    flock -n 9 || { echo "USB update already in progress, skipping"; return 1; }

    usb_gadget_stop

    if [ "$reformat" = "true" ]; then
        /usr/sbin/mkfs.fat -F 32 "$img_file" > /dev/null 2>&1
    else
        # Auto-repair FAT corruption (e.g. from OOM crash mid-write)
        sudo /usr/sbin/fsck.fat -a "$img_file" > /dev/null 2>&1 || true
    fi

    usb_mount "$img_file" "$mount_point"

    # Delete ALL files from USB that aren't in staging (photos + FSCK junk + anything else)
    for f in "$mount_point"/*; do
        [ -f "$f" ] || continue
        fname=$(basename "$f")
        if [ ! -f "$staging_dir/$fname" ]; then
            sudo rm "$f"
        fi
    done

    # Copy new/changed files from staging to USB
    for f in "$staging_dir"/*; do
        [ -f "$f" ] || continue
        fname=$(basename "$f")
        dest="$mount_point/$fname"
        if [ ! -f "$dest" ] || [ "$(stat -c%s "$f" 2>/dev/null)" != "$(stat -c%s "$dest" 2>/dev/null)" ]; then
            sudo cp "$f" "$dest"
        fi
    done

    usb_unmount "$mount_point"
    usb_gadget_start "$img_file"

    # Release lock
    flock -u 9
}

# Watermark files in a directory.
# Automatically uses parallel processing on devices with >1.5GB RAM (Pi 5),
# falls back to sequential on low-RAM devices (Pi Zero).
# $1 = directory containing photos to watermark
# $2 = space-separated list of filenames to watermark (or "all" for all photos)
usb_watermark() {
    local dir="$1"
    local files="$2"
    local instapi_dir
    instapi_dir="$(dirname "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"
    local venv="$instapi_dir/app/venv/bin/python3"

    [ -f "$venv" ] || return 0

    cd "$instapi_dir/app"
    "$venv" -c "
import sys, os, gc, glob

# Build target list
target_dir = '$dir'
file_list = '''$files'''.strip()
if file_list == 'all':
    targets = glob.glob(os.path.join(target_dir, '*.jpg')) + glob.glob(os.path.join(target_dir, '*.jpeg')) + glob.glob(os.path.join(target_dir, '*.png'))
else:
    targets = [os.path.join(target_dir, f) for f in file_list.split() if f]
targets = [p for p in targets if os.path.exists(p) and 'qr-placeholder' not in p and 'wifi-fix' not in p]

if not targets:
    print('No photos to watermark')
    sys.exit(0)

# Get watermark URL once (requires DB access — do this in parent process)
from utils import get_upload_url, add_qr_watermark
url = get_upload_url()

# Check available RAM to decide parallel vs sequential
mem_gb = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / (1024**3)

if mem_gb >= 1.5 and len(targets) > 1:
    import multiprocessing
    workers = min(4, multiprocessing.cpu_count(), len(targets))
    # Use a wrapper that passes the pre-computed URL (avoids DB access in workers)
    def _wm(path):
        add_qr_watermark(path, watermark_url=url)
    with multiprocessing.Pool(workers) as pool:
        pool.map(_wm, targets)
    print(f'Watermarked {len(targets)} photos ({workers} workers)')
else:
    count = 0
    for i, path in enumerate(targets):
        add_qr_watermark(path, watermark_url=url)
        count += 1
        if (i + 1) % 3 == 0:
            gc.collect()
    print(f'Watermarked {count} photos (sequential)')
" 2>/dev/null || echo "Watermark failed (non-fatal)"
}
