#!/usr/bin/env bash
# End-to-end integration tests for admin auth, disk space, sync history, and security.
# Spins up two Flask instances on localhost and tests various features.
set -euo pipefail

MASTER_PORT=3100
CHILD_PORT=3101
SYNC_TOKEN="e2e-test-token-abc123"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$(cd "$APP_DIR/.." && pwd)"

# Activate venv if present
if [ -f "$REPO_DIR/venv/bin/activate" ]; then
    source "$REPO_DIR/venv/bin/activate"
fi
TMPDIR_BASE=$(mktemp -d)
MASTER_DIR="$TMPDIR_BASE/master"
CHILD_DIR="$TMPDIR_BASE/child"
MASTER_PID=""
CHILD_PID=""
PASS=0; FAIL=0; TOTAL=0
COOKIE_JAR="$TMPDIR_BASE/cookies.txt"
CHILD_COOKIE_JAR="$TMPDIR_BASE/cookies_child.txt"

cleanup() {
    [ -n "$MASTER_PID" ] && kill "$MASTER_PID" 2>/dev/null || true
    [ -n "$CHILD_PID" ] && kill "$CHILD_PID" 2>/dev/null || true
    wait "$MASTER_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
    rm -rf "$TMPDIR_BASE"
    [ "$CREATED_SECRETS" = "true" ] && rm -f "$APP_DIR/secrets.json"
    echo ""
    echo "Cleaned up temp dir and processes."
}
trap cleanup EXIT

result() {
    TOTAL=$((TOTAL+1))
    if [ "$1" = "PASS" ]; then
        PASS=$((PASS+1)); echo "  PASS: $2"
    else
        FAIL=$((FAIL+1)); echo "  FAIL: $2"
    fi
}

wait_for_port() {
    local port=$1
    for i in $(seq 1 30); do
        curl -s "http://127.0.0.1:$port/" >/dev/null 2>&1 && return 0
        sleep 0.5
    done
    echo "ERROR: port $port never came up"
    return 1
}

start_master() {
    cd "$APP_DIR"
    INSTAPI_PHOTOS_DIR="$MASTER_DIR/photos" \
    INSTAPI_STATE_FILE="$MASTER_DIR/device_state.json" \
    INSTAPI_ADMIN_PASSWORD=test-password \
    PORT=$MASTER_PORT \
    python3 main.py >"$TMPDIR_BASE/master.log" 2>&1 &
    MASTER_PID=$!
    wait_for_port $MASTER_PORT
}

stop_master() {
    [ -n "$MASTER_PID" ] && kill "$MASTER_PID" 2>/dev/null || true
    wait "$MASTER_PID" 2>/dev/null || true
    MASTER_PID=""
}

restart_master() {
    stop_master
    start_master
}

start_child() {
    cd "$APP_DIR"
    INSTAPI_PHOTOS_DIR="$CHILD_DIR/photos" \
    INSTAPI_STATE_FILE="$CHILD_DIR/device_state.json" \
    INSTAPI_ADMIN_PASSWORD=test-password \
    PORT=$CHILD_PORT \
    python3 main.py >"$TMPDIR_BASE/child.log" 2>&1 &
    CHILD_PID=$!
    wait_for_port $CHILD_PORT
    # Authenticate for admin endpoints
    curl -s -c "$CHILD_COOKIE_JAR" -X POST -d "password=test-password" \
        "http://127.0.0.1:$CHILD_PORT/admin/login" >/dev/null 2>&1
}

stop_child() {
    [ -n "$CHILD_PID" ] && kill "$CHILD_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
    CHILD_PID=""
}

restart_child() {
    stop_child
    start_child
}

wait_sync_done() {
    for i in $(seq 1 30); do
        local in_progress
        in_progress=$(curl -s -b "$CHILD_COOKIE_JAR" "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('sync_in_progress',False))" 2>/dev/null)
        [ "$in_progress" = "False" ] && return 0
        sleep 1
    done
    echo "WARNING: sync did not complete in 30s"
    return 1
}

