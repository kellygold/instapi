import json
import os
import sqlite3
import subprocess
import pytest
from hashlib import sha256
from pathlib import Path

pytestmark = pytest.mark.timeout(5)


REPO_ROOT = Path(__file__).resolve().parents[2]
UPDATE_PHOTOS_SCRIPT = REPO_ROOT / "pi-setup" / "update-photos.sh"


def _source_signature(source_relpath, source_md5):
    return sha256(f"{source_relpath}\0{source_md5}".encode("utf-8")).hexdigest()


def _manifest_entry(export_name, source_relpath, source_md5, uploaded_by="Michael"):
    return {
        "export_name": export_name,
        "source_relpath": source_relpath,
        "source_md5": source_md5,
        "source_signature": _source_signature(source_relpath, source_md5),
        "uploaded_by": uploaded_by,
    }


def _write_large_image(path, marker):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (marker.encode("utf-8") * 4096)[:12000]
    path.write_bytes(b"\xff\xd8\xff\xe0" + payload)


def _write_export_manifest(path, entries):
    path.write_text(json.dumps({"version": 1, "entries": entries}, indent=2, sort_keys=True), encoding="utf-8")


def _write_staging_manifest(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "entries": entries}, indent=2, sort_keys=True), encoding="utf-8")


def _write_settings_db(path, sync_role="child", frame_balance_enabled=True):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("sync_role", json.dumps(sync_role)))
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        ("frame_balance_enabled", json.dumps(frame_balance_enabled)),
    )
    conn.commit()
    conn.close()


def _write_usb_helper(path):
    path.write_text(
        "\n".join([
            "usb_prepare_and_swap() { :; }",
            "usb_watermark() {",
            "  printf '%s\\n' \"$2\" > \"$USB_WATERMARK_LOG\"",
            "}",
            "",
        ]),
        encoding="utf-8",
    )


