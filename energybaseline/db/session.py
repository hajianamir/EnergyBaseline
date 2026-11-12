"""Engine / session factory with zero-fail boot, migrations and start-up self-test."""
from __future__ import annotations

import contextlib
import json
import logging
import threading
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import ensure_dirs, get_engine_url, resolve_database_url
from .models import Base, Health

log = logging.getLogger("energybaseline.db")
_lock = threading.Lock()
_engine: Engine | None = None
_state: dict = {"url": "", "fallback": True, "external_failed": False}


def _make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return create_engine(url, **kwargs)


def get_engine() -> Engine:
    """Return the process-wide engine. Never raises: falls back to SQLite."""
    global _engine
    with _lock:
        if _engine is not None:
            return _engine
        ensure_dirs()
        url, fallback = resolve_database_url()
        eng_url = get_engine_url(url)
        try:
            eng = _make_engine(eng_url)
            if not eng_url.startswith("sqlite"):
                with eng.connect() as c:  # reachability probe (short)
                    c.execute(text("SELECT 1"))
        except Exception as exc:
            log.warning("db.external_unreachable url=%s error=%s -> falling back to SQLite", url, exc)
            from ..config import DEFAULT_SQLITE_URL
            eng_url = get_engine_url(DEFAULT_SQLITE_URL)
            eng = _make_engine(eng_url)
            fallback = True
            _state["external_failed"] = True
        if eng_url.startswith("sqlite"):
            from sqlalchemy import event

            @event.listens_for(eng, "connect")
            def _pragma(dbapi_con, _rec):  # pragma: no cover
                cur = dbapi_con.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        _state.update(url=eng_url, fallback=fallback)
        _engine = eng
        return eng


def reset_engine() -> None:
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
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


def self_test() -> dict:
    """Write a sentinel row into ``health`` and read it back."""
    result = {"ok": False, "url": _state["url"], "fallback": _state["fallback"], "writable": False}
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


def init_database() -> dict:
    """Apply migrations (falls back to create_all) and run the self-test. Never raises."""
    eng = get_engine()
    try:
        from .migrate import upgrade_head
        upgrade_head()
    except Exception as exc:
        log.warning("db.migrate_failed error=%s -> using metadata.create_all", exc)
        try:
            Base.metadata.create_all(eng)
        except Exception as exc2:  # pragma: no cover
            log.error("db.create_all_failed error=%s", exc2)
    return self_test()


def db_status() -> dict:
    get_engine()
    return dict(_state)
