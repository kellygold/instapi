import json
import os
from hashlib import sha256
from collections import Counter


def _write_source_photo(photos_dir, filename, subdir="sync/upload", content=None):
    relative_path = os.path.join(subdir, filename) if subdir else filename
    target_path = os.path.join(photos_dir, relative_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    payload = content or (b"\xff\xd8\xff\xe0" + filename.encode("utf-8") * 64)
    with open(target_path, "wb") as fh:
        fh.write(payload)
    return target_path


def _add_weighted_photo(db, uploader, index, subdir="sync/upload"):
    serial = 100000 - index
    day = ((serial // 86400) % 28) + 1
    hour = (serial // 3600) % 24
    minute = (serial // 60) % 60
    second = serial % 60
    created_at = f"2026-02-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    db.add_photo(
        f"{uploader.lower()}_{index}.jpg",
        subdir=subdir,
        uploaded_by=uploader,
        size_bytes=1234,
        md5=f"{uploader}-{index}",
        created_at=created_at,
    )


def test_frame_balance_settings_round_trip(app_client, monkeypatch):
    import db
    import routes.admin_routes as ar

    db.set_setting("sync_role", "child")
    _add_weighted_photo(db, "Michael", 0)
    _add_weighted_photo(db, "Kyle", 0)
    monkeypatch.setattr(ar, "get_display_mode", lambda: "hdmi")

    resp = app_client.post(
        "/admin/frame_balance",
        json={"enabled": True, "weights": {"Michael": 50, "Kyle": 50}},
        content_type="application/json"
    )
    data = resp.get_json()
    assert data["success"] is True
    assert data["enabled"] is True
    assert data["weights"] == {"Michael": 50, "Kyle": 50}
    assert data["playlist_length"] == 2

    resp = app_client.get("/admin/frame_balance")
    data = resp.get_json()
    assert data["enabled"] is True
    assert data["weights"] == {"Michael": 50, "Kyle": 50}
    assert {item["uploaded_by"] for item in data["candidates"]} == {"Michael", "Kyle"}


def test_get_next_photos_uses_balanced_playlist(app_client):
    import db
    import frame_balance

    db.set_setting("sync_role", "child")
    db.set_setting(frame_balance.FRAME_BALANCE_ENABLED_KEY, True)
    db.set_setting(frame_balance.FRAME_BALANCE_WEIGHTS_KEY, {"Michael": 50, "Kyle": 50})

    for index in range(2):
        _add_weighted_photo(db, "Michael", index)
        _add_weighted_photo(db, "Kyle", index)

    frame_balance.rebuild_balanced_playlist(force=True)

    resp = app_client.get("/get_next_photos?count=4")
    data = resp.get_json()

    assert len(data) == 4
    counts = Counter("Michael" if "michael_" in url else "Kyle" for url in data)
    assert counts["Michael"] == 2
    assert counts["Kyle"] == 2


def test_balanced_playlist_repeats_newest_when_ratio_requires_duplicates(app_client):
    import db
    import frame_balance

    db.set_setting("sync_role", "child")
    db.set_setting(frame_balance.FRAME_BALANCE_ENABLED_KEY, True)
    db.set_setting(frame_balance.FRAME_BALANCE_WEIGHTS_KEY, {"Michael": 50, "Kyle": 50})

    for index in range(371):
        _add_weighted_photo(db, "Michael", index)
    for index in range(481):
        _add_weighted_photo(db, "Kyle", index)

    playlist = frame_balance.rebuild_balanced_playlist(force=True)

    assert len(playlist) == 962

    michael_urls = [url for url in playlist if "michael_" in url]
    kyle_urls = [url for url in playlist if "kyle_" in url]
    assert len(michael_urls) == 481
    assert len(kyle_urls) == 481

    michael_counts = Counter(url.rsplit("/", 1)[-1] for url in michael_urls)
    duplicated = {name for name, count in michael_counts.items() if count == 2}
    expected = {f"michael_{index}.jpg" for index in range(110)}
    assert duplicated == expected


def test_balanced_export_manifest_includes_source_metadata(app_client, tmp_path, monkeypatch):
    import config
    import db
    import frame_balance

    export_dir = tmp_path / "frame_export"
    export_manifest = export_dir / "manifest.json"
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_DIR", str(export_dir))
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_MANIFEST", str(export_manifest))

    db.set_setting("sync_role", "child")
    db.set_setting(frame_balance.FRAME_BALANCE_ENABLED_KEY, True)
    db.set_setting(frame_balance.FRAME_BALANCE_WEIGHTS_KEY, {"Michael": 50, "Kyle": 50})

    _write_source_photo(config.PHOTOS_DIR, "michael_0.jpg")
    _write_source_photo(config.PHOTOS_DIR, "kyle_0.jpg")
    db.add_photo("michael_0.jpg", subdir="sync/upload", uploaded_by="Michael", md5="michael-md5", created_at="2026-02-01 10:00:00")
    db.add_photo("kyle_0.jpg", subdir="sync/upload", uploaded_by="Kyle", md5="kyle-md5", created_at="2026-02-01 10:00:01")

    frame_balance.rebuild_balanced_playlist(force=True)

    with open(export_manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)

    assert manifest["version"] == frame_balance.FRAME_BALANCE_EXPORT_MANIFEST_VERSION
    assert len(manifest["entries"]) == 2

    entries_by_relpath = {entry["source_relpath"]: entry for entry in manifest["entries"]}
    assert set(entries_by_relpath) == {"sync/upload/michael_0.jpg", "sync/upload/kyle_0.jpg"}

    michael_entry = entries_by_relpath["sync/upload/michael_0.jpg"]
    assert michael_entry["uploaded_by"] == "Michael"
    assert michael_entry["source_md5"] == "michael-md5"
    assert michael_entry["export_name"].startswith("000")
    assert michael_entry["source_signature"] == sha256("sync/upload/michael_0.jpg\0michael-md5".encode("utf-8")).hexdigest()