create_test_photo() {
    local path="$1"
    local color="${2:-128}"
    python3 -c "
from PIL import Image
img = Image.new('RGB', (100, 100), color=($color, $color, $color))
img.save('$path', 'JPEG')
"
}

# ============================================================
# Create mock secrets.json if not present (needed at module import time)
CREATED_SECRETS=false
if [ ! -f "$APP_DIR/secrets.json" ]; then
    cat > "$APP_DIR/secrets.json" <<SJSON
{
  "flask_secret": "e2e-test-secret",
  "web": {
    "client_id": "test.apps.googleusercontent.com",
    "project_id": "test",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_secret": "test-secret",
    "redirect_uris": ["http://localhost:3100/oauth2callback"]
  }
}
SJSON
    CREATED_SECRETS=true
fi

echo "Setting up isolated directories..."
mkdir -p "$MASTER_DIR/photos" "$CHILD_DIR/photos"

MASTER_UPLOAD_TOKEN="e2e-master-upload-token"

# Master state: role=master, one child token + upload token
cat > "$MASTER_DIR/device_state.json" <<JSON
{
  "sync_role": "master",
  "upload_token": "$MASTER_UPLOAD_TOKEN",
  "sync_children": [
    {"label": "test-child", "token": "$SYNC_TOKEN"}
  ]
}
JSON

# Child state: role=child, pointing at master, long interval (we trigger manually)
cat > "$CHILD_DIR/device_state.json" <<JSON
{
  "sync_role": "child",
  "master_url": "http://127.0.0.1:$MASTER_PORT",
  "sync_token": "$SYNC_TOKEN",
  "sync_interval": 86400
}
JSON

echo "Starting master on port $MASTER_PORT..."
start_master
echo "Starting child on port $CHILD_PORT..."
start_child

# ============================================================
# Admin Auth Tests
# ============================================================
echo ""
echo "=== Test A1: Admin without session -> redirect ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$MASTER_PORT/admin")
[ "$HTTP" = "302" ] && result PASS "admin without session -> $HTTP" \
                     || result FAIL "expected 302, got $HTTP"

echo "=== Test A2: Login with wrong password -> 401 ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST -d "password=wrong" "http://127.0.0.1:$MASTER_PORT/admin/login")
[ "$HTTP" = "401" ] && result PASS "login with wrong password -> $HTTP" \
                     || result FAIL "expected 401, got $HTTP"

echo "=== Test A3: Login with correct password -> 302 redirect + session ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -c "$COOKIE_JAR" -X POST -d "password=test-password" "http://127.0.0.1:$MASTER_PORT/admin/login")
[ "$HTTP" = "302" ] && result PASS "login with correct password -> $HTTP" \
                     || result FAIL "expected 302, got $HTTP"

echo "=== Test A4: Admin with valid session -> 200 ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" "http://127.0.0.1:$MASTER_PORT/admin")
[ "$HTTP" = "200" ] && result PASS "admin with session -> $HTTP" \
                     || result FAIL "expected 200, got $HTTP"

echo "=== Test A5: Logout -> session cleared ==="
curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" "http://127.0.0.1:$MASTER_PORT/admin/logout" > /dev/null
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" "http://127.0.0.1:$MASTER_PORT/admin")
[ "$HTTP" = "302" ] && result PASS "admin after logout -> $HTTP" \
                     || result FAIL "expected 302, got $HTTP"

echo "=== Test A6: Upload page works without admin auth ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$MASTER_PORT/upload?t=$MASTER_UPLOAD_TOKEN")
[ "$HTTP" = "200" ] && result PASS "upload page without auth -> $HTTP" \
                     || result FAIL "expected 200, got $HTTP"

