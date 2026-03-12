#!/usr/bin/env bash
# End-to-end integration test for master/child photo sync.
# Spins up two Flask instances on localhost and syncs real photos between them.
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
    PORT=$CHILD_PORT \
    python3 main.py >"$TMPDIR_BASE/child.log" 2>&1 &
    CHILD_PID=$!
    wait_for_port $CHILD_PORT
}

wait_sync_done() {
    for i in $(seq 1 30); do
        local in_progress
        in_progress=$(curl -s "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
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

# Master state: role=master, one child token
cat > "$MASTER_DIR/device_state.json" <<JSON
{
  "sync_role": "master",
  "sync_children": [{"label": "test-child", "token": "$SYNC_TOKEN"}]
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
echo ""
echo "=== Test 1: Bad token returns 403 ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://127.0.0.1:$MASTER_PORT/sync/manifest?token=wrong-token")
[ "$HTTP" = "403" ] && result PASS "manifest with bad token -> $HTTP" \
                     || result FAIL "expected 403, got $HTTP"

echo "=== Test 2: Valid token returns 200 manifest ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://127.0.0.1:$MASTER_PORT/sync/manifest?token=$SYNC_TOKEN")
[ "$HTTP" = "200" ] && result PASS "manifest with good token -> $HTTP" \
                     || result FAIL "expected 200, got $HTTP"

MANIFEST=$(curl -s "http://127.0.0.1:$MASTER_PORT/sync/manifest?token=$SYNC_TOKEN")
PHOTO_COUNT=$(echo "$MANIFEST" | python3 -c "import sys,json; print(json.load(sys.stdin)['photo_count'])")
[ "$PHOTO_COUNT" = "0" ] && result PASS "empty manifest has 0 photos" \
                          || result FAIL "expected 0 photos, got $PHOTO_COUNT"

echo "=== Test 3: Path traversal returns 403 ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --path-as-is \
    "http://127.0.0.1:$MASTER_PORT/sync/photo/../../etc/passwd?token=$SYNC_TOKEN")
# Flask may normalize the path and return 404, or our guard returns 403. Either is acceptable.
if [ "$HTTP" = "403" ] || [ "$HTTP" = "404" ]; then
    result PASS "path traversal blocked -> $HTTP"
else
    result FAIL "expected 403 or 404, got $HTTP"
fi

# ============================================================
echo ""
echo "=== Test 4: Initial sync - 3 photos ==="

# Add 3 test photos to master
for i in 1 2 3; do
    create_test_photo "$MASTER_DIR/photos/photo_$i.jpg" "$((i * 80))"
done

# Restart master so manifest cache rebuilds from disk
restart_master

# Trigger sync on child
curl -s -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done

# Verify photos landed in child's sync dir
CHILD_COUNT=$(find "$CHILD_DIR/photos/sync" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$CHILD_COUNT" = "3" ] && result PASS "initial sync: $CHILD_COUNT photos on child" \
                          || result FAIL "expected 3 photos on child, got $CHILD_COUNT"

# Verify md5s match
echo "=== Test 5: MD5 verification ==="
ALL_MATCH=true
for i in 1 2 3; do
    if [ -f "$CHILD_DIR/photos/sync/photo_$i.jpg" ]; then
        MASTER_MD5=$(md5 -q "$MASTER_DIR/photos/photo_$i.jpg")
        CHILD_MD5=$(md5 -q "$CHILD_DIR/photos/sync/photo_$i.jpg")
        if [ "$MASTER_MD5" != "$CHILD_MD5" ]; then
            ALL_MATCH=false
            echo "    photo_$i.jpg: master=$MASTER_MD5 child=$CHILD_MD5"
        fi
    else
        ALL_MATCH=false
        echo "    photo_$i.jpg missing on child"
    fi
done
[ "$ALL_MATCH" = "true" ] && result PASS "all md5s match" \
                           || result FAIL "md5 mismatch"

# ============================================================
echo ""
echo "=== Test 6: Deletion sync ==="
rm "$MASTER_DIR/photos/photo_2.jpg"
restart_master

curl -s -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done

CHILD_COUNT=$(find "$CHILD_DIR/photos/sync" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$CHILD_COUNT" = "2" ] && result PASS "deletion sync: $CHILD_COUNT photos remain" \
                          || result FAIL "expected 2 photos, got $CHILD_COUNT"

[ ! -f "$CHILD_DIR/photos/sync/photo_2.jpg" ] \
    && result PASS "photo_2.jpg correctly removed from child" \
    || result FAIL "photo_2.jpg still exists on child"

# ============================================================
echo ""
echo "=== Test 7: Incremental sync - add photo_4 ==="
create_test_photo "$MASTER_DIR/photos/photo_4.jpg" "200"
restart_master

curl -s -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done

CHILD_COUNT=$(find "$CHILD_DIR/photos/sync" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$CHILD_COUNT" = "3" ] && result PASS "incremental sync: $CHILD_COUNT photos" \
                          || result FAIL "expected 3 photos, got $CHILD_COUNT"

[ -f "$CHILD_DIR/photos/sync/photo_4.jpg" ] \
    && result PASS "photo_4.jpg arrived on child" \
    || result FAIL "photo_4.jpg missing on child"

# ============================================================
echo ""
echo "=== Test 8: Sync status reports success ==="
LAST_RESULT=$(curl -s "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_sync_result',''))")
[ "$LAST_RESULT" = "success" ] && result PASS "sync_status reports success" \
                                || result FAIL "expected 'success', got '$LAST_RESULT'"

SYNCED_COUNT=$(curl -s "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('synced_photo_count',0))")
[ "$SYNCED_COUNT" = "3" ] && result PASS "synced_photo_count = $SYNCED_COUNT" \
                           || result FAIL "expected synced_photo_count=3, got $SYNCED_COUNT"

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
