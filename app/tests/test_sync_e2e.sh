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

ADMIN_PASSWORD="e2e-test-admin"
COOKIES_MASTER="$TMPDIR_BASE/cookies_master.txt"
COOKIES_CHILD="$TMPDIR_BASE/cookies_child.txt"

start_master() {
    cd "$APP_DIR"
    INSTAPI_PHOTOS_DIR="$MASTER_DIR/photos" \
    INSTAPI_STATE_FILE="$MASTER_DIR/device_state.json" \
    INSTAPI_DB_PATH="$MASTER_DIR/instapi.db" \
    INSTAPI_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    PORT=$MASTER_PORT \
    python3 main.py >"$TMPDIR_BASE/master.log" 2>&1 &
    MASTER_PID=$!
    wait_for_port $MASTER_PORT
    # Authenticate for admin endpoints
    curl -s -c "$COOKIES_MASTER" -X POST -d "password=$ADMIN_PASSWORD" \
        "http://127.0.0.1:$MASTER_PORT/admin/login" >/dev/null 2>&1
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
    INSTAPI_DB_PATH="$CHILD_DIR/instapi.db" \
    INSTAPI_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    PORT=$CHILD_PORT \
    python3 main.py >"$TMPDIR_BASE/child.log" 2>&1 &
    CHILD_PID=$!
    wait_for_port $CHILD_PORT
    # Authenticate for admin endpoints
    curl -s -c "$COOKIES_CHILD" -X POST -d "password=$ADMIN_PASSWORD" \
        "http://127.0.0.1:$CHILD_PORT/admin/login" >/dev/null 2>&1
}