echo "=== Test A7: Upload POST works without admin auth ==="
create_test_photo "$TMPDIR_BASE/test_photo.jpg" "150"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -F "photos=@$TMPDIR_BASE/test_photo.jpg" "http://127.0.0.1:$MASTER_PORT/upload?t=$MASTER_UPLOAD_TOKEN")
[ "$HTTP" = "200" ] && result PASS "upload POST without auth -> $HTTP" \
                     || result FAIL "expected 200, got $HTTP"

echo "=== Test A8: WiFi setup works without admin auth ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$MASTER_PORT/wifi-setup")
[ "$HTTP" = "200" ] && result PASS "wifi-setup without auth -> $HTTP" \
                     || result FAIL "expected 200, got $HTTP"

echo "=== Test A9: Delete photo without auth -> blocked ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" \
    -d '{"path":"/static/photos/test.jpg"}' "http://127.0.0.1:$MASTER_PORT/admin/delete_photo")
if [ "$HTTP" = "401" ] || [ "$HTTP" = "302" ]; then
    result PASS "delete photo without auth -> $HTTP"
else
    result FAIL "expected 401 or 302, got $HTTP"
fi

echo "=== Test A10: Settings without auth -> blocked ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$MASTER_PORT/admin/settings")
if [ "$HTTP" = "302" ] || [ "$HTTP" = "401" ]; then
    result PASS "settings without auth -> $HTTP"
else
    result FAIL "expected 302 or 401, got $HTTP"
fi

# ============================================================
# Disk Space Tests
# ============================================================
echo ""
echo "=== Test D1: System info includes storage ==="
# Re-login for authenticated requests
curl -s -o /dev/null -c "$COOKIE_JAR" -X POST -d "password=test-password" "http://127.0.0.1:$MASTER_PORT/admin/login"
RESP=$(curl -s -b "$COOKIE_JAR" "http://127.0.0.1:$MASTER_PORT/admin/system_info")
HAS_FREE=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if 'storage' in d and 'free_gb' in d['storage'] else 'no')" 2>/dev/null || echo "no")
[ "$HAS_FREE" = "yes" ] && result PASS "system_info has storage.free_gb" \
                          || result FAIL "storage.free_gb missing from system_info"

