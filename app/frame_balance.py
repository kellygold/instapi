import hashlib
import json
import math
import os
import random
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
FRAME_BALANCE_EXPORT_MANIFEST_VERSION = 1


def is_balance_applicable():
    """Return whether child-local frame balance should control playback.

    Frame balance only applies on child frames and only when the feature has
    been enabled in settings.
    """
    return db.get_setting("sync_role") == "child" and db.get_setting(FRAME_BALANCE_ENABLED_KEY, False)


def get_frame_balance_settings():
    """Load the persisted frame-balance toggle and weight map.

    Returns a dict shaped like:
    `{"enabled": True, "weights": {"Michael": 50, "Kelly": 50}}`
    """
    weights = db.get_setting(FRAME_BALANCE_WEIGHTS_KEY, {}) or {}
    enabled = bool(db.get_setting(FRAME_BALANCE_ENABLED_KEY, False))
    return {"enabled": enabled, "weights": weights}


def get_frame_balance_candidates():
    """Return uploaders that currently have photos on this frame.

    Each item includes the uploader label and how many photos they own, which
    is what the admin UI uses to build the frame-balance form.
    """
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
    """Validate and normalize a user-provided uploader percentage map.

    Rules:
    - at least one uploader must be selected
    - values must be integers
    - zero/negative values are discarded
    - remaining values must add up to 100
    - when `candidates` is provided, every uploader must exist on the frame

    Example:
    `{"Michael": "50", "Kelly": 50, "Ana": 0}` becomes
    `(True, {"Michael": 50, "Kelly": 50})`
    """
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
    """Group eligible photos by uploader in newest-first order.

    Only uploaders present in `weights` are included. Each returned photo entry
    carries both its slideshow URL and its on-disk source path so the same
    grouped data can drive playback and exported frame files.
    """
    rows = db.get_db().execute(
        """
        SELECT filename, subdir, uploaded_by, created_at, md5
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
            "md5": row["md5"] or "",
            "url": url,
            "source_path": source_path,
        })
    return {uploader: photos for uploader, photos in grouped.items() if photos}


def _normalize_ratios(weights):
    """Reduce percentage weights to their simplest whole-number ratio.

    Example:
    `{"Michael": 50, "Kelly": 30, "Ana": 20}` becomes
    `{"Michael": 5, "Kelly": 3, "Ana": 2}`.

    This makes it easier to compute how many playlist slots each uploader needs
    before any duplication is introduced.
    """
    values = list(weights.values())
    gcd = values[0]
    for value in values[1:]:
        gcd = math.gcd(gcd, value)
    return {uploader: value // gcd for uploader, value in weights.items()}


def _compute_targets(grouped, weights):
    """Choose per-uploader slot counts that satisfy the requested ratio.

    The target count for each uploader is the smallest multiple of the
    normalized ratio that can cover the uploader's available photos.

    Example:
    if grouped counts are `Michael=6`, `Kelly=4`, `Ana=2` and weights are
    `50/30/20`, the normalized ratio is `5/3/2` and the targets become
    `Michael=10`, `Kelly=6`, `Ana=4`.
    """
    ratios = _normalize_ratios(weights)
    multiplier = max(
        int(math.ceil(len(grouped[uploader]) / ratios[uploader]))
        for uploader in grouped
    )
    return {uploader: ratios[uploader] * multiplier for uploader in grouped}


def _build_playlist_entries(grouped, targets, seed):
    """Build a shuffled playlist that realizes the target uploader counts.

    If an uploader does not have enough unique photos to fill its target count,
    its newest photos are reused in a round-robin loop.

    Example:
    if Ana has 2 photos but needs 4 slots, her two photos will each appear
    twice in the final playlist.
    """
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
    """Hash the current balance inputs so no-op rebuilds can be skipped.

    The signature changes when either:
    - the selected weights change
    - the set/order/timestamps/content of grouped photos change
    """
    signature_payload = {
        "weights": weights,
        "photos": {
            uploader: [
                (photo["filename"], photo["subdir"], photo["created_at"], photo["md5"])
                for photo in photos
            ]
            for uploader, photos in grouped.items()
        }
    }
    payload = json.dumps(signature_payload, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _log_balanced_playlist(weights, grouped, playlist_entries, seed):
    """Print a human-readable summary of the rebuilt balanced playlist.

    Example output:
    `[FRAME BALANCE] playlist counts=Ana=2, Kelly=3, Michael=5 | total=10`

    That makes it easy to confirm, at a glance, whether a saved balance like
    `{"Michael": 50, "Kelly": 30, "Ana": 20}` produced the expected mix.
    """
    summary = ", ".join(
        f"{uploader}={len(grouped.get(uploader, []))} photos @ {weights[uploader]}%"
        for uploader in sorted(weights)
    )
    counts = Counter(entry["uploaded_by"] for entry in playlist_entries)
    realized = ", ".join(
        f"{uploader}={counts.get(uploader, 0)}"
        for uploader in sorted(weights)
    )
    print(f"[FRAME BALANCE] seed={seed} | source={summary}", flush=True)
    print(f"[FRAME BALANCE] playlist counts={realized} | total={len(playlist_entries)}", flush=True)


def get_balanced_playlist():
    """Return the cached balanced playlist URLs from the database."""
    playlist = db.get_setting(FRAME_BALANCE_PLAYLIST_KEY, []) or []
    if not playlist:
        return []
    return playlist


def disable_frame_balance():
    """Disable frame balance while preserving saved weights for later editing."""
    db.set_setting(FRAME_BALANCE_ENABLED_KEY, False)
    clear_balanced_playlist()


def rebuild_balanced_playlist(force=False, progress_cb=None):
    """Recompute and persist the balanced playlist for a child frame.

    This is the core orchestration step. It:
    - validates the saved weights
    - groups photos by uploader
    - skips work when the inputs have not changed
    - computes target slot counts from the requested ratio
    - builds a deterministic shuffled playlist using a saved seed
    - exports the ordered files into `frame_export`

    Returns the slideshow URLs in playback order. If frame balance is disabled
    or invalid, it clears the cached/exported state and returns an empty list.

    progress_cb, if provided, is forwarded to export_balanced_playlist for
    per-file copy progress reporting.
    """
    settings = get_frame_balance_settings()
    if not settings["enabled"] or db.get_setting("sync_role") != "child":
        clear_balanced_playlist()
        return []

    candidates = get_frame_balance_candidates()
    valid, weights = validate_weights(settings["weights"], candidates)
    if not valid:
        disable_frame_balance()
        return []

    grouped = _group_photos_for_balance(weights)
    if set(grouped) != set(weights):
        disable_frame_balance()
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
    export_balanced_playlist(playlist_entries, progress_cb=progress_cb)
    _log_balanced_playlist(weights, grouped, playlist_entries, seed)
    return playlist_urls


def clear_balanced_playlist():
    """Remove all cached frame-balance state and exported files."""
    db.delete_setting(FRAME_BALANCE_PLAYLIST_KEY)
    db.delete_setting(FRAME_BALANCE_SIGNATURE_KEY)
    db.delete_setting(FRAME_BALANCE_INDEX_KEY)
    db.delete_setting(FRAME_BALANCE_SEED_KEY)
    clear_export_dir()


def clear_export_dir():
    """Delete the on-disk export directory if it exists."""
    if os.path.isdir(FRAME_BALANCE_EXPORT_DIR):
        shutil.rmtree(FRAME_BALANCE_EXPORT_DIR, ignore_errors=True)


def _build_source_signature(source_relpath, source_md5):
    """Build a stable signature for staged USB cache reuse."""
    payload = f"{source_relpath}\0{source_md5}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def export_balanced_playlist(playlist_entries, progress_cb=None):
    """Write the ordered balanced playlist to the export directory.

    The export is a flat numbered copy of the selected source photos plus a
    structured `manifest.json` file that records where each export slot came
    from. USB sync uses the manifest to decide which staged files can be
    reused without re-watermarking.

    progress_cb, if provided, is called as progress_cb(current, total) after
    each file is copied so callers can stream per-file progress.
    """
    clear_export_dir()
    os.makedirs(FRAME_BALANCE_EXPORT_DIR, exist_ok=True)
    manifest_entries = []
    total = len(playlist_entries)
    for index, entry in enumerate(playlist_entries):
        ext = os.path.splitext(entry["filename"])[1].lower() or ".jpg"
        export_name = f"{index:05d}_{entry['uploaded_by'].replace(' ', '_')}{ext}"
        export_path = os.path.join(FRAME_BALANCE_EXPORT_DIR, export_name)
        if os.path.exists(entry["source_path"]):
            os.symlink(entry["source_path"], export_path)
            source_relpath = os.path.join(entry["subdir"], entry["filename"]) if entry["subdir"] else entry["filename"]
            source_relpath = source_relpath.replace(os.sep, "/")
            source_md5 = entry["md5"] or ""
            manifest_entries.append({
                "export_name": export_name,
                "source_relpath": source_relpath,
                "source_md5": source_md5,
                "source_signature": _build_source_signature(source_relpath, source_md5),
                "uploaded_by": entry["uploaded_by"],
            })
        if progress_cb:
            progress_cb(index + 1, total)
    with open(os.path.join(FRAME_BALANCE_EXPORT_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "version": FRAME_BALANCE_EXPORT_MANIFEST_VERSION,
            "entries": manifest_entries,
        }, fh, indent=2, sort_keys=True)


def get_export_dir():
    """Return the directory where balanced playlist exports are written."""
    return FRAME_BALANCE_EXPORT_DIR


def get_effective_photo_urls():
    """Return the slideshow photo list that should be used right now.

    When frame balance is active, this returns the balanced playlist.
    Otherwise it falls back to the normal chronological photo URLs from `db.py`.
    """
    if is_balance_applicable():
        playlist = rebuild_balanced_playlist()
        if playlist:
            return playlist
    return db.get_photo_urls()


def get_next_balanced_photos(count):
    """Return the next `count` photos from the balanced playlist and advance.

    The current index is persisted so repeated calls walk through the balanced
    sequence instead of starting over from the beginning each time.
    """
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
    """Refresh or clear frame-balance artifacts after photo changes.

    When balance is active, a photo add/delete may change the candidate pools
    and duplication requirements, so the playlist is refreshed. Otherwise any
    stale exported balance directory is removed.
    """
    if is_balance_applicable():
        rebuild_balanced_playlist(force=False)
    else:
        clear_export_dir()