def _run_update_photos(
    tmp_path,
    manifest_entries,
    export_markers,
    staging_manifest_entries=None,
    staging_files=None,
    photo_markers=None,
    frame_balance_active=True,
):
    frame_export_dir = tmp_path / "frame_export"
    frame_export_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = tmp_path / "usb_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "instapi.db"
    helper_path = tmp_path / "usb-helper.sh"
    log_path = tmp_path / "usb-watermark.log"
    qr_placeholder = tmp_path / "qr-placeholder.jpg"
    qr_placeholder.write_bytes(b"placeholder")
    img_file = tmp_path / "usb_drive.img"
    img_file.write_bytes(b"img")
    mount_point = tmp_path / "mount"
    mount_point.mkdir(parents=True, exist_ok=True)

    _write_usb_helper(helper_path)
    _write_export_manifest(frame_export_dir / "manifest.json", manifest_entries)

    for export_name, marker in export_markers.items():
        _write_large_image(frame_export_dir / export_name, marker)

    if photo_markers:
        for relative_path, marker in photo_markers.items():
            _write_large_image(photos_dir / relative_path, marker)

    if staging_manifest_entries is not None:
        _write_staging_manifest(staging_dir / ".manifest.json", staging_manifest_entries)
    if staging_files:
        for filename, marker in staging_files.items():
            _write_large_image(staging_dir / filename, marker)

    _write_settings_db(db_path, frame_balance_enabled=frame_balance_active)

    env = os.environ.copy()
    env.update({
        "FRAME_EXPORT_DIR": str(frame_export_dir),
        "FRAME_EXPORT_MANIFEST": str(frame_export_dir / "manifest.json"),
        "STAGING": str(staging_dir),
        "STAGING_MANIFEST": str(staging_dir / ".manifest.json"),
        "PHOTOS_DIR": str(photos_dir),
        "QR_PLACEHOLDER": str(qr_placeholder),
        "IMG_FILE": str(img_file),
        "MOUNT_POINT": str(mount_point),
        "INSTAPI_DB_PATH": str(db_path),
        "USB_HELPER_PATH": str(helper_path),
        "USB_WATERMARK_LOG": str(log_path),
    })

    result = subprocess.run(
        ["bash", str(UPDATE_PHOTOS_SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        "result": result,
        "frame_export_dir": frame_export_dir,
        "staging_dir": staging_dir,
        "log_path": log_path,
    }


def test_update_photos_balanced_export_noop_reuses_staged_files(tmp_path):
    entry = _manifest_entry("00000_Michael.jpg", "sync/upload/michael_1.jpg", "md5-a")
    run = _run_update_photos(
        tmp_path,
        manifest_entries=[entry],
        export_markers={"00000_Michael.jpg": "export-a"},
        staging_manifest_entries={"00000_Michael.jpg": entry["source_signature"]},
        staging_files={"00000_Michael.jpg": "staged-a"},
    )

    assert "No new photos to watermark" in run["result"].stdout
    assert not run["log_path"].exists()
    assert (run["staging_dir"] / "00000_Michael.jpg").read_bytes() == (b"\xff\xd8\xff\xe0" + ("staged-a".encode("utf-8") * 4096)[:12000])


def test_update_photos_balanced_export_refreshes_changed_slot(tmp_path):
    old_entry = _manifest_entry("00000_Michael.jpg", "sync/upload/michael_old.jpg", "old-md5")
    new_entry = _manifest_entry("00000_Michael.jpg", "sync/upload/michael_new.jpg", "new-md5")
    run = _run_update_photos(
        tmp_path,
        manifest_entries=[new_entry],
        export_markers={"00000_Michael.jpg": "export-new"},
        staging_manifest_entries={"00000_Michael.jpg": old_entry["source_signature"]},
        staging_files={"00000_Michael.jpg": "staged-old"},
    )

    assert run["log_path"].read_text(encoding="utf-8").strip() == "00000_Michael.jpg"
    assert (run["staging_dir"] / "00000_Michael.jpg").read_bytes() == (b"\xff\xd8\xff\xe0" + ("export-new".encode("utf-8") * 4096)[:12000])

    with open(run["staging_dir"] / ".manifest.json", encoding="utf-8") as fh:
        staging_manifest = json.load(fh)
    assert staging_manifest["entries"] == {"00000_Michael.jpg": new_entry["source_signature"]}


def test_update_photos_balanced_export_only_refreshes_changed_slots(tmp_path):
    entry_a = _manifest_entry("00000_Michael.jpg", "sync/upload/michael_1.jpg", "md5-a")
    old_entry_b = _manifest_entry("00001_Kyle.jpg", "sync/upload/kyle_old.jpg", "md5-b-old", uploaded_by="Kyle")
    new_entry_b = _manifest_entry("00001_Kyle.jpg", "sync/upload/kyle_new.jpg", "md5-b-new", uploaded_by="Kyle")
    entry_c = _manifest_entry("00003_Ana.jpg", "sync/upload/ana_1.jpg", "md5-c", uploaded_by="Ana")

    run = _run_update_photos(
        tmp_path,
        manifest_entries=[entry_a, new_entry_b, entry_c],
        export_markers={
            "00000_Michael.jpg": "export-a",
            "00001_Kyle.jpg": "export-b-new",
            "00003_Ana.jpg": "export-c",
        },
        staging_manifest_entries={
            "00000_Michael.jpg": entry_a["source_signature"],
            "00001_Kyle.jpg": old_entry_b["source_signature"],
            "00002_Stale.jpg": "stale-signature",
        },
        staging_files={
            "00000_Michael.jpg": "staged-a",
            "00001_Kyle.jpg": "staged-b-old",
            "00002_Stale.jpg": "staged-stale",
        },
    )

    refreshed = set(run["log_path"].read_text(encoding="utf-8").split())
    assert refreshed == {"00001_Kyle.jpg", "00003_Ana.jpg"}
    assert (run["staging_dir"] / "00000_Michael.jpg").read_bytes() == (b"\xff\xd8\xff\xe0" + ("staged-a".encode("utf-8") * 4096)[:12000])
    assert not (run["staging_dir"] / "00002_Stale.jpg").exists()

    with open(run["staging_dir"] / ".manifest.json", encoding="utf-8") as fh:
        staging_manifest = json.load(fh)
    assert staging_manifest["entries"] == {
        "00000_Michael.jpg": entry_a["source_signature"],
        "00001_Kyle.jpg": new_entry_b["source_signature"],
        "00003_Ana.jpg": entry_c["source_signature"],
    }


def test_update_photos_ignores_stale_balanced_manifest_when_balance_inactive(tmp_path):
    entry = _manifest_entry("00000_Michael.jpg", "sync/upload/michael_1.jpg", "md5-a")
    run = _run_update_photos(
        tmp_path,
        manifest_entries=[entry],
        export_markers={"00000_Michael.jpg": "export-a"},
        photo_markers={"upload/library.jpg": "library-live"},
        frame_balance_active=False,
    )

    assert run["log_path"].read_text(encoding="utf-8").strip() == "library.jpg"
    assert (run["staging_dir"] / "library.jpg").exists()
    assert not (run["staging_dir"] / "00000_Michael.jpg").exists()


def test_update_photos_balanced_export_symlinks_pass_size_filter(tmp_path):
    """Symlinks in frame_export/ should be staged correctly via stat -L size check.

    Without stat -L, stat reports the symlink path-string length (~50 bytes), which
    trips the <10KB guard and silently skips the file.  With stat -L the real file
    size is measured, the file passes the filter, and its content lands in staging.
    """
    # Real source file lives outside frame_export (simulating static/photos/)
    source_dir = tmp_path / "source_photos"
    source_dir.mkdir()
    source_file = source_dir / "michael_0.jpg"
    _write_large_image(source_file, "real-content")

    # frame_export/ holds a symlink instead of a copy
    frame_export_dir = tmp_path / "frame_export"
    frame_export_dir.mkdir(parents=True)
    symlink_in_export = frame_export_dir / "00000_Michael.jpg"
    symlink_in_export.symlink_to(source_file)

    entry = _manifest_entry("00000_Michael.jpg", "sync/upload/michael_0.jpg", "md5-a")
    _write_export_manifest(frame_export_dir / "manifest.json", [entry])

    # Build the same environment that _run_update_photos would set up
    staging_dir = tmp_path / "usb_staging"
    staging_dir.mkdir(parents=True)
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir(parents=True)
    db_path = tmp_path / "instapi.db"
    helper_path = tmp_path / "usb-helper.sh"
    log_path = tmp_path / "usb-watermark.log"
    qr_placeholder = tmp_path / "qr-placeholder.jpg"
    qr_placeholder.write_bytes(b"placeholder")
    img_file = tmp_path / "usb_drive.img"
    img_file.write_bytes(b"img")
    mount_point = tmp_path / "mount"
    mount_point.mkdir(parents=True)

    _write_usb_helper(helper_path)
    _write_settings_db(db_path, frame_balance_enabled=True)

    env = os.environ.copy()
    env.update({
        "FRAME_EXPORT_DIR": str(frame_export_dir),
        "FRAME_EXPORT_MANIFEST": str(frame_export_dir / "manifest.json"),
        "STAGING": str(staging_dir),
        "STAGING_MANIFEST": str(staging_dir / ".manifest.json"),
        "PHOTOS_DIR": str(photos_dir),
        "QR_PLACEHOLDER": str(qr_placeholder),
        "IMG_FILE": str(img_file),
        "MOUNT_POINT": str(mount_point),
        "INSTAPI_DB_PATH": str(db_path),
        "USB_HELPER_PATH": str(helper_path),
        "USB_WATERMARK_LOG": str(log_path),
    })

    result = subprocess.run(
        ["bash", str(UPDATE_PHOTOS_SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    staged = staging_dir / "00000_Michael.jpg"
    assert staged.exists(), f"symlinked file not staged.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    # cp dereferences the symlink, so staging should hold a real file (not a symlink)
    assert not staged.is_symlink(), "staging should contain a regular file, not a symlink"
    assert staged.read_bytes() == source_file.read_bytes(), "staged content should match the symlink target"