echo "=== Test D2: Storage has expected fields ==="
ALL_FIELDS=$(echo "$RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d.get('storage', {})
fields = ['total_gb', 'free_gb', 'used_gb', 'photos_mb']
present = [f for f in fields if f in s]
print(','.join(present))
" 2>/dev/null || echo "")
EXPECTED_COUNT=$(echo "$ALL_FIELDS" | tr ',' '\n' | wc -l | tr -d ' ')
[ "$EXPECTED_COUNT" -ge 3 ] && result PASS "storage has fields: $ALL_FIELDS" \
                              || result FAIL "expected >=3 storage fields, got: $ALL_FIELDS"

# ============================================================
# Sync History Tests
# ============================================================
echo ""
echo "=== Test H1: After sync, status includes sync_history ==="
# Add a test photo to master so sync has something to do
create_test_photo "$MASTER_DIR/photos/history_test.jpg" "100"
restart_master

# Trigger sync on child
curl -s -b "$CHILD_COOKIE_JAR" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done

SYNC_STATUS=$(curl -s -b "$CHILD_COOKIE_JAR" "http://127.0.0.1:$CHILD_PORT/admin/sync_status")
HAS_HISTORY=$(echo "$SYNC_STATUS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
h = d.get('sync_history', None)
print('yes' if isinstance(h, list) else 'no')
" 2>/dev/null || echo "no")
[ "$HAS_HISTORY" = "yes" ] && result PASS "sync_status has sync_history array" \
                             || result FAIL "sync_history missing or not array"

echo "=== Test H2: History entry has expected fields ==="
ENTRY_FIELDS=$(echo "$SYNC_STATUS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
h = d.get('sync_history', [])
if h:
    entry = h[0]
    fields = []
    if 'timestamp' in entry: fields.append('timestamp')
    if 'result' in entry: fields.append('result')
    if 'photos_added' in entry: fields.append('photos_added')
    if 'photos_removed' in entry: fields.append('photos_removed')
    print(','.join(fields))
else:
    print('')
" 2>/dev/null || echo "")
FIELD_COUNT=$(echo "$ENTRY_FIELDS" | tr ',' '\n' | grep -c . || echo "0")
[ "$FIELD_COUNT" -ge 2 ] && result PASS "history entry has fields: $ENTRY_FIELDS" \
                           || result FAIL "expected >=2 fields in history entry, got: $ENTRY_FIELDS"

echo "=== Test H3: After failed sync, history has error entry ==="
# Stop master so sync fails
stop_master

curl -s -b "$CHILD_COOKIE_JAR" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done || true

SYNC_STATUS=$(curl -s -b "$CHILD_COOKIE_JAR" "http://127.0.0.1:$CHILD_PORT/admin/sync_status")
HAS_ERROR=$(echo "$SYNC_STATUS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
h = d.get('sync_history', [])
for entry in h:
    r = entry.get('result', '')
    if r != 'success':
        print('yes'); break
else:
    print('no')
" 2>/dev/null || echo "no")
[ "$HAS_ERROR" = "yes" ] && result PASS "history has error entry after failed sync" \
                           || result FAIL "no error entry found in sync_history"

echo "=== Test H5: History persists across restart ==="
# Get history count before restart
BEFORE_COUNT=$(echo "$SYNC_STATUS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(len(d.get('sync_history', [])))
" 2>/dev/null || echo "0")

# Restart child and check history is still present
restart_master
restart_child

SYNC_STATUS=$(curl -s -b "$CHILD_COOKIE_JAR" "http://127.0.0.1:$CHILD_PORT/admin/sync_status")
AFTER_COUNT=$(echo "$SYNC_STATUS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(len(d.get('sync_history', [])))
" 2>/dev/null || echo "0")
[ "$AFTER_COUNT" -ge "$BEFORE_COUNT" ] && result PASS "history persists: $AFTER_COUNT entries (was $BEFORE_COUNT)" \
                                         || result FAIL "history lost: $AFTER_COUNT entries (was $BEFORE_COUNT)"

# ============================================================
# Security Tests
# ============================================================
echo ""
echo "=== Test S1: Debug mode off ==="
RESP=$(curl -s "http://127.0.0.1:$MASTER_PORT/nonexistent-page-that-404s")
if echo "$RESP" | grep -qi "Debugger\|Traceback"; then
    result FAIL "debug info exposed in 404 response"
else
    result PASS "no debug info in 404 response"
fi

echo "=== Test S2: Upload with invalid token -> rejected ==="
create_test_photo "$TMPDIR_BASE/test_invalid_upload.jpg" "200"
HTTP=$(curl -s -o "$TMPDIR_BASE/invalid_upload_resp.txt" -w "%{http_code}" \
    -F "photos=@$TMPDIR_BASE/test_invalid_upload.jpg" "http://127.0.0.1:$MASTER_PORT/upload?t=invalid-token")
BODY=$(cat "$TMPDIR_BASE/invalid_upload_resp.txt")
if [ "$HTTP" = "403" ] || echo "$BODY" | grep -qi "invalid\|error\|denied\|unauthorized"; then
    result PASS "upload with invalid token rejected -> $HTTP"
else
    result FAIL "expected rejection for invalid token, got $HTTP with no error in body"
fi

# ============================================================
echo ""
echo "========================================"
echo "  Results: $PASS/$TOTAL passed, $FAIL failed"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Master log tail:"
    tail -20 "$TMPDIR_BASE/master.log" 2>/dev/null || true
    echo ""
    echo "Child log tail:"
    tail -20 "$TMPDIR_BASE/child.log" 2>/dev/null || true
fi

[ "$FAIL" = "0" ] && exit 0 || exit 1
