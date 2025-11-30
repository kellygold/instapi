#!/bin/bash
# Kiosk launcher script for Pi Zero 2 W (no desktop environment)
# This script is called by instapi-kiosk.service

# Disable screen saver and power management
xset s off
xset -dpms
xset s noblank

# Hide cursor
unclutter -idle 0.1 -root &

# Wait for Flask server to be ready
echo "Waiting for InstaPi server..."
until curl -s http://localhost:3000 > /dev/null 2>&1; do
    sleep 1
done
echo "Server ready!"

# Launch Chromium in kiosk mode
exec chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --disable-features=TranslateUI \
    --no-first-run \
    --start-fullscreen \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    http://localhost:3000
