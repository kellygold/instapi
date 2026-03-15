#!/bin/bash
# Shared USB gadget helper — consistent timing across all scripts.
# Source this file: . "$(dirname "$0")/usb-gadget-helper.sh"
#
# Tune these delays if your photo frame needs more/less time:
USB_STOP_DELAY=3    # seconds after removing gadget (frame deregisters USB)
USB_START_DELAY=0   # seconds after starting gadget (frame detects USB)

usb_gadget_stop() {
    sudo modprobe -r g_mass_storage 2>/dev/null || true
    sleep "$USB_STOP_DELAY"
}

usb_gadget_start() {
    local img_file="$1"
    sudo modprobe g_mass_storage file="$img_file" stall=0 removable=1 ro=0
    [ "$USB_START_DELAY" -gt 0 ] && sleep "$USB_START_DELAY"
}
