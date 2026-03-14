#!/bin/bash
# InstaPi WiFi Setup — AP mode for initial config + client connection
# Usage: wifi-setup.sh {check|start-ap|stop-ap|connect|scan}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTAPI_DIR="$(dirname "$SCRIPT_DIR")"
MODE_FILE="/tmp/instapi_wifi_mode"
SCAN_FILE="/tmp/wifi_scan.json"
LOG="logger -t instapi-wifi"

HOSTAPD_CONF="$SCRIPT_DIR/hostapd.conf"
DNSMASQ_CONF="$SCRIPT_DIR/dnsmasq-ap.conf"

AP_IP="192.168.4.1"

# ============================================================
# check — is WiFi connected? exit 0=yes, 1=no
# ============================================================
cmd_check() {
    # If no network blocks in wpa_supplicant.conf, definitely not configured
    if ! grep -q 'network=' /etc/wpa_supplicant/wpa_supplicant.conf 2>/dev/null; then
        $LOG "No WiFi networks configured"
        cmd_start_ap
        exit 1
    fi

    # Wait for wlan0 interface to exist (can take a while at boot)
    $LOG "WiFi configured, waiting for wlan0 interface..."
    for i in $(seq 1 30); do
        if ip link show wlan0 >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    # Try to connect for up to 60 seconds
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

    # iw scan, parse into JSON
    local scan_output
    scan_output=$(iw dev wlan0 scan 2>/dev/null || true)

    python3 -c "
import sys, json, re

raw = sys.stdin.read()
networks = []
current = {}

for line in raw.splitlines():
    line = line.strip()
    if line.startswith('BSS '):
        if current.get('ssid'):
            networks.append(current)
        current = {'ssid': '', 'signal': -100, 'security': 'open'}
    elif line.startswith('SSID: '):
        current['ssid'] = line[6:]
    elif line.startswith('signal: '):
        m = re.search(r'(-?\d+)', line)
        if m:
            current['signal'] = int(m.group(1))
    elif 'WPA' in line or 'RSN' in line:
        current['security'] = 'wpa'

if current.get('ssid'):
    networks.append(current)

# Deduplicate by SSID (keep strongest signal)
seen = {}
for n in networks:
    ssid = n['ssid']
    if ssid and (ssid not in seen or n['signal'] > seen[ssid]['signal']):
        seen[ssid] = n

result = sorted(seen.values(), key=lambda x: x['signal'], reverse=True)
json.dump(result, sys.stdout, indent=2)
" <<< "$scan_output" > "$SCAN_FILE"

    $LOG "Scan complete: $(cat "$SCAN_FILE" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))') networks found"
}

# ============================================================
# start-ap — create the setup hotspot
# ============================================================
cmd_start_ap() {
    $LOG "Starting AP mode..."

    # Scan while still in managed mode (before switching to AP)
    cmd_scan || true

    # Stop client-mode services
    systemctl stop wpa_supplicant 2>/dev/null || true
    systemctl stop dhcpcd 2>/dev/null || true
    sleep 1

    # Configure static IP on wlan0
    ip addr flush dev wlan0 2>/dev/null || true
    ip addr add "$AP_IP/24" dev wlan0
    ip link set wlan0 up

    # Start AP
    hostapd -B "$HOSTAPD_CONF"
    sleep 1

    # Start DHCP/DNS (foreground would block, use -d for daemon)
    dnsmasq -C "$DNSMASQ_CONF" --no-daemon &
    DNSMASQ_PID=$!
    echo "$DNSMASQ_PID" > /tmp/instapi_dnsmasq.pid

    echo "ap" > "$MODE_FILE"
    $LOG "AP mode active — SSID: InstaPi-Setup, IP: $AP_IP"
}

# ============================================================
# stop-ap — tear down hotspot, restore client mode
# ============================================================
cmd_stop_ap() {
    $LOG "Stopping AP mode..."

    # Kill AP services
    killall hostapd 2>/dev/null || true
    if [ -f /tmp/instapi_dnsmasq.pid ]; then
        kill "$(cat /tmp/instapi_dnsmasq.pid)" 2>/dev/null || true
        rm -f /tmp/instapi_dnsmasq.pid
    fi
    killall dnsmasq 2>/dev/null || true
    sleep 1

    # Restore client mode
    ip addr flush dev wlan0 2>/dev/null || true
    systemctl start wpa_supplicant 2>/dev/null || true
    sleep 2
    systemctl start dhcpcd 2>/dev/null || true

    echo "client" > "$MODE_FILE"
    $LOG "Client mode restored"
}

# ============================================================
# connect <ssid> <psk> — configure WiFi and switch to client
# ============================================================
cmd_connect() {
    local ssid="$1"
    local psk="${2:-}"

    $LOG "Connecting to '$ssid'..."

    # Stop AP
    cmd_stop_ap

    # Wait for wpa_supplicant to be ready
    sleep 2

    # Add network via wpa_cli
    local net_id
    net_id=$(wpa_cli -i wlan0 add_network 2>/dev/null | tail -1)

    wpa_cli -i wlan0 set_network "$net_id" ssid "\"$ssid\"" >/dev/null
    if [ -n "$psk" ]; then
        wpa_cli -i wlan0 set_network "$net_id" psk "\"$psk\"" >/dev/null
    else
        wpa_cli -i wlan0 set_network "$net_id" key_mgmt NONE >/dev/null
    fi
    wpa_cli -i wlan0 enable_network "$net_id" >/dev/null
    wpa_cli -i wlan0 save_config >/dev/null

    # Restart dhcpcd to get an IP
    systemctl restart dhcpcd 2>/dev/null || true

    # Wait for connectivity (up to 15 seconds)
    for i in $(seq 1 15); do
        if ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1; then
            $LOG "Connected to '$ssid'"
            echo "client" > "$MODE_FILE"
            # Clear any WiFi fail counter
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
