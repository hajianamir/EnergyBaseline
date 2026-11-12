"""Centralised configuration + database URL resolver.

This is the ONLY module allowed to read DATABASE_URL / POSTGRES_URL /
DB_CONNECTION_STRING / DB_URI.  Every other consumer (ORM engine, Alembic
env.py, worker threads, tests, CLI tools) calls ``get_database_url()``.

Guarantees
* never raises at import time, even when no variable is set;
* falls back deterministically to ``sqlite:///./data/app.db``;
* creates ``./data`` (and the other working folders) before any SQLite use;
* logs ``db.url_resolved url=... fallback=...`` once at start-up.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

DEFAULT_SQLITE_URL = "sqlite:///./data/app.db"
_ENV_KEYS = ("DATABASE_URL", "POSTGRES_URL", "DB_CONNECTION_STRING", "DB_URI")
_log = logging.getLogger("energybaseline.config")
_logged_once = False


def get_base_dir() -> Path:
    """Application home: EB_HOME env, the .exe folder when frozen, else the repo root."""
    env = os.environ.get("EB_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def get_resource_dir() -> Path:
    """Folder holding bundled read-only resources (alembic/, templates)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parents[2]


def get_paths() -> dict[str, Path]:
    base = get_base_dir()
    return {
        "base": base,
        "data": base / "data",
        "logs": base / "logs",
        "models": base / "data" / "models",
        "reports": base / "data" / "reports",
        "uploads": base / "data" / "uploads",
        "runs": base / "data" / "runs",
    }


def ensure_dirs() -> dict[str, Path]:
    paths = get_paths()
    for key, p in paths.items():
        if key != "base":
            p.mkdir(parents=True, exist_ok=True)
    return paths


def resolve_database_url() -> tuple[str, bool]:
    """Return ``(url, fallback_used)``.  Never raises."""
    global _logged_once
    url = ""
    for key in _ENV_KEYS:
        val = os.environ.get(key)
        if val is not None and val.strip():
            url = val.strip()
            break
    fallback = not url
    if fallback:
        url = DEFAULT_SQLITE_URL
    try:
        ensure_dirs()
    except Exception as exc:  # pragma: no cover - extremely defensive
        _log.error("db.ensure_dirs_failed error=%s", exc)
    if not _logged_once:
        _logged_once = True
        _log.info("db.url_resolved url=%s fallback=%s", _mask(url), fallback)
    return url, fallback


def get_database_url() -> str:
    """Single accessor for the configured database URL."""
    return resolve_database_url()[0]


def get_engine_url(url: str | None = None) -> str:
    """URL usable by SQLAlchemy: relative SQLite paths are anchored at the app home."""
    url = url or get_database_url()
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
        rel = url[len("sqlite:///"):]
        if not (len(rel) > 1 and rel[1] == ":"):  # Windows drive letter => absolute
            path = (get_base_dir() / rel).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return "sqlite:///" + path.as_posix()
    return url


def _mask(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        if ":" in creds:
            creds = creds.split(":", 1)[0] + ":***"
        return f"{scheme}://{creds}@{host}"
    return url


def setup_logging(extra_handler: logging.Handler | None = None) -> None:
    paths = ensure_dirs()
    root = logging.getLogger("energybaseline")
    if getattr(root, "_eb_configured", False):
        if extra_handler:
            extra_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            root.addHandler(extra_handler)
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        paths["logs"] / "energybaseline.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    if extra_handler:
        extra_handler.setFormatter(fmt)
        root.addHandler(extra_handler)
    root._eb_configured = True  # type: ignore[attr-defined]
