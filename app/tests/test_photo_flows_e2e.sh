#!/usr/bin/env bash
# Comprehensive e2e tests for photo lifecycle flows: upload, manifest, delete, sync, DB consistency.
# Spins up two Flask instances (master + child) on localhost and exercises full photo paths.
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

ADMIN_PASSWORD="e2e-test-admin"
COOKIES_MASTER="$TMPDIR_BASE/cookies_master.txt"
COOKIES_CHILD="$TMPDIR_BASE/cookies_child.txt"

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

create_test_photo() {
    local path="$1"
    local color="${2:-128}"
    python3 -c "
from PIL import Image
img = Image.new('RGB', (100, 100), color=($color, $color, $color))
img.save('$path', 'JPEG')
"
}

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
        in_progress=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('sync_in_progress',False))" 2>/dev/null)
        [ "$in_progress" = "False" ] && return 0
        sleep 1
    done
    echo "WARNING: sync did not complete in 30s"
    return 1
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
# Upload Tests
# ============================================================
echo ""
echo "=== U1: Upload 1 photo -> file on disk, thumbnail, DB record ==="
create_test_photo "$TMPDIR_BASE/u1_photo.jpg" "100"
HTTP=$(curl -s -o "$TMPDIR_BASE/u1_resp.json" -w "%{http_code}" \
    -F "t=$MASTER_UPLOAD_TOKEN" -F "photos=@$TMPDIR_BASE/u1_photo.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload")
[ "$HTTP" = "200" ] && result PASS "U1: upload returned $HTTP" \
                     || result FAIL "U1: expected 200, got $HTTP"

# Wait for background processing
sleep 5

