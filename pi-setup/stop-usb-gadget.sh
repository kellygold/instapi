#!/bin/bash
# Stop USB Mass Storage Gadget
# Called when we need to update photos

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/usb-gadget-helper.sh"

usb_gadget_stop
echo "USB Gadget stopped"
