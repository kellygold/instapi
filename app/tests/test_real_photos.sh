#!/bin/bash
# test_real_photos.sh - Capture real webcam photos and run them through photo pipelines
# Requires: imagesnap (brew install imagesnap), Python 3 with PIL
set -e

cd "$(dirname "$0")/.."

TEST_DIR=$(mktemp -d)
PHOTOS_DIR="$TEST_DIR/static/photos"
mkdir -p "$PHOTOS_DIR/upload" "$PHOTOS_DIR/thumbs"

cleanup() {
    rm -rf "$TEST_DIR"
}
trap cleanup EXIT

echo "=== Capturing test images with imagesnap ==="

imagesnap -w 1.0 -q "$TEST_DIR/webcam_1.jpg"
imagesnap -w 0.5 -q "$TEST_DIR/webcam_2.jpg"
imagesnap -w 0.5 -q "$TEST_DIR/webcam_3.jpg"

echo "Captured 3 photos"

# Verify they are valid JPEGs
for f in "$TEST_DIR"/webcam_*.jpg; do
    file "$f" | grep -q "JPEG" || { echo "FAIL: $f is not a valid JPEG"; exit 1; }
    echo "  OK: $(basename $f) ($(stat -f%z "$f") bytes)"
done

echo ""
echo "=== Test 1: compute_md5 consistency ==="
python3 -c "
import sys; sys.path.insert(0, '.')
from photo_ops import compute_md5
md5_1 = compute_md5('$TEST_DIR/webcam_1.jpg')
md5_2 = compute_md5('$TEST_DIR/webcam_1.jpg')
assert md5_1 == md5_2, f'MD5 mismatch: {md5_1} != {md5_2}'
assert len(md5_1) == 32, f'Invalid MD5 length: {len(md5_1)}'
print(f'  OK: MD5 consistent ({md5_1})')
"

echo ""
echo "=== Test 2: generate_thumbnail from webcam JPEG ==="
python3 -c "
import sys; sys.path.insert(0, '.')
from photo_ops import generate_thumbnail
from PIL import Image
generate_thumbnail('$TEST_DIR/webcam_1.jpg', '$TEST_DIR/thumb_1.jpg')
img = Image.open('$TEST_DIR/thumb_1.jpg')
w, h = img.size
assert max(w, h) <= 200, f'Thumbnail too large: {w}x{h}'
print(f'  OK: Thumbnail generated ({w}x{h})')
"

echo ""
echo "=== Test 3: walk_photos finds webcam JPEGs ==="
cp "$TEST_DIR"/webcam_*.jpg "$PHOTOS_DIR/upload/"
python3 -c "
import sys; sys.path.insert(0, '.')
from photo_ops import walk_photos
results = list(walk_photos('$PHOTOS_DIR'))
assert len(results) == 3, f'Expected 3 photos, found {len(results)}'
for fname, fpath, subdir in results:
    assert fname.startswith('webcam_'), f'Unexpected filename: {fname}'
    assert subdir == 'upload', f'Unexpected subdir: {subdir}'
print(f'  OK: Found {len(results)} photos in upload/')
"

echo ""
echo "=== Test 4: EXIF rotation handling (real camera data) ==="
python3 -c "
import sys; sys.path.insert(0, '.')
from PIL import Image, ImageOps
img = Image.open('$TEST_DIR/webcam_1.jpg')
img = ImageOps.exif_transpose(img)
img = img.convert('RGB')
img.save('$TEST_DIR/processed_1.jpg', 'JPEG', quality=85)
import os
original_size = os.path.getsize('$TEST_DIR/webcam_1.jpg')
processed_size = os.path.getsize('$TEST_DIR/processed_1.jpg')
print(f'  OK: EXIF transpose + recompress ({original_size} -> {processed_size} bytes)')
"

echo ""
echo "=== Test 5: Full upload flow with real image ==="
python3 -c "
import sys, os; sys.path.insert(0, '.')
os.environ['INSTAPI_DB_PATH'] = '$TEST_DIR/test.db'
os.environ['INSTAPI_PHOTOS_DIR'] = '$PHOTOS_DIR'
import db
db.init_db()
from photo_ops import compute_md5, generate_thumbnail
from PIL import Image, ImageOps

src = '$TEST_DIR/webcam_2.jpg'
dest = '$PHOTOS_DIR/upload/upload_test_0.jpg'

img = Image.open(src)
img = ImageOps.exif_transpose(img)
img = img.convert('RGB')
img.save(dest, 'JPEG', quality=85)

generate_thumbnail(dest, '$PHOTOS_DIR/thumbs/upload_test_0.jpg')

md5 = compute_md5(dest)
size = os.path.getsize(dest)
db.add_photo('upload_test_0.jpg', subdir='upload', uploaded_by='test', size_bytes=size, md5=md5)

photo = db.get_photo('upload_test_0.jpg')
assert photo is not None, 'Photo not in DB'
assert photo['md5'] == md5, 'MD5 mismatch in DB'
assert photo['size_bytes'] == size, 'Size mismatch in DB'

thumb = '$PHOTOS_DIR/thumbs/upload_test_0.jpg'
assert os.path.exists(thumb), 'Thumbnail not created'
print(f'  OK: Full pipeline (size={size}, md5={md5[:8]}...)')
"

echo ""
echo "=== Test 6: DB-backed manifest matches real photos ==="
python3 -c "
import sys, os; sys.path.insert(0, '.')
os.environ['INSTAPI_DB_PATH'] = '$TEST_DIR/test.db'
photos = __import__('db').get_all_photos()
assert len(photos) > 0, 'No photos in DB'
for p in photos:
    assert p['md5'], f'Missing MD5 for {p[\"filename\"]}'
    assert p['size_bytes'] > 0, f'Zero size for {p[\"filename\"]}'
    path = os.path.join('$PHOTOS_DIR', p['subdir'], p['filename']) if p['subdir'] else os.path.join('$PHOTOS_DIR', p['filename'])
    assert os.path.exists(path), f'File missing: {path}'

urls = __import__('db').get_photo_urls()
assert len(urls) > 0, 'No photo URLs from DB'
for url in urls:
    assert url.startswith('/static/photos/'), f'Bad URL format: {url}'
print(f'  OK: All {len(photos)} DB records have valid md5, size, files, and URLs')
"

echo ""
echo "=== All webcam capture tests passed ==="
