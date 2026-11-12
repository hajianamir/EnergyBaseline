"""Phase 1 DB fallback matrix, SQLite PRAGMAs, 3 s external probe, and Online Backup restore tests (P1.6, AC-05)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from energybaseline.config import settings
from energybaseline.core import store
from energybaseline.db import backup as bkp
from energybaseline.db import models as M
from energybaseline.db import session as ses

REPO_ROOT = Path(__file__).resolve().parents[2]


def _clear_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("DATABASE_URL", "POSTGRES_URL", "DB_CONNECTION_STRING", "DB_URI", "EB_DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)


def _block_dir_with_readonly_data_file(base_dir: Path) -> None:
    """Create a regular file named ``data`` inside ``base_dir`` so ``ensure_dirs`` fails cross-platform."""
    base_dir.mkdir(parents=True, exist_ok=True)
    blocker = base_dir / "data"
    if blocker.is_dir():
        for child in sorted(blocker.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        blocker.rmdir()
    blocker.write_bytes(b"locked-not-a-directory")


def test_launcher_does_not_override_database_url() -> None:
    launcher_src = (REPO_ROOT / "energybaseline" / "launcher.py").read_text(encoding="utf-8")
    assert "DATABASE_URL" not in launcher_src


def test_db_matrix_no_env_and_whitespace_env_and_pragmas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. No env set
    eb1 = tmp_path / "eb_no_env"
    eb1.mkdir()
    monkeypatch.setenv("EB_HOME", str(eb1))
    _clear_db_env(monkeypatch)
    ses.set_temp_root_for_testing(None)
    ses.reset_engine()

    res1 = ses.init_database()
    assert res1["ok"] is True
    assert res1["writable"] is True
    assert res1["fallback"] is True
    assert res1["rung"] == "eb_home"
    assert res1["banner_level"] == "none"

    pragmas = ses.query_sqlite_pragmas()
    assert pragmas["journal_mode"] == "wal"
    assert pragmas["foreign_keys"] == 1
    assert pragmas["busy_timeout"] == 5000
    assert pragmas["synchronous"] in (1, "normal")
    assert pragmas["temp_store"] in (2, "memory")

    # 2. Whitespace-only env
    eb2 = tmp_path / "eb_ws_env"
    eb2.mkdir()
    monkeypatch.setenv("EB_HOME", str(eb2))
    monkeypatch.setenv("DATABASE_URL", "   \t\n  ")
    ses.reset_engine()

    url, fb = settings.resolve_database_url()
    assert url == settings.DEFAULT_SQLITE_URL
    assert fb is True
    res2 = ses.init_database()
    assert res2["ok"] is True
    assert res2["rung"] == "eb_home"
    ses.reset_engine()


def test_db_matrix_unreachable_postgres_probe_audits_and_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    eb = tmp_path / "eb_unreach"
    eb.mkdir()
    monkeypatch.setenv("EB_HOME", str(eb))
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://user:secret@127.0.0.1:1/energy")
    ses.set_temp_root_for_testing(None)
    ses.reset_engine()

    t0 = time.monotonic()
    res = ses.init_database()
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0
    assert res["ok"] is True
    assert res["writable"] is True
    st = ses.db_status()
    assert st["external_failed"] is True
    assert st["banner_level"] == "warning"
    assert "***" in st["masked_url"] or "sqlite" in st["masked_url"]

    audits = store.audit_entries()
    assert any("external database unreachable" in a["message"].lower() for a in audits)
    ses.reset_engine()


def test_db_matrix_unwritable_app_home_falls_back_to_temp_rung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unwritable_home = tmp_path / "ro_app_home"
    _block_dir_with_readonly_data_file(unwritable_home)

    isolated_temp = tmp_path / "temp_root"
    isolated_temp.mkdir()

    monkeypatch.setenv("EB_HOME", str(unwritable_home))
    _clear_db_env(monkeypatch)
    ses.set_temp_root_for_testing(isolated_temp)
    ses.reset_engine()

    try:
        res = ses.init_database()
        assert res["ok"] is True
        assert res["writable"] is True
        st = ses.db_status()
        assert st["rung"] == "temp"
        assert st["banner_level"] == "warning"
        assert "data may be lost" in st["banner_text"].lower()
        assert isolated_temp.resolve().as_posix() in st["url"]
    finally:
        ses.set_temp_root_for_testing(None)
        ses.reset_engine()


def test_db_matrix_unwritable_app_home_and_temp_falls_back_to_in_memory_red_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unwritable_home = tmp_path / "ro_home_2"
    _block_dir_with_readonly_data_file(unwritable_home)

    # Make temp_root/EnergyBaseline/data impossible to create as a directory as well
    unwritable_temp_root = tmp_path / "ro_temp_root"
    _block_dir_with_readonly_data_file(unwritable_temp_root / "EnergyBaseline")

    monkeypatch.setenv("EB_HOME", str(unwritable_home))
    _clear_db_env(monkeypatch)
    ses.set_temp_root_for_testing(unwritable_temp_root)
    ses.reset_engine()

    try:
        res = ses.init_database()
        assert res["ok"] is True
        assert res["writable"] is True
        st = ses.db_status()
        assert st["rung"] == "memory"
        assert st["url"] == "sqlite:///:memory:"
        assert st["banner_level"] == "critical"
        assert "in memory" in st["banner_text"].lower()

        # Verify tables persist across distinct sessions via StaticPool
        with ses.session_scope() as s:
            s.add(M.Health(note="in-memory-persistence-check"))
        with ses.session_scope() as s:
            notes = [h.note for h in s.execute(select(M.Health)).scalars().all()]
        assert "in-memory-persistence-check" in notes
    finally:
        ses.set_temp_root_for_testing(None)
        ses.reset_engine()


def test_pre_migration_online_backup_and_validated_restore_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eb = tmp_path / "eb_mig_restore"
    eb.mkdir()
    monkeypatch.setenv("EB_HOME", str(eb))
    _clear_db_env(monkeypatch)
    ses.set_temp_root_for_testing(None)
    ses.reset_engine()

    # 1. Initialize clean database and insert precious user record
    init1 = ses.init_database()
    assert init1["ok"] is True
    with ses.session_scope() as s:
        s.add(
            M.Dataset(
                name="precious_plant_2025.csv",
                source_path="/data/precious_plant_2025.csv",
                sha256="a" * 64,
                n_rows=8760,
                n_cols=5,
                schema_json="{}",
            )
        )
    db_file = eb / "data" / "app.db"
    assert db_file.is_file()
    ses.reset_engine()

    # 2. Simulate a destructive broken migration that drops rows and raises mid-flight
    def _destructive_failed_migration() -> None:
        con = sqlite3.connect(str(db_file))
        try:
            con.execute("DELETE FROM datasets")
            con.commit()
        finally:
            con.close()
        raise RuntimeError("Simulated mid-migration DDL failure")

    init2 = ses.init_database(migration_fn=_destructive_failed_migration)
    assert init2["ok"] is True
    st = ses.db_status()
    assert st["migration_restored"] is True
    assert st["last_backup"] is not None

    manifest = bkp.verify_backup_manifest(
        st["last_backup"]["backup_db"],
        st["last_backup"]["manifest_path"],
    )
    assert len(manifest["sha256"]) == 64

    # Verify precious dataset was restored intact from the pre-migration online backup!
    with ses.session_scope() as s:
        names = [d.name for d in s.execute(select(M.Dataset)).scalars().all()]
    assert "precious_plant_2025.csv" in names

    # 3. Verify tampered backup file is rejected by verify_backup_manifest
    tampered_bkp = Path(st["last_backup"]["backup_db"])
    raw = bytearray(tampered_bkp.read_bytes())
    raw[-1] ^= 0xFF
    tampered_bkp.write_bytes(bytes(raw))
    with pytest.raises(bkp.BackupIntegrityError, match="SHA-256 mismatch"):
        bkp.verify_backup_manifest(tampered_bkp, st["last_backup"]["manifest_path"])

    ses.reset_engine()
