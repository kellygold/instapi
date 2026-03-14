#!/bin/bash
# InstaPi WiFi Setup — AP mode for initial config + client connection
# Uses NetworkManager (nmcli) — standard on Raspberry Pi OS Trixie
# Usage: wifi-setup.sh {check|start-ap|stop-ap|connect|scan}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
MODE_FILE="/tmp/instapi_wifi_mode"
SCAN_FILE="/tmp/wifi_scan.json"
LOG="logger -t instapi-wifi"
AP_CON_NAME="InstaPi-Setup"
AP_IP="192.168.4.1"

# ============================================================
# check — is WiFi connected? exit 0=yes, 1=no
# ============================================================
cmd_check() {
    # Check if any wifi connection exists in NetworkManager
    if ! nmcli -t -f TYPE connection show 2>/dev/null | grep -q '802-11-wireless'; then
        $LOG "No WiFi networks configured in NetworkManager"
        cmd_start_ap
        exit 1
    fi

    # Wait for wlan0 interface
    $LOG "WiFi configured, waiting for interface..."
    for i in $(seq 1 30); do
        if ip link show wlan0 >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    # Wait for connectivity (up to 60 seconds)
    $LOG "Waiting for WiFi connection..."
    for i in $(seq 1 60); do
        if ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1; then
            $LOG "WiFi connected"
            echo "client" > "$MODE_FILE"
            exit 0
        fi
        sleep 1
    done

    # Could not connect — start AP mode
    $LOG "WiFi configured but cannot connect after 60s, starting AP"
    cmd_start_ap
    exit 1
}

# ============================================================
# scan — scan for available networks, save to JSON
# ============================================================
cmd_scan() {
    $LOG "Scanning for WiFi networks..."

    # Force a rescan
    nmcli device wifi rescan 2>/dev/null || true
    sleep 2

    # Parse nmcli output to JSON
    nmcli -t -f SSID,SIGNAL,SECURITY device wifi list 2>/dev/null | python3 -c "
import sys, json

networks = {}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    parts = line.split(':')
    if len(parts) < 3:
        continue
    ssid = parts[0].replace('\\\\:', ':')  # unescape colons in SSID
    if not ssid:
        continue
    try:
        signal = int(parts[1])
    except ValueError:
        signal = 0
    security = 'open' if parts[2] == '' or parts[2] == '--' else 'wpa'
    # Convert signal percentage to approximate dBm for consistency
    dbm = -100 + signal  # rough: 0%=-100dBm, 100%=0dBm
    # Keep strongest signal per SSID
    if ssid not in networks or dbm > networks[ssid]['signal']:
        networks[ssid] = {'ssid': ssid, 'signal': dbm, 'security': security}

result = sorted(networks.values(), key=lambda x: x['signal'], reverse=True)
json.dump(result, sys.stdout, indent=2)
" > "$SCAN_FILE"

    local count
    count=$(python3 -c "import json; print(len(json.load(open('$SCAN_FILE'))))" 2>/dev/null || echo 0)
    $LOG "Scan complete: $count networks found"
}

# ============================================================
# start-ap — create the setup hotspot
# ============================================================
cmd_start_ap() {
    $LOG "Starting AP mode..."

    # Remove any existing AP connection
    nmcli connection delete "$AP_CON_NAME" 2>/dev/null || true

    # Create open AP connection
    nmcli connection add type wifi ifname wlan0 con-name "$AP_CON_NAME" \
        autoconnect no ssid "$AP_CON_NAME" \
        802-11-wireless.mode ap \
        802-11-wireless.band bg \
        ipv4.method shared \
        ipv4.addresses "$AP_IP/24"

    # Remove security (open network for easy setup)
    nmcli connection modify "$AP_CON_NAME" wifi-sec.key-mgmt none 2>/dev/null || true

    # Bring up the AP
    nmcli connection up "$AP_CON_NAME"

    echo "ap" > "$MODE_FILE"
    $LOG "AP mode active — SSID: $AP_CON_NAME, IP: $AP_IP"
}

# ============================================================
# stop-ap — tear down hotspot
# ============================================================
cmd_stop_ap() {
    $LOG "Stopping AP mode..."

    nmcli connection down "$AP_CON_NAME" 2>/dev/null || true
    nmcli connection delete "$AP_CON_NAME" 2>/dev/null || true

    # NetworkManager will auto-reconnect to known networks
    sleep 2

    echo "client" > "$MODE_FILE"
    $LOG "AP mode stopped"
}

# ============================================================
# connect <ssid> [psk] — configure WiFi and switch to client
# ============================================================
cmd_connect() {
    local ssid="$1"
    local psk="${2:-}"

    $LOG "Connecting to '$ssid'..."

    # Stop AP first
    cmd_stop_ap
    sleep 2

    # Connect via nmcli
    local result
    if [ -n "$psk" ]; then
        result=$(nmcli device wifi connect "$ssid" password "$psk" ifname wlan0 2>&1)
    else
        result=$(nmcli device wifi connect "$ssid" ifname wlan0 2>&1)
    fi

    if echo "$result" | grep -qi "error\|failed"; then
        $LOG "nmcli connect failed: $result"
    fi

    # Wait for connectivity (up to 15 seconds)
    for i in $(seq 1 15); do
        if ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1; then
            $LOG "Connected to '$ssid'"
            echo "client" > "$MODE_FILE"
            rm -f /tmp/wifi_fail_count
            exit 0
        fi
        sleep 1
    done

    # Failed — restart AP mode
    $LOG "Failed to connect to '$ssid', restarting AP"
    cmd_start_ap
    exit 1
}

# ============================================================
# Main dispatcher
# ============================================================
case "${1:-}" in
    check)    cmd_check ;;
    start-ap) cmd_start_ap ;;
    stop-ap)  cmd_stop_ap ;;
    connect)  cmd_connect "${2:-}" "${3:-}" ;;
    scan)     cmd_scan ;;
    *)
        echo "Usage: $0 {check|start-ap|stop-ap|connect <ssid> [psk]|scan}"
        exit 1
        ;;
esac
