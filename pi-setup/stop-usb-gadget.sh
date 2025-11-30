#!/bin/bash
# Stop USB Mass Storage Gadget
# Called when we need to update photos

# Unload the gadget module
sudo modprobe -r g_mass_storage

echo "USB Gadget stopped"
