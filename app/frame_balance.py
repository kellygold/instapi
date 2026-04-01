import hashlib
import json
import math
import os
import random
import shutil
from collections import Counter

import config
import db

FRAME_BALANCE_ENABLED_KEY = "frame_balance_enabled"
FRAME_BALANCE_WEIGHTS_KEY = "frame_balance_weights"
FRAME_BALANCE_INDEX_KEY = "frame_balance_index"
FRAME_BALANCE_SEED_KEY = "frame_balance_seed"
FRAME_BALANCE_SIGNATURE_KEY = "frame_balance_signature"
FRAME_BALANCE_PLAYLIST_KEY = "frame_balance_playlist"
FRAME_BALANCE_EXPORT_DIR = os.path.join(os.path.dirname(__file__), "frame_export")
FRAME_BALANCE_EXPORT_MANIFEST = os.path.join(FRAME_BALANCE_EXPORT_DIR, "manifest.json")


def is_balance_applicable():
    return db.get_setting("sync_role") == "child" and db.get_setting(FRAME_BALANCE_ENABLED_KEY, False)


def get_frame_balance_settings():
    weights = db.get_setting(FRAME_BALANCE_WEIGHTS_KEY, {}) or {}
    enabled = bool(db.get_setting(FRAME_BALANCE_ENABLED_KEY, False))
    return {"enabled": enabled, "weights": weights}


def get_frame_balance_candidates():
    rows = db.get_db().execute(
        """
        SELECT uploaded_by, COUNT(*) AS count
        FROM photos
        WHERE uploaded_by != '' AND uploaded_by IS NOT NULL
        GROUP BY uploaded_by
        ORDER BY uploaded_by COLLATE NOCASE
        """
    ).fetchall()
    return [{"uploaded_by": row["uploaded_by"], "count": row["count"]} for row in rows]


def validate_weights(weights, candidates=None):
    if not isinstance(weights, dict) or not weights:
        return False, "Choose at least one uploader."
    normalized = {}
    for uploader, value in weights.items():
        if not uploader:
            continue
        try:
            percent = int(value)
        except (TypeError, ValueError):
            return False, f"Invalid percentage for {uploader}."
        if percent <= 0:
            continue
        normalized[uploader] = percent
    if not normalized:
        return False, "Choose at least one uploader."
    if sum(normalized.values()) != 100:
        return False, "Percentages must add up to 100."
    if candidates is not None:
        available = {item["uploaded_by"] for item in candidates}
        unknown = [u for u in normalized if u not in available]
        if unknown:
            return False, f"Unknown uploader: {unknown[0]}"
    return True, normalized


def _group_photos_for_balance(weights):
    rows = db.get_db().execute(
        """
        SELECT filename, subdir, uploaded_by, created_at
        FROM photos
        ORDER BY datetime(created_at) DESC, filename ASC
        """
    ).fetchall()
    grouped = {uploader: [] for uploader in weights}
    for row in rows:
        uploader = row["uploaded_by"]
        if uploader not in grouped:
            continue
        if row["subdir"]:
            url = f"/static/photos/{row['subdir']}/{row['filename']}"
            source_path = os.path.join(config.PHOTOS_DIR, row["subdir"], row["filename"])
        else:
            url = f"/static/photos/{row['filename']}"
            source_path = os.path.join(config.PHOTOS_DIR, row["filename"])
        grouped[uploader].append({
            "filename": row["filename"],
            "subdir": row["subdir"],
            "uploaded_by": uploader,
            "created_at": row["created_at"],
            "url": url,
            "source_path": source_path,
        })
    return {uploader: photos for uploader, photos in grouped.items() if photos}


