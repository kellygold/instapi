import json
import os
import pytest
from collections import Counter
from hashlib import sha256

pytestmark = pytest.mark.timeout(5)


def _parse_sse(data: bytes) -> list:
    """Parse raw SSE response bytes into a list of event dicts."""
    events = []
    for line in data.decode("utf-8").splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


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
    events = _parse_sse(resp.data)
    done = next(e for e in events if e["step"] == "done")
    data = done["result"]
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


def test_balance_signature_tracks_source_md5_changes(app_client, tmp_path, monkeypatch):
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

    export_calls = []
    original_export = frame_balance.export_balanced_playlist

    def tracking_export(entries, progress_cb=None):
        export_calls.append([entry["md5"] for entry in entries])
        return original_export(entries, progress_cb=progress_cb)

    monkeypatch.setattr(frame_balance, "export_balanced_playlist", tracking_export)
    db.add_photo("michael_0.jpg", subdir="sync/upload", uploaded_by="Michael", md5="michael-md5-updated", created_at="2026-02-01 10:00:00")

    frame_balance.rebuild_balanced_playlist(force=False)

    assert len(export_calls) == 1
    with open(export_manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)
    entries_by_relpath = {entry["source_relpath"]: entry for entry in manifest["entries"]}
    assert entries_by_relpath["sync/upload/michael_0.jpg"]["source_md5"] == "michael-md5-updated"


def test_invalid_balanced_weights_disable_feature_and_fall_back(app_client):
    import db
    import frame_balance

    db.set_setting("sync_role", "child")
    db.set_setting(frame_balance.FRAME_BALANCE_ENABLED_KEY, True)
    db.set_setting(frame_balance.FRAME_BALANCE_WEIGHTS_KEY, {"Michael": 50, "Kyle": 50})

    _add_weighted_photo(db, "Michael", 0)
    _add_weighted_photo(db, "Kyle", 0)
    frame_balance.rebuild_balanced_playlist(force=True)

    db.remove_photo("kyle_0.jpg")
    frame_balance.handle_photo_collection_changed()

    assert db.get_setting(frame_balance.FRAME_BALANCE_ENABLED_KEY) is False
    assert db.get_setting(frame_balance.FRAME_BALANCE_PLAYLIST_KEY) is None

    resp = app_client.get("/get_next_photos?count=1")
    data = resp.get_json()
    assert data == ["/static/photos/sync/upload/michael_0.jpg"]


# ============== SSE streaming route ==============

def test_frame_balance_sse_progress_advances_monotonically(app_client, monkeypatch):
    """SSE events should have non-decreasing progress values ending at 100."""
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
    events = _parse_sse(resp.data)
    progress_values = [e["progress"] for e in events if "progress" in e]
    assert progress_values, "expected at least one progress event"
    assert progress_values == sorted(progress_values), "progress should not go backwards"
    assert progress_values[-1] == 100


def test_frame_balance_sse_error_for_non_child(app_client):
    """Non-child frames should get an error SSE event, not a done event."""
    import db

    db.set_setting("sync_role", "master")

    resp = app_client.post(
        "/admin/frame_balance",
        json={"enabled": True, "weights": {}},
        content_type="application/json"
    )
    events = _parse_sse(resp.data)
    steps = [e["step"] for e in events]
    assert "error" in steps
    assert "done" not in steps


def test_frame_balance_sse_disable_reaches_done(app_client, monkeypatch):
    """Disabling frame balance should still stream a done event with enabled=False."""
    import db
    import routes.admin_routes as ar

    db.set_setting("sync_role", "child")
    _add_weighted_photo(db, "Michael", 0)
    _add_weighted_photo(db, "Kyle", 0)
    monkeypatch.setattr(ar, "get_display_mode", lambda: "hdmi")

    resp = app_client.post(
        "/admin/frame_balance",
        json={"enabled": False, "weights": {}},
        content_type="application/json"
    )
    events = _parse_sse(resp.data)
    done = next((e for e in events if e["step"] == "done"), None)
    assert done is not None
    assert done["result"]["enabled"] is False
    assert done["progress"] == 100