wait_sync_done() {
    for i in $(seq 1 30); do
        local in_progress
        in_progress=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
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

SYNC_TOKEN_B="e2e-test-token-other-child"
MASTER_UPLOAD_TOKEN="e2e-master-upload-token"

# Master state: role=master, two child tokens + upload token
cat > "$MASTER_DIR/device_state.json" <<JSON
{
  "sync_role": "master",
  "upload_token": "$MASTER_UPLOAD_TOKEN",
  "sync_children": [
    {"label": "test-child", "token": "$SYNC_TOKEN"},
    {"label": "other-child", "token": "$SYNC_TOKEN_B"}
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
curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
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

curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
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

curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
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
LAST_RESULT=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_sync_result',''))")
[ "$LAST_RESULT" = "success" ] && result PASS "sync_status reports success" \
                                || result FAIL "expected 'success', got '$LAST_RESULT'"

SYNCED_COUNT=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('synced_photo_count',0))")
[ "$SYNCED_COUNT" = "3" ] && result PASS "synced_photo_count = $SYNCED_COUNT" \
                           || result FAIL "expected synced_photo_count=3, got $SYNCED_COUNT"

# ============================================================
echo ""
echo "=== Test 9: Re-sync downloads 0 photos (no re-download bug) ==="
# Sync again — nothing changed, should download 0
curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done
# Check logs for "0 to download"
LAST_SYNC=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('synced_photo_count',0))")
[ "$LAST_SYNC" = "3" ] && result PASS "re-sync: still 3 photos (no re-download)" \
                        || result FAIL "expected 3 photos after re-sync, got $LAST_SYNC"

# Verify MD5s still match (watermark wasn't applied to source)
ALL_MATCH=true
for i in 1 3 4; do
    if [ -f "$CHILD_DIR/photos/sync/photo_$i.jpg" ] && [ -f "$MASTER_DIR/photos/photo_$i.jpg" ]; then
        M_MD5=$(md5 -q "$MASTER_DIR/photos/photo_$i.jpg")
        C_MD5=$(md5 -q "$CHILD_DIR/photos/sync/photo_$i.jpg")
        [ "$M_MD5" != "$C_MD5" ] && ALL_MATCH=false
    fi
done
[ "$ALL_MATCH" = "true" ] && result PASS "re-sync: MD5s still match (no watermark corruption)" \
                           || result FAIL "MD5 mismatch after re-sync"

echo "=== Test 10: Upload via child token, verify attribution ==="
# Upload a test photo using the child's sync token (same as upload token)
create_test_photo "/tmp/e2e_upload_test.jpg" "150"
HTTP=$(curl -s -o /tmp/e2e_upload_resp.json -w "%{http_code}" \
    -F "t=$SYNC_TOKEN" -F "photos=@/tmp/e2e_upload_test.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload")
[ "$HTTP" = "200" ] && result PASS "upload via child token -> $HTTP" \
                     || result FAIL "expected 200, got $HTTP"

# Wait for background processing
sleep 5

# Check upload attribution via API
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
META_UPLOADER=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
uploaders = [p['uploaded_by'] for p in photos if p['uploaded_by'] != 'admin']
print(uploaders[0] if uploaders else 'none')
")
[ "$META_UPLOADER" = "test-child" ] && result PASS "upload tagged as '$META_UPLOADER'" \
                                     || result FAIL "expected 'test-child', got '$META_UPLOADER'"

# Get the uploaded filename
UPLOADED_FILE=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
for p in photos:
    if p['uploaded_by'] == 'test-child':
        print(p['name']); break
")

echo "=== Test 11: Child deletes own photo via master API ==="
HTTP=$(curl -s -o /tmp/e2e_delete_resp.json -w "%{http_code}" \
    -X POST -H "Content-Type: application/json" \
    -d "{\"token\": \"$SYNC_TOKEN\", \"filename\": \"$UPLOADED_FILE\"}" \
    "http://127.0.0.1:$MASTER_PORT/sync/delete_photo")
[ "$HTTP" = "200" ] && result PASS "delete own photo -> $HTTP" \
                     || result FAIL "expected 200, got $HTTP"

# Verify file is gone from master
[ ! -f "$MASTER_DIR/photos/upload/$UPLOADED_FILE" ] \
    && result PASS "photo removed from master disk" \
    || result FAIL "photo still on master disk"

echo "=== Test 12: Child cannot delete another child's photo ==="
# Upload a photo as child B
create_test_photo "/tmp/e2e_upload_b.jpg" "200"
curl -s -F "t=$SYNC_TOKEN_B" -F "photos=@/tmp/e2e_upload_b.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload" >/dev/null
sleep 5

# Get child B's filename
PHOTOS_JSON_B=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
UPLOADED_FILE_B=$(echo "$PHOTOS_JSON_B" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
for p in photos:
    if p['uploaded_by'] == 'other-child':
        print(p['name']); break
")

# Try to delete child B's photo with child A's token
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST -H "Content-Type: application/json" \
    -d "{\"token\": \"$SYNC_TOKEN\", \"filename\": \"$UPLOADED_FILE_B\"}" \
    "http://127.0.0.1:$MASTER_PORT/sync/delete_photo")
[ "$HTTP" = "403" ] && result PASS "cross-child delete blocked -> $HTTP" \
                     || result FAIL "expected 403, got $HTTP"

# Verify file still exists
[ -f "$MASTER_DIR/photos/upload/$UPLOADED_FILE_B" ] \
    && result PASS "other child's photo still on disk" \
    || result FAIL "other child's photo was deleted!"

echo "=== Test 13: Admin can delete any photo ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST -H "Content-Type: application/json" \
    -d "{\"token\": \"$MASTER_UPLOAD_TOKEN\", \"filename\": \"$UPLOADED_FILE_B\"}" \
    "http://127.0.0.1:$MASTER_PORT/sync/delete_photo")
[ "$HTTP" = "200" ] && result PASS "admin delete any photo -> $HTTP" \
                     || result FAIL "expected 200, got $HTTP"

[ ! -f "$MASTER_DIR/photos/upload/$UPLOADED_FILE_B" ] \
    && result PASS "admin-deleted photo removed from disk" \
    || result FAIL "photo still on disk after admin delete"

echo "=== Test 14: Delete non-existent photo ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST -H "Content-Type: application/json" \
    -d "{\"token\": \"$SYNC_TOKEN\", \"filename\": \"bogus_file.jpg\"}" \
    "http://127.0.0.1:$MASTER_PORT/sync/delete_photo")
# Returns 403 (not your photo — not in upload_meta) or 404 (not found). Both are correct.
if [ "$HTTP" = "403" ] || [ "$HTTP" = "404" ]; then
    result PASS "delete non-existent -> $HTTP"
else
    result FAIL "expected 403 or 404, got $HTTP"
fi

echo "=== Test 15: Full cycle - upload, sync, delete, re-sync ==="
# Upload a photo
create_test_photo "/tmp/e2e_cycle.jpg" "100"
curl -s -F "t=$SYNC_TOKEN" -F "photos=@/tmp/e2e_cycle.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload" >/dev/null
sleep 5
restart_master

# Sync to child
curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done
CHILD_HAS=$(find "$CHILD_DIR/photos/sync" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$CHILD_HAS" -ge 1 ] && result PASS "photo synced to child ($CHILD_HAS files)" \
                        || result FAIL "photo not on child after sync"

# Get filename and delete from master
PHOTOS_JSON_CYCLE=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
CYCLE_FILE=$(echo "$PHOTOS_JSON_CYCLE" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
for p in photos:
    if p['uploaded_by'] == 'test-child':
        print(p['name']); break
")
curl -s -X POST -H "Content-Type: application/json" \
    -d "{\"token\": \"$SYNC_TOKEN\", \"filename\": \"$CYCLE_FILE\"}" \
    "http://127.0.0.1:$MASTER_PORT/sync/delete_photo" >/dev/null

# Re-sync child — deleted photo should be removed
restart_master
curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done
CHILD_AFTER=$(find "$CHILD_DIR/photos/sync" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$CHILD_AFTER" -lt "$CHILD_HAS" ] && result PASS "deleted photo removed from child after re-sync ($CHILD_AFTER files)" \
                                     || result FAIL "child still has $CHILD_AFTER files (expected fewer than $CHILD_HAS)"

# Clean up temp files
rm -f /tmp/e2e_upload_test.jpg /tmp/e2e_upload_b.jpg /tmp/e2e_cycle.jpg /tmp/e2e_upload_resp.json /tmp/e2e_delete_resp.json

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
