"""SQLite Online Backup API pre-migration backup, SHA-256 manifest, and validated restore (P1.6, DB-4/5)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path


class BackupIntegrityError(RuntimeError):
    """Raised when a backup file fails SHA-256 manifest or SQLite integrity verification."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sqlite_integrity_ok(db_path: Path) -> tuple[bool, str]:
    con = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
        verdict = str(row[0]) if row else "empty"
        return verdict.lower() == "ok", verdict
    finally:
        con.close()


def backup_sqlite_online(
    source_db: str | Path,
    backup_dir: str | Path,
    *,
    reason: str = "pre_migration",
) -> dict:
    """Create a consistent snapshot of ``source_db`` via ``sqlite3.Connection.backup`` and emit a JSON manifest."""
    src_path = Path(source_db).resolve()
    if not src_path.is_file():
        raise FileNotFoundError(f"Source SQLite database does not exist: {src_path}")

    out_dir = Path(backup_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    bkp_path = out_dir / f"{src_path.stem}_{reason}_{stamp}.db"
    manifest_path = out_dir / f"{bkp_path.name}.manifest.json"

    src_con = sqlite3.connect(str(src_path), timeout=15.0)
    dst_con = sqlite3.connect(str(bkp_path), timeout=15.0)
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()

    ok, detail = _sqlite_integrity_ok(bkp_path)
    if not ok:
        bkp_path.unlink(missing_ok=True)
        raise BackupIntegrityError(f"Backup failed PRAGMA integrity_check: {detail}")

    digest = _sha256_file(bkp_path)
    size_bytes = bkp_path.stat().st_size
    manifest = {
        "schema_version": "1.0",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reason": reason,
        "source_db": str(src_path),
        "backup_db": str(bkp_path),
        "manifest_path": str(manifest_path),
        "sha256": digest,
        "size_bytes": size_bytes,
        "integrity_check": "ok",
    }
    tmp_manifest = manifest_path.with_suffix(".tmp")
    tmp_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp_manifest.replace(manifest_path)
    return manifest


def verify_backup_manifest(
    backup_path: str | Path,
    manifest_path: str | Path | None = None,
) -> dict:
    """Verify backup file size, SHA-256 digest, and SQLite PRAGMA integrity_check against its manifest."""
    bkp = Path(backup_path).resolve()
    mpath = Path(manifest_path).resolve() if manifest_path else Path(f"{bkp}.manifest.json")

    if not bkp.is_file():
        raise BackupIntegrityError(f"Backup database file is missing: {bkp}")
    if not mpath.is_file():
        raise BackupIntegrityError(f"Backup manifest file is missing: {mpath}")

    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BackupIntegrityError(f"Invalid backup manifest JSON: {exc}") from exc

    expected_sha = str(manifest.get("sha256", "")).lower()
    expected_size = int(manifest.get("size_bytes", -1))
    actual_size = bkp.stat().st_size
    if actual_size != expected_size:
        raise BackupIntegrityError(
            f"Backup size mismatch for {bkp.name}: expected {expected_size}, got {actual_size}"
        )

    actual_sha = _sha256_file(bkp)
    if actual_sha != expected_sha:
        raise BackupIntegrityError(
            f"Backup SHA-256 mismatch for {bkp.name}: expected {expected_sha[:12]}..., got {actual_sha[:12]}..."
        )

    ok, detail = _sqlite_integrity_ok(bkp)
    if not ok:
        raise BackupIntegrityError(f"Backup SQLite integrity_check failed: {detail}")

    return manifest


def restore_sqlite_online(
    backup_path: str | Path,
    target_db: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> dict:
    """Validate ``backup_path`` against its hash manifest and restore into ``target_db`` via Online Backup API."""
    manifest = verify_backup_manifest(backup_path, manifest_path)
    bkp = Path(backup_path).resolve()
    dst = Path(target_db).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(dst) + suffix)
        sidecar.unlink(missing_ok=True)

    bkp_con = sqlite3.connect(str(bkp), timeout=15.0)
    dst_con = sqlite3.connect(str(dst), timeout=15.0)
    try:
        bkp_con.backup(dst_con)
    finally:
        dst_con.close()
        bkp_con.close()

    ok, detail = _sqlite_integrity_ok(dst)
    if not ok:
        raise BackupIntegrityError(f"Restored target DB failed PRAGMA integrity_check: {detail}")

    return {
        "restored": True,
        "target_db": str(dst),
        "backup_db": str(bkp),
        "sha256": manifest["sha256"],
    }