def _normalize_ratios(weights):
    values = list(weights.values())
    gcd = values[0]
    for value in values[1:]:
        gcd = math.gcd(gcd, value)
    return {uploader: value // gcd for uploader, value in weights.items()}


def _compute_targets(grouped, weights):
    ratios = _normalize_ratios(weights)
    multiplier = max(
        int(math.ceil(len(grouped[uploader]) / ratios[uploader]))
        for uploader in grouped
    )
    return {uploader: ratios[uploader] * multiplier for uploader in grouped}


def _build_playlist_entries(grouped, targets, seed):
    per_uploader_entries = {}
    for uploader, photos in grouped.items():
        target_count = targets[uploader]
        photo_count = len(photos)
        entries = []
        for index in range(target_count):
            photo = photos[index % photo_count]
            entries.append(photo)
        per_uploader_entries[uploader] = entries

    uploader_slots = []
    for uploader, count in targets.items():
        uploader_slots.extend([uploader] * count)

    random.Random(seed).shuffle(uploader_slots)
    next_index = Counter()
    playlist = []
    for uploader in uploader_slots:
        photo = per_uploader_entries[uploader][next_index[uploader]]
        next_index[uploader] += 1
        playlist.append(photo)
    return playlist


def _build_signature(weights, grouped):
    signature_payload = {
        "weights": weights,
        "photos": {
            uploader: [
                (photo["filename"], photo["subdir"], photo["created_at"])
                for photo in photos
            ]
            for uploader, photos in grouped.items()
        }
    }
    payload = json.dumps(signature_payload, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_balanced_playlist():
    playlist = db.get_setting(FRAME_BALANCE_PLAYLIST_KEY, []) or []
    if not playlist:
        return []
    return playlist


def rebuild_balanced_playlist(force=False):
    settings = get_frame_balance_settings()
    if not settings["enabled"] or db.get_setting("sync_role") != "child":
        clear_balanced_playlist()
        return []

    candidates = get_frame_balance_candidates()
    valid, weights = validate_weights(settings["weights"], candidates)
    if not valid:
        clear_balanced_playlist()
        return []

    grouped = _group_photos_for_balance(weights)
    if set(grouped) != set(weights):
        clear_balanced_playlist()
        return []

    signature = _build_signature(weights, grouped)
    old_signature = db.get_setting(FRAME_BALANCE_SIGNATURE_KEY)
    existing = get_balanced_playlist()
    if not force and signature == old_signature and existing and os.path.exists(FRAME_BALANCE_EXPORT_MANIFEST):
        return existing

    targets = _compute_targets(grouped, weights)
    seed = db.get_setting(FRAME_BALANCE_SEED_KEY)
    if not seed or force or signature != old_signature:
        seed = random.randint(0, 2 ** 31 - 1)
        db.set_setting(FRAME_BALANCE_SEED_KEY, seed)

    playlist_entries = _build_playlist_entries(grouped, targets, seed)
    playlist_urls = [entry["url"] for entry in playlist_entries]
    db.set_setting(FRAME_BALANCE_PLAYLIST_KEY, playlist_urls)
    db.set_setting(FRAME_BALANCE_SIGNATURE_KEY, signature)
    current_index = db.get_setting(FRAME_BALANCE_INDEX_KEY, 0)
    if current_index >= len(playlist_urls):
        db.set_setting(FRAME_BALANCE_INDEX_KEY, 0)
    export_balanced_playlist(playlist_entries)
    return playlist_urls


def clear_balanced_playlist():
    db.delete_setting(FRAME_BALANCE_PLAYLIST_KEY)
    db.delete_setting(FRAME_BALANCE_SIGNATURE_KEY)
    db.delete_setting(FRAME_BALANCE_INDEX_KEY)
    db.delete_setting(FRAME_BALANCE_SEED_KEY)
    clear_export_dir()


def clear_export_dir():
    if os.path.isdir(FRAME_BALANCE_EXPORT_DIR):
        shutil.rmtree(FRAME_BALANCE_EXPORT_DIR, ignore_errors=True)


def export_balanced_playlist(playlist_entries):
    clear_export_dir()
    os.makedirs(FRAME_BALANCE_EXPORT_DIR, exist_ok=True)
    manifest = []
    for index, entry in enumerate(playlist_entries):
        ext = os.path.splitext(entry["filename"])[1].lower() or ".jpg"
        export_name = f"{index:05d}_{entry['uploaded_by'].replace(' ', '_')}{ext}"
        export_path = os.path.join(FRAME_BALANCE_EXPORT_DIR, export_name)
        if os.path.exists(entry["source_path"]):
            shutil.copy2(entry["source_path"], export_path)
            manifest.append(export_name)
    with open(os.path.join(FRAME_BALANCE_EXPORT_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)


def get_export_dir():
    return FRAME_BALANCE_EXPORT_DIR


def get_effective_photo_urls():
    if is_balance_applicable():
        playlist = rebuild_balanced_playlist()
        if playlist:
            return playlist
    return db.get_photo_urls()


def get_next_balanced_photos(count):
    playlist = rebuild_balanced_playlist()
    if not playlist:
        return []
    total_photos = len(playlist)
    current_index = db.get_setting(FRAME_BALANCE_INDEX_KEY, 0) % total_photos
    next_photos = []
    for _ in range(count):
        next_photos.append(playlist[current_index])
        current_index = (current_index + 1) % total_photos
    db.set_setting(FRAME_BALANCE_INDEX_KEY, current_index)
    return next_photos


def handle_photo_collection_changed():
    if is_balance_applicable():
        rebuild_balanced_playlist(force=False)
    else:
        clear_export_dir()
