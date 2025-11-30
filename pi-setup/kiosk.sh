#!/bin/bash
# Setup Chromium kiosk mode for InstaPi

AUTOSTART_DIR="$HOME/.config/lxsession/LXDE-pi"
AUTOSTART_FILE="$AUTOSTART_DIR/autostart"

mkdir -p "$AUTOSTART_DIR"

# Backup existing autostart if it exists
if [ -f "$AUTOSTART_FILE" ]; then
    cp "$AUTOSTART_FILE" "$AUTOSTART_FILE.backup"
fi

# Create autostart file
cat > "$AUTOSTART_FILE" << 'EOF'
@lxpanel --profile LXDE-pi
@pcmanfm --desktop --profile LXDE-pi
@xscreensaver -no-splash

# Hide mouse cursor after 3 seconds
@unclutter -idle 3

# Wait for InstaPi server to start
@sleep 5

# Launch Chromium in kiosk mode
@chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --disable-restore-session-state http://localhost:3000
EOF

echo "✅ Kiosk mode configured!"
echo "Reboot to start in kiosk mode: sudo reboot"