def test_frame_balance_sse_usb_step_emitted_before_done(app_client, monkeypatch):
    """In USB mode, a 'usb' step event must appear before the done event."""
    import db
    import routes.admin_routes as ar

    db.set_setting("sync_role", "child")
    _add_weighted_photo(db, "Michael", 0)
    _add_weighted_photo(db, "Kyle", 0)
    monkeypatch.setattr(ar, "get_display_mode", lambda: "usb")
    monkeypatch.setattr("utils.sync_photos_to_usb", lambda: None)

    resp = app_client.post(
        "/admin/frame_balance",
        json={"enabled": True, "weights": {"Michael": 50, "Kyle": 50}},
        content_type="application/json"
    )
    events = _parse_sse(resp.data)
    steps = [e["step"] for e in events]
    assert "usb" in steps
    assert steps.index("usb") < steps.index("done")


# ============== symlink export ==============

def _make_export_entries(tmp_path, count=3):
    """Build playlist entry dicts backed by real files in tmp_path."""
    entries = []
    for i in range(count):
        src = tmp_path / f"photo_{i}.jpg"
        src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        entries.append({
            "filename": f"photo_{i}.jpg",
            "uploaded_by": "Michael",
            "subdir": "",
            "source_path": str(src),
            "md5": f"md5-{i}",
            "url": f"/static/photos/photo_{i}.jpg",
        })
    return entries


def test_export_creates_symlinks_not_copies(tmp_path, monkeypatch):
    """export_balanced_playlist should create symlinks rather than file copies."""
    import frame_balance

    export_dir = tmp_path / "frame_export"
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_DIR", str(export_dir))
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_MANIFEST", str(export_dir / "manifest.json"))

    entries = _make_export_entries(tmp_path, count=2)
    frame_balance.export_balanced_playlist(entries)

    for i, entry in enumerate(entries):
        export_path = export_dir / f"{i:05d}_Michael.jpg"
        assert export_path.exists(), f"export file {i} not found"
        assert os.path.islink(str(export_path)), f"export file {i} should be a symlink, not a copy"


def test_export_symlinks_target_source_path(tmp_path, monkeypatch):
    """Each symlink in the export dir should point to the original source file."""
    import frame_balance

    export_dir = tmp_path / "frame_export"
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_DIR", str(export_dir))
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_MANIFEST", str(export_dir / "manifest.json"))

    entries = _make_export_entries(tmp_path, count=2)
    frame_balance.export_balanced_playlist(entries)

    for i, entry in enumerate(entries):
        export_path = str(export_dir / f"{i:05d}_Michael.jpg")
        assert os.readlink(export_path) == entry["source_path"]


def test_export_skips_missing_source_without_error(tmp_path, monkeypatch):
    """Entries whose source file is absent should be silently skipped."""
    import frame_balance

    export_dir = tmp_path / "frame_export"
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_DIR", str(export_dir))
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_MANIFEST", str(export_dir / "manifest.json"))

    entries = [{
        "filename": "ghost.jpg",
        "uploaded_by": "Michael",
        "subdir": "",
        "source_path": str(tmp_path / "nonexistent.jpg"),
        "md5": "abc",
        "url": "/static/photos/ghost.jpg",
    }]

    frame_balance.export_balanced_playlist(entries)  # should not raise

    assert not (export_dir / "00000_Michael.jpg").exists()
    with open(export_dir / "manifest.json") as fh:
        manifest = json.load(fh)
    assert manifest["entries"] == []


def test_export_progress_cb_fires_for_every_entry(tmp_path, monkeypatch):
    """progress_cb should be called once per entry regardless of symlink creation."""
    import frame_balance

    export_dir = tmp_path / "frame_export"
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_DIR", str(export_dir))
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_MANIFEST", str(export_dir / "manifest.json"))

    entries = _make_export_entries(tmp_path, count=3)
    calls = []
    frame_balance.export_balanced_playlist(entries, progress_cb=lambda cur, tot: calls.append((cur, tot)))

    assert calls == [(1, 3), (2, 3), (3, 3)]


def test_export_progress_cb_fires_even_for_missing_source(tmp_path, monkeypatch):
    """progress_cb fires for skipped (missing source) entries too."""
    import frame_balance

    export_dir = tmp_path / "frame_export"
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_DIR", str(export_dir))
    monkeypatch.setattr(frame_balance, "FRAME_BALANCE_EXPORT_MANIFEST", str(export_dir / "manifest.json"))

    entries = [
        {
            "filename": "missing.jpg",
            "uploaded_by": "Michael",
            "subdir": "",
            "source_path": str(tmp_path / "does_not_exist.jpg"),
            "md5": "x",
            "url": "/static/photos/missing.jpg",
        },
    ]
    calls = []
    frame_balance.export_balanced_playlist(entries, progress_cb=lambda cur, tot: calls.append((cur, tot)))

    assert calls == [(1, 1)]
