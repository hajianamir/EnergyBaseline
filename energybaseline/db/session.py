"""Engine / session factory with 3 s external probe, full SQLite fallback ladder, pragmas, and online backup."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ..config import (
    DEFAULT_SQLITE_URL,
    ensure_dirs,
    get_base_dir_info,
    get_engine_url,
    mask_database_url,
    resolve_database_url,
    set_runtime_root_override,
)
from ..io.paths import PathGuardError, resolve_temp_dir
from .backup import backup_sqlite_online, restore_sqlite_online
from .models import AuditLog, Base, Health

log = logging.getLogger("energybaseline.db")
EXTERNAL_PROBE_TIMEOUT_SECONDS = 3.0

_lock = threading.RLock()
_engine: Engine | None = None
_temp_root_override: Path | None = None
_pending_audits: list[tuple[str, str, str, dict]] = []
_state: dict = {
    "url": "",
    "masked_url": "",
    "rung": "uninitialized",
    "fallback": True,
    "external_failed": False,
    "external_error": "",
    "external_probe_ms": None,
    "banner_level": "none",
    "banner_text": "",
    "pragmas": {},
    "last_backup": None,
    "migration_restored": False,
    "migration_error": "",
}


def _assert_not_worker() -> None:
    if os.environ.get("_EB_IN_WORKER") == "1":
        raise RuntimeError("SHL-5 violation: worker process must never open SQLite or ORM sessions.")


def set_temp_root_for_testing(temp_root: Path | None) -> None:
    """Test hook allowing fallback matrix tests to supply an isolated or read-only %TEMP% directory."""
    global _temp_root_override
    _temp_root_override = temp_root


def _attach_sqlite_pragmas(eng: Engine, *, is_memory: bool) -> None:
    @event.listens_for(eng, "connect")
    def _pragma(dbapi_con, _rec) -> None:
        cur = dbapi_con.cursor()
        try:
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA temp_store=MEMORY")
            if not is_memory:
                cur.execute("PRAGMA journal_mode=WAL")
        finally:
            cur.close()


def _make_sqlite_engine(url: str, *, is_memory: bool = False) -> Engine:
    if is_memory:
        eng = create_engine(
            "sqlite:///:memory:",
            future=True,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        eng = create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
    _attach_sqlite_pragmas(eng, is_memory=is_memory)
    return eng


def _verify_sqlite_writable(eng: Engine) -> None:
    with eng.begin() as conn:
        uv = int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)
        conn.exec_driver_sql(f"PRAGMA user_version = {uv}")


def query_sqlite_pragmas(eng: Engine | None = None) -> dict:
    """Read live SQLite PRAGMA settings from ``eng`` (or the active engine)."""
    target = eng or get_engine()
    if not str(target.url).startswith("sqlite"):
        return {}
    out: dict = {}
    with target.connect() as conn:
        for name in ("journal_mode", "synchronous", "foreign_keys", "busy_timeout", "temp_store"):
            val = conn.exec_driver_sql(f"PRAGMA {name}").scalar()
            out[name] = val.lower() if isinstance(val, str) else val
    return out


def _probe_external_engine(eng_url: str, timeout_s: float = EXTERNAL_PROBE_TIMEOUT_SECONDS) -> tuple[Engine | None, str, float]:
    holder: dict = {"eng": None, "err": ""}
    t0 = time.monotonic()

    def _attempt() -> None:
        eng: Engine | None = None
        try:
            connect_args: dict = {}
            lower = eng_url.lower()
            if lower.startswith(("postgresql", "mysql", "mariadb")):
                connect_args["connect_timeout"] = max(1, int(timeout_s))
            elif lower.startswith("mssql"):
                connect_args["timeout"] = max(1, int(timeout_s))
            eng = create_engine(eng_url, future=True, connect_args=connect_args, pool_pre_ping=True)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            holder["eng"] = eng
        except Exception as exc:
            if eng is not None:
                with contextlib.suppress(Exception):
                    eng.dispose()
            holder["err"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=_attempt, name="eb-ext-db-probe", daemon=True)
    worker.start()
    worker.join(timeout=timeout_s)
    elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
    if worker.is_alive():
        return None, f"TimeoutError: external database probe exceeded {timeout_s:.1f}s", elapsed_ms
    if holder["eng"] is not None:
        return holder["eng"], "", elapsed_ms
    return None, holder["err"] or "Unknown external database error", elapsed_ms


def _sqlite_file_from_url(eng_url: str) -> Path | None:
    if not eng_url.startswith("sqlite:///") or ":memory:" in eng_url:
        return None
    raw = eng_url[len("sqlite:///") :]
    return Path(raw)


def get_engine() -> Engine:
    """Return the process-wide engine following the Phase 1 fallback ladder. Never raises in the UI process."""
    _assert_not_worker()
    global _engine
    with _lock:
        if _engine is not None:
            return _engine

        set_runtime_root_override(None)
        primary_base, primary_mode = get_base_dir_info()
        raw_url, env_was_empty = resolve_database_url()

        external_failed = False
        external_error = ""
        probe_ms: float | None = None

        # 1. External database probe (bounded to 3 s)
        if not raw_url.startswith("sqlite"):
            ext_eng, ext_err, probe_ms = _probe_external_engine(raw_url, EXTERNAL_PROBE_TIMEOUT_SECONDS)
            if ext_eng is not None:
                _state.update(
                    url=raw_url,
                    masked_url=mask_database_url(raw_url),
                    rung="external",
                    fallback=False,
                    external_failed=False,
                    external_error="",
                    external_probe_ms=probe_ms,
                    banner_level="none",
                    banner_text="",
                    pragmas={},
                )
                _engine = ext_eng
                return ext_eng

            external_failed = True
            external_error = ext_err
            log.warning(
                "db.external_unreachable url=%s probe_ms=%s error=%s -> falling back to SQLite ladder",
                mask_database_url(raw_url),
                probe_ms,
                ext_err,
            )
            _pending_audits.append(
                (
                    "WARNING",
                    "db",
                    "Configured external database unreachable during 3 s probe; degraded to local SQLite.",
                    {
                        "configured_url": mask_database_url(raw_url),
                        "probe_ms": probe_ms,
                        "error": ext_err,
                    },
                )
            )
            sqlite_candidate_url = DEFAULT_SQLITE_URL
        else:
            sqlite_candidate_url = raw_url

        # 2. Primary SQLite rung (%LOCALAPPDATA%, portable.flag, EB_HOME, or explicit sqlite:/// URL)
        if ":memory:" not in sqlite_candidate_url:
            try:
                ensure_dirs(primary_base)
                eng_url = get_engine_url(sqlite_candidate_url, base_dir=primary_base)
                eng = _make_sqlite_engine(eng_url, is_memory=False)
                _verify_sqlite_writable(eng)
                pragmas = query_sqlite_pragmas(eng)
                banner_level = "warning" if external_failed else "none"
                banner_text = (
                    f"External database unreachable ({probe_ms:.0f} ms probe); using local SQLite fallback."
                    if external_failed
                    else ""
                )
                _state.update(
                    url=eng_url,
                    masked_url=mask_database_url(eng_url),
                    rung=primary_mode,
                    fallback=env_was_empty or external_failed,
                    external_failed=external_failed,
                    external_error=external_error,
                    external_probe_ms=probe_ms,
                    banner_level=banner_level,
                    banner_text=banner_text,
                    pragmas=pragmas,
                )
                _engine = eng
                return eng
            except Exception as primary_exc:
                log.warning(
                    "db.primary_sqlite_unavailable base=%s error=%s -> trying %%TEMP%% rung",
                    primary_base,
                    primary_exc,
                )

            # 3. %TEMP% SQLite rung ("data may be lost" warning banner)
            try:
                temp_base = resolve_temp_dir("EnergyBaseline", temp_root=_temp_root_override)
                ensure_dirs(temp_base)
                set_runtime_root_override(temp_base)
                temp_url = get_engine_url(DEFAULT_SQLITE_URL, base_dir=temp_base)
                temp_eng = _make_sqlite_engine(temp_url, is_memory=False)
                _verify_sqlite_writable(temp_eng)
                pragmas = query_sqlite_pragmas(temp_eng)
                banner_msg = (
                    "Primary application folder is not writable. Database is running in temporary storage "
                    f"({temp_base}) — data may be lost when temporary files are cleaned."
                )
                _pending_audits.append(
                    (
                        "WARNING",
                        "db",
                        "Database fell back to %TEMP% rung; data may be lost.",
                        {"temp_base": str(temp_base)},
                    )
                )
                _state.update(
                    url=temp_url,
                    masked_url=temp_url,
                    rung="temp",
                    fallback=True,
                    external_failed=external_failed,
                    external_error=external_error,
                    external_probe_ms=probe_ms,
                    banner_level="warning",
                    banner_text=banner_msg,
                    pragmas=pragmas,
                )
                _engine = temp_eng
                return temp_eng
            except (OSError, PathGuardError, Exception) as temp_exc:
                set_runtime_root_override(None)
                log.error(
                    "db.temp_sqlite_unavailable error=%s -> falling back to in-memory SQLite",
                    temp_exc,
                )

        # 4. In-memory SQLite rung (red critical banner)
        mem_eng = _make_sqlite_engine("sqlite:///:memory:", is_memory=True)
        _verify_sqlite_writable(mem_eng)
        pragmas = query_sqlite_pragmas(mem_eng)
        red_msg = (
            "CRITICAL: Local disk storage is unavailable. Database is running IN MEMORY ONLY — "
            "all data may be lost as soon as EnergyBaseline closes."
        )
        _pending_audits.append(
            (
                "ERROR",
                "db",
                "Database fell back to in-memory SQLite rung.",
                {"external_failed": external_failed},
            )
        )
        _state.update(
            url="sqlite:///:memory:",
            masked_url="sqlite:///:memory:",
            rung="memory",
            fallback=True,
            external_failed=external_failed,
            external_error=external_error,
            external_probe_ms=probe_ms,
            banner_level="critical",
            banner_text=red_msg,
            pragmas=pragmas,
        )
        _engine = mem_eng
        return mem_eng


def reset_engine() -> None:
    global _engine
    with _lock:
        if _engine is not None:
            with contextlib.suppress(Exception):
                _engine.dispose()
        _engine = None
        set_runtime_root_override(None)


def checkpoint_and_close() -> dict:
    """Flush SQLite WAL checkpoint and dispose engine handles during clean shutdown (P1.2)."""
    global _engine
    info = {"wal_checkpointed": False, "disposed": False}
    with _lock:
        if _engine is not None:
            url_str = str(_engine.url)
            if url_str.startswith("sqlite") and ":memory:" not in url_str:
                try:
                    with _engine.connect() as conn:
                        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
                    info["wal_checkpointed"] = True
                except Exception as exc:
                    log.warning("db.wal_checkpoint_failed error=%s", exc)
            with contextlib.suppress(Exception):
                _engine.dispose()
            _engine = None
            info["disposed"] = True
        set_runtime_root_override(None)
    return info


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    _assert_not_worker()
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _flush_pending_audits() -> None:
    global _pending_audits
    if not _pending_audits:
        return
    queued = list(_pending_audits)
    _pending_audits = []
    try:
        with session_scope() as s:
            for level, cat, msg, detail in queued:
                s.add(
                    AuditLog(
                        level=level,
                        category=cat,
                        message=msg,
                        detail_json=json.dumps(detail, default=str),
                    )
                )
    except Exception as exc:
        log.warning("db.pending_audit_flush_failed error=%s", exc)


def self_test() -> dict:
    """Write a sentinel row into ``health`` and read it back."""
    _assert_not_worker()
    result = {
        "ok": False,
        "url": _state["url"],
        "masked_url": _state["masked_url"],
        "rung": _state["rung"],
        "fallback": _state["fallback"],
        "writable": False,
        "banner_level": _state["banner_level"],
        "banner_text": _state["banner_text"],
    }
    try:
        with session_scope() as s:
            row = Health(note="startup-selftest")
            s.add(row)
            s.flush()
            rid = row.id
        with session_scope() as s:
            got = s.get(Health, rid)
            result["writable"] = got is not None
            result["ok"] = got is not None
    except Exception as exc:
        result["error"] = str(exc)
    log.info("db.selftest %s", json.dumps(result))
    return result


def init_database(*, migration_fn: Callable[[], None] | None = None) -> dict:
    """Create pre-migration online backup, apply migrations (restoring on failure), and run self-test."""
    _assert_not_worker()
    eng = get_engine()
    _state["migration_restored"] = False
    _state["migration_error"] = ""

    if _state["rung"] == "memory":
        Base.metadata.create_all(eng)
        _flush_pending_audits()
        return self_test()

    db_file = _sqlite_file_from_url(_state["url"])
    has_existing_tables = False
    if db_file is not None and db_file.is_file() and db_file.stat().st_size > 0:
        with contextlib.suppress(Exception):
            has_existing_tables = len(inspect(eng).get_table_names()) > 0

    backup_manifest: dict | None = None
    if db_file is not None and has_existing_tables:
        try:
            backup_dir = db_file.parent / "backups"
            backup_manifest = backup_sqlite_online(db_file, backup_dir, reason="pre_migration")
            _state["last_backup"] = backup_manifest
        except Exception as bkp_exc:
            log.warning("db.pre_migration_backup_failed error=%s", bkp_exc)

    run_mig = migration_fn
    if run_mig is None:
        from .migrate import upgrade_head

        target_url = _state["url"]
        run_mig = lambda: upgrade_head(url=target_url)  # noqa: E731

    try:
        run_mig()
    except Exception as mig_exc:
        _state["migration_error"] = f"{type(mig_exc).__name__}: {mig_exc}"
        log.warning("db.migrate_failed error=%s", mig_exc)
        if backup_manifest is not None and db_file is not None:
            try:
                saved_rung_root = db_file.parent.parent if _state["rung"] == "temp" else None
                reset_engine()
                if saved_rung_root is not None:
                    set_runtime_root_override(saved_rung_root)
                restore_sqlite_online(
                    backup_manifest["backup_db"],
                    db_file,
                    manifest_path=backup_manifest["manifest_path"],
                )
                eng = get_engine()
                _state["migration_restored"] = True
                _pending_audits.append(
                    (
                        "WARNING",
                        "db",
                        "Schema migration failed; restored validated pre-migration backup via Online Backup API.",
                        {
                            "backup_db": backup_manifest["backup_db"],
                            "sha256": backup_manifest["sha256"],
                            "error": _state["migration_error"],
                        },
                    )
                )
            except Exception as restore_exc:
                log.error("db.backup_restore_failed error=%s", restore_exc)
        else:
            try:
                Base.metadata.create_all(eng)
            except Exception as exc2:  # pragma: no cover
                log.error("db.create_all_failed error=%s", exc2)

    _flush_pending_audits()
    return self_test()


def db_status() -> dict:
    get_engine()
    snap = dict(_state)
    if str(snap.get("url", "")).startswith("sqlite"):
        with contextlib.suppress(Exception):
            snap["pragmas"] = query_sqlite_pragmas(_engine)
    return snap
