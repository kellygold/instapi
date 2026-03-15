#!/bin/bash
# Shared USB gadget helper — consistent timing across all scripts.
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
