"""Centralised configuration + database URL resolver.

This is the ONLY module allowed to read DATABASE_URL / POSTGRES_URL /
DB_CONNECTION_STRING / DB_URI / EB_DATABASE_URL. Every other consumer
(ORM engine, Alembic env.py, GUI, tests, CLI tools) calls ``get_database_url()``
or ``resolve_database_url()``.

Guarantees
* never raises at import time, even when no variable is set;
* falls back deterministically to ``sqlite:///./data/app.db``;
* resolves APP_HOME via ``energybaseline.io.paths.resolve_app_home``;
* logs ``db.url_resolved url=... fallback=...`` once at start-up.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from ..io.paths import PathGuardError, resolve_app_home, resolve_temp_dir

DEFAULT_SQLITE_URL = "sqlite:///./data/app.db"
_ENV_KEYS = ("DATABASE_URL", "POSTGRES_URL", "DB_CONNECTION_STRING", "DB_URI", "EB_DATABASE_URL")
_log = logging.getLogger("energybaseline.config")
_logged_once = False
_runtime_root_override: Path | None = None


def set_runtime_root_override(path: Path | None) -> None:
    """Used by the DB fallback ladder when primary APP_HOME is unwritable (%TEMP% rung)."""
    global _runtime_root_override
    _runtime_root_override = path


def get_base_dir_info() -> tuple[Path, str]:
    """Return ``(app_home_path, source_mode)`` using the path guard."""
    if _runtime_root_override is not None:
        return _runtime_root_override, "temp"
    try:
        return resolve_app_home()
    except PathGuardError as exc:
        _log.warning("config.app_home_guard_rejected error=%s -> falling back to temp dir", exc)
        return resolve_temp_dir("EnergyBaseline"), "temp"


def get_base_dir() -> Path:
    """Application home: EB_HOME, portable.flag folder, %LOCALAPPDATA%\\EnergyBaseline, or XDG."""
    return get_base_dir_info()[0]


def get_resource_dir() -> Path:
    """Folder holding bundled read-only resources (alembic/, assets/, docs/)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parents[2]


def _paths_for(base: Path) -> dict[str, Path]:
    return {
        "base": base,
        "data": base / "data",
        "logs": base / "logs",
        "cache": base / "cache",
        "backups": base / "data" / "backups",
        "models": base / "data" / "models",
        "reports": base / "data" / "reports",
        "uploads": base / "data" / "uploads",
        "runs": base / "data" / "runs",
    }


def get_paths(base: Path | None = None) -> dict[str, Path]:
    return _paths_for(base or get_base_dir())


def ensure_dirs(base: Path | None = None) -> dict[str, Path]:
    target_base = base or get_base_dir()
    paths = _paths_for(target_base)
    try:
        target_base.mkdir(parents=True, exist_ok=True)
        for key, p in paths.items():
            if key != "base":
                p.mkdir(parents=True, exist_ok=True)
        return paths
    except OSError as exc:
        if base is not None:
            raise
        _log.warning("config.ensure_dirs_primary_failed base=%s error=%s -> trying temp dir", target_base, exc)
        try:
            temp_base = resolve_temp_dir("EnergyBaseline")
            temp_paths = _paths_for(temp_base)
            temp_base.mkdir(parents=True, exist_ok=True)
            for key, p in temp_paths.items():
                if key != "base":
                    p.mkdir(parents=True, exist_ok=True)
            return temp_paths
        except OSError:
            return paths


def resolve_database_url() -> tuple[str, bool]:
    """Return ``(url, fallback_used)``. Never raises."""
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
    except Exception as exc:  # pragma: no cover - defensive
        _log.error("db.ensure_dirs_failed error=%s", exc)
    if not _logged_once:
        _logged_once = True
        _log.info("db.url_resolved url=%s fallback=%s", _mask(url), fallback)
    return url, fallback


def get_database_url() -> str:
    """Single accessor for the configured database URL."""
    return resolve_database_url()[0]


def get_engine_url(url: str | None = None, *, base_dir: Path | None = None) -> str:
    """URL usable by SQLAlchemy: relative SQLite paths are anchored at ``base_dir`` or ``get_base_dir()``."""
    url = url or get_database_url()
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
        rel = url[len("sqlite:///") :]
        if not (len(rel) > 1 and rel[1] == ":"):  # Windows drive letter => absolute
            anchor = base_dir or get_base_dir()
            path = (anchor / rel).resolve()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            return "sqlite:///" + path.as_posix()
    return url


def mask_database_url(url: str) -> str:
    return _mask(url)


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
    try:
        fh = logging.handlers.RotatingFileHandler(
            paths["logs"] / "energybaseline.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        _log.warning("config.file_logger_unavailable error=%s", exc)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    if extra_handler:
        extra_handler.setFormatter(fmt)
        root.addHandler(extra_handler)
    root._eb_configured = True  # type: ignore[attr-defined]