# Check file on disk in photos/upload/
U1_DISK_COUNT=$(find "$MASTER_DIR/photos/upload" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$U1_DISK_COUNT" -ge 1 ] && result PASS "U1: file on disk ($U1_DISK_COUNT in upload/)" \
                             || result FAIL "U1: no file in upload/ dir"

# Check thumbnail
U1_THUMB_COUNT=$(find "$MASTER_DIR/photos/thumbs" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$U1_THUMB_COUNT" -ge 1 ] && result PASS "U1: thumbnail created ($U1_THUMB_COUNT in thumbs/)" \
                              || result FAIL "U1: no thumbnail found"

# Check DB record via /admin/photos
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
U1_DB_COUNT=$(echo "$PHOTOS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
[ "$U1_DB_COUNT" -ge 1 ] && result PASS "U1: DB has $U1_DB_COUNT record(s)" \
                           || result FAIL "U1: DB has 0 records"

# Check that DB record has size > 0
U1_HAS_SIZE=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
print('yes' if photos and photos[0].get('size', 0) > 0 else 'no')
")
[ "$U1_HAS_SIZE" = "yes" ] && result PASS "U1: DB record has size > 0" \
                             || result FAIL "U1: DB record missing size"

# ============================================================
echo ""
echo "=== U2: Upload 5 photos batch -> all processed ==="
# Clean slate: restart master to clear previous photos from manifest
# (but keep the DB — we just want to test batch upload)
for i in 1 2 3 4 5; do
    create_test_photo "$TMPDIR_BASE/u2_photo_$i.jpg" "$((i * 40))"
done

HTTP=$(curl -s -o "$TMPDIR_BASE/u2_resp.json" -w "%{http_code}" \
    -F "t=$MASTER_UPLOAD_TOKEN" \
    -F "photos=@$TMPDIR_BASE/u2_photo_1.jpg" \
    -F "photos=@$TMPDIR_BASE/u2_photo_2.jpg" \
    -F "photos=@$TMPDIR_BASE/u2_photo_3.jpg" \
    -F "photos=@$TMPDIR_BASE/u2_photo_4.jpg" \
    -F "photos=@$TMPDIR_BASE/u2_photo_5.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload")

U2_COUNT=$(cat "$TMPDIR_BASE/u2_resp.json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))")
[ "$U2_COUNT" = "5" ] && result PASS "U2: upload accepted 5 photos (count=$U2_COUNT)" \
                        || result FAIL "U2: expected count=5, got $U2_COUNT"

# Wait for background processing of batch
sleep 15

U2_DISK_COUNT=$(find "$MASTER_DIR/photos/upload" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$U2_DISK_COUNT" -ge 6 ] && result PASS "U2: $U2_DISK_COUNT files on disk (1 from U1 + 5)" \
                             || result FAIL "U2: expected >=6 files on disk, got $U2_DISK_COUNT"

U2_THUMB_COUNT=$(find "$MASTER_DIR/photos/thumbs" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$U2_THUMB_COUNT" -ge 6 ] && result PASS "U2: $U2_THUMB_COUNT thumbnails" \
                              || result FAIL "U2: expected >=6 thumbnails, got $U2_THUMB_COUNT"

PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
U2_DB_COUNT=$(echo "$PHOTOS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
[ "$U2_DB_COUNT" -ge 6 ] && result PASS "U2: DB has $U2_DB_COUNT records" \
                           || result FAIL "U2: expected >=6 DB records, got $U2_DB_COUNT"

# ============================================================
echo ""
echo "=== U3: Upload invalid file (not an image) -> rejected ==="
echo "not an image" > "$TMPDIR_BASE/u3_fake.jpg"
HTTP=$(curl -s -o "$TMPDIR_BASE/u3_resp.json" -w "%{http_code}" \
    -F "t=$MASTER_UPLOAD_TOKEN" -F "photos=@$TMPDIR_BASE/u3_fake.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload")

U3_RESP=$(cat "$TMPDIR_BASE/u3_resp.json")
# Should have count=0 or success=false (no valid files staged)
U3_OK=$(echo "$U3_RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
# Either success=false (no valid files) or skipped >= 1
print('yes' if not d.get('success', True) or d.get('skipped', 0) >= 1 else 'no')
")
[ "$U3_OK" = "yes" ] && result PASS "U3: invalid file rejected ($U3_RESP)" \
                       || result FAIL "U3: invalid file was not rejected ($U3_RESP)"

# ============================================================
echo ""
echo "=== U4: Upload oversized file -> rejected, valid files still process ==="
# Create a valid small photo
create_test_photo "$TMPDIR_BASE/u4_small.jpg" "180"
# Create a >10MB file with valid JPEG header
python3 -c "
data = b'\xff\xd8\xff\xe0' + b'\x00' * (11 * 1024 * 1024)
with open('$TMPDIR_BASE/u4_big.jpg', 'wb') as f:
    f.write(data)
"

HTTP=$(curl -s -o "$TMPDIR_BASE/u4_resp.json" -w "%{http_code}" \
    -F "t=$MASTER_UPLOAD_TOKEN" \
    -F "photos=@$TMPDIR_BASE/u4_small.jpg" \
    -F "photos=@$TMPDIR_BASE/u4_big.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload")

U4_RESP=$(cat "$TMPDIR_BASE/u4_resp.json")
U4_COUNT=$(echo "$U4_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))")
U4_SKIPPED=$(echo "$U4_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('skipped',0))")
[ "$U4_COUNT" = "1" ] && result PASS "U4: valid photo accepted (count=$U4_COUNT)" \
                        || result FAIL "U4: expected count=1, got $U4_COUNT"
[ "$U4_SKIPPED" = "1" ] && result PASS "U4: oversized file skipped (skipped=$U4_SKIPPED)" \
                          || result FAIL "U4: expected skipped=1, got $U4_SKIPPED"

sleep 5

# ============================================================
echo ""
echo "=== U5: Upload valid JPEG -> processed output is valid JPEG ==="
# Create a wider-than-tall image to verify processing doesn't corrupt it
python3 -c "
from PIL import Image
img = Image.new('RGB', (200, 100), color=(255, 0, 0))
img.save('$TMPDIR_BASE/u5_wide.jpg', 'JPEG')
"

HTTP=$(curl -s -o "$TMPDIR_BASE/u5_resp.json" -w "%{http_code}" \
    -F "t=$MASTER_UPLOAD_TOKEN" -F "photos=@$TMPDIR_BASE/u5_wide.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload")
[ "$HTTP" = "200" ] && result PASS "U5: upload accepted" \
                     || result FAIL "U5: expected 200, got $HTTP"

sleep 5

# Find the most recently created file and verify it's a valid JPEG
U5_VALID=$(python3 -c "
import glob, os
from PIL import Image
files = sorted(glob.glob('$MASTER_DIR/photos/upload/*.jpg'), key=os.path.getmtime, reverse=True)
if files:
    img = Image.open(files[0])
    img.verify()
    print('yes')
else:
    print('no')
")
[ "$U5_VALID" = "yes" ] && result PASS "U5: processed photo is valid JPEG" \
                          || result FAIL "U5: processed photo is not valid JPEG"

# ============================================================
echo ""
echo "=== U6: Upload via child token -> uploaded_by = child label ==="
create_test_photo "$TMPDIR_BASE/u6_child.jpg" "160"
HTTP=$(curl -s -o "$TMPDIR_BASE/u6_resp.json" -w "%{http_code}" \
    -F "t=$SYNC_TOKEN" -F "photos=@$TMPDIR_BASE/u6_child.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload")
[ "$HTTP" = "200" ] && result PASS "U6: upload via child token -> $HTTP" \
                     || result FAIL "U6: expected 200, got $HTTP"

sleep 5

PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
U6_UPLOADER=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
child_uploads = [p['uploaded_by'] for p in photos if p['uploaded_by'] == 'test-child']
print(child_uploads[0] if child_uploads else 'none')
")
[ "$U6_UPLOADER" = "test-child" ] && result PASS "U6: uploaded_by = '$U6_UPLOADER'" \
                                    || result FAIL "U6: expected 'test-child', got '$U6_UPLOADER'"

# ============================================================
echo ""
echo "=== U7: Upload processing status ==="
# Upload 3 photos and immediately check status
for i in 1 2 3; do
    create_test_photo "$TMPDIR_BASE/u7_photo_$i.jpg" "$((i * 60))"
done

curl -s -o /dev/null \
    -F "t=$MASTER_UPLOAD_TOKEN" \
    -F "photos=@$TMPDIR_BASE/u7_photo_1.jpg" \
    -F "photos=@$TMPDIR_BASE/u7_photo_2.jpg" \
    -F "photos=@$TMPDIR_BASE/u7_photo_3.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload"

# Immediately check status — should show processing=true or already done
U7_STATUS=$(curl -s "http://127.0.0.1:$MASTER_PORT/upload/status")
U7_HAS_FIELDS=$(echo "$U7_STATUS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('yes' if 'processing' in d and 'total' in d else 'no')
")
[ "$U7_HAS_FIELDS" = "yes" ] && result PASS "U7: upload/status has processing and total fields" \
                                || result FAIL "U7: upload/status missing expected fields ($U7_STATUS)"

# Wait for processing to finish
sleep 10

U7_DONE=$(curl -s "http://127.0.0.1:$MASTER_PORT/upload/status" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('yes' if not d.get('processing', True) else 'no')
")
[ "$U7_DONE" = "yes" ] && result PASS "U7: processing completed (processing=false)" \
                         || result FAIL "U7: processing still in progress after wait"

# ============================================================
# Manifest Tests
# ============================================================
echo ""
echo "=== M1: Manifest includes upload_meta ==="
# Mark manifest dirty so it rebuilds with all uploaded photos
restart_master

MANIFEST=$(curl -s "http://127.0.0.1:$MASTER_PORT/sync/manifest?token=$SYNC_TOKEN")
M1_HAS_META=$(echo "$MANIFEST" | python3 -c "
import sys, json
d = json.load(sys.stdin)
meta = d.get('upload_meta', {})
# Should have at least one entry mapping filename -> child label
has_child = any(v == 'test-child' for v in meta.values())
print('yes' if has_child else 'no')
")
[ "$M1_HAS_META" = "yes" ] && result PASS "M1: manifest upload_meta has test-child entry" \
                              || result FAIL "M1: upload_meta missing test-child entry"

# ============================================================
echo ""
echo "=== M2: Manifest reflects deletions without restart ==="
# Get a photo name to delete
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
M2_FILE=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
# Pick the first admin-uploaded photo
for p in photos:
    if p['uploaded_by'] == 'admin':
        print(p['name']); break
else:
    if photos: print(photos[0]['name'])
    else: print('')
")

if [ -n "$M2_FILE" ]; then
    # Get manifest photo count before delete
    M2_BEFORE=$(echo "$MANIFEST" | python3 -c "import sys,json; print(json.load(sys.stdin)['photo_count'])")

    # Delete via admin API
    M2_PATH="/static/photos/upload/$M2_FILE"
    curl -s -b "$COOKIES_MASTER" -X POST -H "Content-Type: application/json" \
        -d "{\"path\": \"$M2_PATH\"}" \
        "http://127.0.0.1:$MASTER_PORT/admin/delete_photo" >/dev/null

    # Fetch manifest again (no restart)
    MANIFEST2=$(curl -s "http://127.0.0.1:$MASTER_PORT/sync/manifest?token=$SYNC_TOKEN")
    M2_AFTER=$(echo "$MANIFEST2" | python3 -c "import sys,json; print(json.load(sys.stdin)['photo_count'])")

    [ "$M2_AFTER" -lt "$M2_BEFORE" ] && result PASS "M2: manifest updated after delete ($M2_BEFORE -> $M2_AFTER)" \
                                       || result FAIL "M2: manifest not updated ($M2_BEFORE -> $M2_AFTER)"
else
    result FAIL "M2: no photo available to delete"
fi

# ============================================================
echo ""
echo "=== M3: Manifest MD5 matches file on disk ==="
MANIFEST3=$(curl -s "http://127.0.0.1:$MASTER_PORT/sync/manifest?token=$SYNC_TOKEN")
M3_CHECK=$(echo "$MANIFEST3" | python3 -c "
import sys, json, hashlib, os
d = json.load(sys.stdin)
photos = d.get('photos', [])
if not photos:
    print('no_photos')
else:
    p = photos[0]
    path = os.path.join('$MASTER_DIR/photos', p['path'])
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    disk_md5 = h.hexdigest()
    manifest_md5 = p['md5']
    print('match' if disk_md5 == manifest_md5 else f'mismatch:{disk_md5}!={manifest_md5}')
")
[ "$M3_CHECK" = "match" ] && result PASS "M3: manifest MD5 matches file on disk" \
                            || result FAIL "M3: MD5 mismatch ($M3_CHECK)"

# ============================================================
echo ""
echo "=== M4: Child sync downloads to sync/ subdir ==="
# Trigger sync on child
curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done

M4_SYNC_COUNT=$(find "$CHILD_DIR/photos/sync" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
[ "$M4_SYNC_COUNT" -ge 1 ] && result PASS "M4: child has $M4_SYNC_COUNT photos in sync/ dir" \
                              || result FAIL "M4: no photos in child sync/ dir"

# Verify none ended up in upload/
M4_UPLOAD_COUNT="0"
if [ -d "$CHILD_DIR/photos/upload" ]; then
    M4_UPLOAD_COUNT="$(find "$CHILD_DIR/photos/upload" -name "*.jpg" | wc -l | tr -d '[:space:]')"
fi
[ "$M4_UPLOAD_COUNT" = "0" ] && result PASS "M4: child has 0 photos in upload/ (correct)" \
                               || result FAIL "M4: child has $M4_UPLOAD_COUNT photos in upload/ (should be 0)"

# ============================================================
echo ""
echo "=== M5: Child sync preserves upload_meta ==="
CHILD_PHOTOS=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/photos")
M5_HAS_CHILD=$(echo "$CHILD_PHOTOS" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
child_uploads = [p for p in photos if p.get('uploaded_by') == 'test-child']
print('yes' if child_uploads else 'no')
")
[ "$M5_HAS_CHILD" = "yes" ] && result PASS "M5: child DB has uploaded_by=test-child" \
                               || result FAIL "M5: child DB missing test-child attribution"

# ============================================================
# Delete Tests
# ============================================================
echo ""
echo "=== D1: Delete photo -> file, thumbnail, DB all gone ==="
# Upload a fresh photo to delete
create_test_photo "$TMPDIR_BASE/d1_photo.jpg" "90"
curl -s -F "t=$MASTER_UPLOAD_TOKEN" -F "photos=@$TMPDIR_BASE/d1_photo.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload" >/dev/null
sleep 5

# Get the filename of the newly uploaded photo
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
D1_FILE=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
# Get most recently added admin photo
admin = [p for p in photos if p['uploaded_by'] == 'admin']
print(admin[-1]['name'] if admin else photos[-1]['name'])
")

# Verify file exists before delete
[ -f "$MASTER_DIR/photos/upload/$D1_FILE" ] && result PASS "D1: file exists before delete" \
                                              || result FAIL "D1: file missing before delete"

# Delete via admin API
curl -s -b "$COOKIES_MASTER" -X POST -H "Content-Type: application/json" \
    -d "{\"path\": \"/static/photos/upload/$D1_FILE\"}" \
    "http://127.0.0.1:$MASTER_PORT/admin/delete_photo" >/dev/null

# Check file gone
[ ! -f "$MASTER_DIR/photos/upload/$D1_FILE" ] && result PASS "D1: file removed from disk" \
                                                || result FAIL "D1: file still on disk"

# Check thumbnail gone
[ ! -f "$MASTER_DIR/photos/thumbs/$D1_FILE" ] && result PASS "D1: thumbnail removed" \
                                                || result FAIL "D1: thumbnail still exists"

# Check DB record gone
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
D1_STILL=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
found = [p for p in photos if p['name'] == '$D1_FILE']
print('yes' if found else 'no')
")
[ "$D1_STILL" = "no" ] && result PASS "D1: DB record removed" \
                         || result FAIL "D1: DB record still exists"

# ============================================================
echo ""
echo "=== D2: Delete -> manifest updated immediately ==="
# Upload a photo
create_test_photo "$TMPDIR_BASE/d2_photo.jpg" "70"
curl -s -F "t=$MASTER_UPLOAD_TOKEN" -F "photos=@$TMPDIR_BASE/d2_photo.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload" >/dev/null
sleep 5

# Rebuild manifest
restart_master

MANIFEST_BEFORE=$(curl -s "http://127.0.0.1:$MASTER_PORT/sync/manifest?token=$SYNC_TOKEN")
D2_BEFORE=$(echo "$MANIFEST_BEFORE" | python3 -c "import sys,json; print(json.load(sys.stdin)['photo_count'])")

# Get filename and delete
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
D2_FILE=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
print(photos[-1]['name'] if photos else '')
")

curl -s -b "$COOKIES_MASTER" -X POST -H "Content-Type: application/json" \
    -d "{\"path\": \"/static/photos/upload/$D2_FILE\"}" \
    "http://127.0.0.1:$MASTER_PORT/admin/delete_photo" >/dev/null

# Check manifest WITHOUT restart
MANIFEST_AFTER=$(curl -s "http://127.0.0.1:$MASTER_PORT/sync/manifest?token=$SYNC_TOKEN")
D2_AFTER=$(echo "$MANIFEST_AFTER" | python3 -c "import sys,json; print(json.load(sys.stdin)['photo_count'])")

[ "$D2_AFTER" -lt "$D2_BEFORE" ] && result PASS "D2: manifest updated immediately ($D2_BEFORE -> $D2_AFTER)" \
                                   || result FAIL "D2: manifest not updated ($D2_BEFORE -> $D2_AFTER)"

# ============================================================
echo ""
echo "=== D3: Child deletes synced photo via proxy to master ==="
# Upload a photo as child on master
create_test_photo "$TMPDIR_BASE/d3_photo.jpg" "110"
curl -s -F "t=$SYNC_TOKEN" -F "photos=@$TMPDIR_BASE/d3_photo.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload" >/dev/null
sleep 5

# Rebuild manifest and sync to child
restart_master
curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done

# Get the child-uploaded filename from child's perspective
CHILD_PHOTOS=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/photos")
D3_FILE=$(echo "$CHILD_PHOTOS" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
child_synced = [p for p in photos if p.get('uploaded_by') == 'test-child']
print(child_synced[-1]['name'] if child_synced else '')
")

if [ -n "$D3_FILE" ]; then
    # Delete from child admin (should proxy to master)
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
        -b "$COOKIES_CHILD" -X POST -H "Content-Type: application/json" \
        -d "{\"path\": \"/static/photos/sync/$D3_FILE\"}" \
        "http://127.0.0.1:$CHILD_PORT/admin/delete_photo")
    [ "$HTTP" = "200" ] && result PASS "D3: child delete proxied to master -> $HTTP" \
                         || result FAIL "D3: expected 200, got $HTTP"

    # Check master file is gone
    [ ! -f "$MASTER_DIR/photos/upload/$D3_FILE" ] && result PASS "D3: master file removed" \
                                                    || result FAIL "D3: master file still exists"
else
    result FAIL "D3: no child-uploaded photo found on child to delete"
    result FAIL "D3: (skipped master check)"
fi

# ============================================================
echo ""
echo "=== D4: Delete last photo -> photo count = 0 ==="
# Delete ALL photos on master
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
for p in photos:
    print(p['name'])
" | while read -r fname; do
    # Determine subdir from path
    SUBDIR=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
for p in photos:
    if p['name'] == '$fname':
        # path is like /static/photos/upload/filename.jpg
        parts = p['path'].split('/')
        # Find subdir between 'photos' and filename
        idx = parts.index('photos') if 'photos' in parts else -1
        if idx >= 0 and len(parts) > idx + 2:
            print(parts[idx+1])
        else:
            print('upload')
        break
")
    curl -s -b "$COOKIES_MASTER" -X POST -H "Content-Type: application/json" \
        -d "{\"path\": \"/static/photos/${SUBDIR}/$fname\"}" \
        "http://127.0.0.1:$MASTER_PORT/admin/delete_photo" >/dev/null 2>&1
done

PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
D4_COUNT=$(echo "$PHOTOS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
[ "$D4_COUNT" = "0" ] && result PASS "D4: photo count = 0 after deleting all" \
                        || result FAIL "D4: expected 0 photos, got $D4_COUNT"

# ============================================================
echo ""
echo "=== D5: Delete synced photo -> child removes on next sync ==="
# Upload a photo on master, sync to child, delete on master, re-sync
create_test_photo "$TMPDIR_BASE/d5_photo.jpg" "130"
curl -s -F "t=$MASTER_UPLOAD_TOKEN" -F "photos=@$TMPDIR_BASE/d5_photo.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload" >/dev/null
sleep 5

restart_master

# Sync to child
curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
wait_sync_done

D5_CHILD_BEFORE=$(find "$CHILD_DIR/photos/sync" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')

# Get filename and delete on master
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
D5_FILE=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
print(photos[0]['name'] if photos else '')
")

if [ -n "$D5_FILE" ]; then
    curl -s -b "$COOKIES_MASTER" -X POST -H "Content-Type: application/json" \
        -d "{\"path\": \"/static/photos/upload/$D5_FILE\"}" \
        "http://127.0.0.1:$MASTER_PORT/admin/delete_photo" >/dev/null

    # Re-sync child (manifest is dirty, no restart needed for sync)
    restart_master
    curl -s -b "$COOKIES_CHILD" -X POST "http://127.0.0.1:$CHILD_PORT/admin/sync_now" >/dev/null
    wait_sync_done

    D5_CHILD_AFTER=$(find "$CHILD_DIR/photos/sync" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
    [ "$D5_CHILD_AFTER" -lt "$D5_CHILD_BEFORE" ] \
        && result PASS "D5: child removed deleted photo after re-sync ($D5_CHILD_BEFORE -> $D5_CHILD_AFTER)" \
        || result FAIL "D5: child still has $D5_CHILD_AFTER files (was $D5_CHILD_BEFORE)"
else
    result FAIL "D5: no photo on master to delete"
fi

# ============================================================
# Sync Interval Tests
# ============================================================
echo ""
echo "=== SI1: Change sync interval -> verify DB updated ==="
HTTP=$(curl -s -o "$TMPDIR_BASE/si1_resp.json" -w "%{http_code}" \
    -b "$COOKIES_CHILD" -X POST -H "Content-Type: application/json" \
    -d '{"sync_role": "child", "sync_interval": 300}' \
    "http://127.0.0.1:$CHILD_PORT/admin/sync_config")
[ "$HTTP" = "200" ] && result PASS "SI1: sync_config POST -> $HTTP" \
                     || result FAIL "SI1: expected 200, got $HTTP"

SI1_INTERVAL=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('sync_interval', 0))")
[ "$SI1_INTERVAL" = "300" ] && result PASS "SI1: sync_interval = $SI1_INTERVAL" \
                              || result FAIL "SI1: expected 300, got $SI1_INTERVAL"

# ============================================================
echo ""
echo "=== SI2: Interval persists across restart ==="
# Set to 600, restart, verify
curl -s -b "$COOKIES_CHILD" -X POST -H "Content-Type: application/json" \
    -d '{"sync_role": "child", "sync_interval": 600}' \
    "http://127.0.0.1:$CHILD_PORT/admin/sync_config" >/dev/null

restart_child

SI2_INTERVAL=$(curl -s -b "$COOKIES_CHILD" "http://127.0.0.1:$CHILD_PORT/admin/sync_status" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('sync_interval', 0))")
[ "$SI2_INTERVAL" = "600" ] && result PASS "SI2: interval persisted across restart ($SI2_INTERVAL)" \
                              || result FAIL "SI2: expected 600 after restart, got $SI2_INTERVAL"

# ============================================================
# Error Recovery
# ============================================================
echo ""
echo "=== E3: Invalid photo in batch -> skipped, valid still processed ==="
create_test_photo "$TMPDIR_BASE/e3_valid.jpg" "140"
echo "this is not a photo" > "$TMPDIR_BASE/e3_invalid.jpg"

HTTP=$(curl -s -o "$TMPDIR_BASE/e3_resp.json" -w "%{http_code}" \
    -F "t=$MASTER_UPLOAD_TOKEN" \
    -F "photos=@$TMPDIR_BASE/e3_valid.jpg" \
    -F "photos=@$TMPDIR_BASE/e3_invalid.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload")

E3_RESP=$(cat "$TMPDIR_BASE/e3_resp.json")
E3_COUNT=$(echo "$E3_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))")
E3_SKIPPED=$(echo "$E3_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('skipped',0))")
[ "$E3_COUNT" = "1" ] && result PASS "E3: valid photo accepted (count=$E3_COUNT)" \
                        || result FAIL "E3: expected count=1, got $E3_COUNT"
[ "$E3_SKIPPED" = "1" ] && result PASS "E3: invalid photo skipped (skipped=$E3_SKIPPED)" \
                          || result FAIL "E3: expected skipped=1, got $E3_SKIPPED"

sleep 5

# ============================================================
# DB Consistency Tests
# ============================================================
echo ""
echo "=== DB1: Photo count in DB matches disk after upload ==="
# Upload 3 fresh photos
for i in 1 2 3; do
    create_test_photo "$TMPDIR_BASE/db1_photo_$i.jpg" "$((i * 50))"
done

curl -s -o /dev/null \
    -F "t=$MASTER_UPLOAD_TOKEN" \
    -F "photos=@$TMPDIR_BASE/db1_photo_1.jpg" \
    -F "photos=@$TMPDIR_BASE/db1_photo_2.jpg" \
    -F "photos=@$TMPDIR_BASE/db1_photo_3.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload"

sleep 10

DB1_DISK=$(find "$MASTER_DIR/photos/upload" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
DB1_DB=$(echo "$PHOTOS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")

[ "$DB1_DISK" = "$DB1_DB" ] && result PASS "DB1: disk ($DB1_DISK) matches DB ($DB1_DB)" \
                              || result FAIL "DB1: disk=$DB1_DISK but DB=$DB1_DB"

# ============================================================
echo ""
echo "=== DB2: Photo count matches after delete ==="
# Delete 1 photo
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
DB2_FILE=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
print(photos[0]['name'] if photos else '')
")

if [ -n "$DB2_FILE" ]; then
    curl -s -b "$COOKIES_MASTER" -X POST -H "Content-Type: application/json" \
        -d "{\"path\": \"/static/photos/upload/$DB2_FILE\"}" \
        "http://127.0.0.1:$MASTER_PORT/admin/delete_photo" >/dev/null

    DB2_DISK=$(find "$MASTER_DIR/photos/upload" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
    PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
    DB2_DB=$(echo "$PHOTOS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")

    [ "$DB2_DISK" = "$DB2_DB" ] && result PASS "DB2: disk ($DB2_DISK) matches DB ($DB2_DB) after delete" \
                                  || result FAIL "DB2: disk=$DB2_DISK but DB=$DB2_DB after delete"
else
    result FAIL "DB2: no photo to delete"
fi

# ============================================================
echo ""
echo "=== DB3: Reconcile doesn't overwrite uploaders ==="
# Upload as child token (uploaded_by=test-child)
create_test_photo "$TMPDIR_BASE/db3_photo.jpg" "170"
curl -s -F "t=$SYNC_TOKEN" -F "photos=@$TMPDIR_BASE/db3_photo.jpg" \
    "http://127.0.0.1:$MASTER_PORT/upload" >/dev/null
sleep 5

# Verify it's tagged as test-child before restart
PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
DB3_BEFORE=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
child_photos = [p for p in photos if p['uploaded_by'] == 'test-child']
print(len(child_photos))
")

# Restart master (triggers reconcile_photos)
restart_master

PHOTOS_JSON=$(curl -s -b "$COOKIES_MASTER" "http://127.0.0.1:$MASTER_PORT/admin/photos")
DB3_AFTER=$(echo "$PHOTOS_JSON" | python3 -c "
import sys, json
photos = json.load(sys.stdin)
child_photos = [p for p in photos if p['uploaded_by'] == 'test-child']
print(len(child_photos))
")

[ "$DB3_AFTER" = "$DB3_BEFORE" ] && result PASS "DB3: reconcile preserved test-child uploads ($DB3_AFTER)" \
                                   || result FAIL "DB3: test-child uploads changed ($DB3_BEFORE -> $DB3_AFTER)"

# ============================================================
# Clean up temp files
rm -f "$TMPDIR_BASE"/u*.jpg "$TMPDIR_BASE"/d*.jpg "$TMPDIR_BASE"/e3*.jpg "$TMPDIR_BASE"/db*.jpg "$TMPDIR_BASE"/si*.json "$TMPDIR_BASE"/*_resp.json

echo ""
echo "========================================"
echo "  Results: $PASS/$TOTAL passed, $FAIL failed"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Master log tail:"
    tail -30 "$TMPDIR_BASE/master.log" 2>/dev/null || true
    echo ""
    echo "Child log tail:"
    tail -30 "$TMPDIR_BASE/child.log" 2>/dev/null || true
fi

[ "$FAIL" = "0" ] && exit 0 || exit 1
