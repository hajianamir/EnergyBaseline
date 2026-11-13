"""Path guard enforcing normalized, absolute, symlink-free, non-UNC paths inside allowed roots."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath


class PathGuardError(ValueError):
    """Raised when a path fails traversal, UNC, symlink, or allowed-root validation."""


def _is_unc_string(raw_str: str) -> bool:
    if raw_str.startswith(("\\\\", "//")):
        return True
    win_drive = PureWindowsPath(raw_str).drive
    if win_drive.startswith("\\\\"):
        return True
    if raw_str.upper().startswith("\\\\?\\UNC\\"):
        return True
    return False


def _has_dotdot_segment(raw_str: str) -> bool:
    posix_parts = PurePosixPath(raw_str.replace("\\", "/")).parts
    win_parts = PureWindowsPath(raw_str).parts
    return ".." in posix_parts or ".." in win_parts


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return True
        file_attrs = getattr(st, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
        if file_attrs & reparse_flag:
            return True
    except OSError:
        return False
    return False


def _assert_no_raw_hazards(candidate: str | Path) -> Path:
    if candidate is None:
        raise PathGuardError("Path must not be None.")
    raw_str = str(candidate).strip()
    if not raw_str:
        raise PathGuardError("Path must not be empty.")
    if "\x00" in raw_str:
        raise PathGuardError("Path contains NUL byte.")
    if _is_unc_string(raw_str):
        raise PathGuardError(f"UNC network paths are forbidden: {raw_str!r}")
    if _has_dotdot_segment(raw_str):
        raise PathGuardError(f"Parent-directory traversal ('..') is forbidden: {raw_str!r}")
    return Path(raw_str)


def _check_symlinks_under_root(raw_abs: Path, allowed_root: Path) -> None:
    if _is_link_or_reparse(raw_abs):
        raise PathGuardError(f"Symbolic links and reparse points are forbidden: {raw_abs}")
    # Check every component from raw_abs up to allowed_root before and after resolution
    cur = raw_abs
    while True:
        if _is_link_or_reparse(cur):
            raise PathGuardError(f"Symbolic link component forbidden in path: {cur}")
        if cur == allowed_root or cur.parent == cur:
            break
        cur = cur.parent


def validate_path(
    candidate: str | Path,
    *,
    allowed_roots: Sequence[str | Path],
    must_exist: bool = False,
) -> Path:
    """Validate ``candidate`` and return a normalized, absolute ``Path`` inside ``allowed_roots``.

    Rejects ``..`` segments, UNC shares, symbolic links / junctions, and paths outside ``allowed_roots``.
    """
    if not allowed_roots:
        raise PathGuardError("allowed_roots must not be empty.")

    raw_path = _assert_no_raw_hazards(candidate)
    if _is_link_or_reparse(raw_path):
        raise PathGuardError(f"Symbolic link target is forbidden: {raw_path}")

    abs_unres = Path(os.path.abspath(str(raw_path)))
    if _is_link_or_reparse(abs_unres):
        raise PathGuardError(f"Symbolic link path is forbidden: {abs_unres}")

    resolved_roots: list[Path] = []
    for root in allowed_roots:
        r_raw = _assert_no_raw_hazards(root)
        if _is_link_or_reparse(r_raw):
            raise PathGuardError(f"Allowed root cannot be a symbolic link: {r_raw}")
        resolved_roots.append(r_raw.resolve())

    # Check raw ancestry before resolve() collapses intermediate symlinks
    cur = abs_unres
    while True:
        if any(cur == rr for rr in resolved_roots):
            break
        if _is_link_or_reparse(cur):
            raise PathGuardError(f"Symbolic link component forbidden in path: {cur}")
        if cur.parent == cur:
            break
        cur = cur.parent

    resolved = abs_unres.resolve()
    if _is_unc_string(str(resolved)):
        raise PathGuardError(f"Resolved path points to a forbidden UNC share: {resolved}")

    matched_root: Path | None = None
    for rr in resolved_roots:
        if resolved == rr or resolved.is_relative_to(rr):
            matched_root = rr
            break

    if matched_root is None:
        roots_txt = ", ".join(str(r) for r in resolved_roots)
        raise PathGuardError(f"Path '{resolved}' is outside allowed roots [{roots_txt}].")

    _check_symlinks_under_root(resolved, matched_root)

    if must_exist and not resolved.exists():
        raise PathGuardError(f"Path does not exist: {resolved}")

    return resolved


def resolve_app_home(
    *,
    env: Mapping[str, str] | None = None,
    frozen: bool | None = None,
    executable: str | Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, str]:
    """Resolve ``(app_home_path, source_kind)`` per Phase 1 specification.

    Order:
    1. Explicit ``EB_HOME`` environment override (validated against ``..``, UNC, symlinks).
    2. ``portable.flag`` beside the frozen executable (or in ``repo_root`` when present).
    3. ``%LOCALAPPDATA%\\EnergyBaseline`` on Windows (or when ``LOCALAPPDATA`` is set),
       otherwise ``$XDG_DATA_HOME/EnergyBaseline`` or ``~/.local/share/EnergyBaseline``.
    """
    from ..config.runtime_env import get_process_environ

    environ = get_process_environ() if env is None else env
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe_path = Path(sys.executable if executable is None else executable)
    src_root = (Path(__file__).resolve().parents[2] if repo_root is None else repo_root).resolve()

    eb_home_raw = environ.get("EB_HOME", "").strip()
    if eb_home_raw:
        p = _assert_no_raw_hazards(eb_home_raw).expanduser()
        if _is_link_or_reparse(p):
            raise PathGuardError(f"EB_HOME must not be a symbolic link: {p}")
        resolved = p.resolve()
        if _is_unc_string(str(resolved)):
            raise PathGuardError(f"EB_HOME must not resolve to a UNC path: {resolved}")
        return resolved, "eb_home"

    exe_dir = exe_path.resolve().parent if is_frozen else src_root
    portable_marker = exe_dir / "portable.flag"
    if portable_marker.is_file() and not _is_link_or_reparse(portable_marker):
        if _is_unc_string(str(exe_dir)):
            raise PathGuardError(f"Portable directory cannot reside on a UNC path: {exe_dir}")
        return exe_dir, "portable"

    local_app_data = environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        lad_raw = _assert_no_raw_hazards(local_app_data).expanduser()
        if _is_link_or_reparse(lad_raw):
            raise PathGuardError(f"LOCALAPPDATA must not be a symbolic link: {lad_raw}")
        lad_resolved = lad_raw.resolve()
        target = validate_path(lad_resolved / "EnergyBaseline", allowed_roots=[lad_resolved])
        return target, "localappdata"

    if sys.platform == "win32":
        home = Path.home().resolve()
        base = home / "AppData" / "Local"
        target = validate_path(base / "EnergyBaseline", allowed_roots=[home])
        return target, "localappdata"

    xdg_raw = environ.get("XDG_DATA_HOME", "").strip()
    if xdg_raw:
        xdg_path = _assert_no_raw_hazards(xdg_raw).expanduser()
        if _is_link_or_reparse(xdg_path):
            raise PathGuardError(f"XDG_DATA_HOME must not be a symbolic link: {xdg_path}")
        xdg_res = xdg_path.resolve()
        return validate_path(xdg_res / "EnergyBaseline", allowed_roots=[xdg_res]), "xdg"

    home = Path.home().resolve()
    return validate_path(home / ".local" / "share" / "EnergyBaseline", allowed_roots=[home]), "xdg"


def resolve_temp_dir(
    subdir: str = "EnergyBaseline",
    *,
    temp_root: str | Path | None = None,
) -> Path:
    """Resolve a safe normalized directory inside the system temporary folder."""
    if "/" in subdir or "\\" in subdir or _has_dotdot_segment(subdir) or not subdir.strip():
        raise PathGuardError(f"Invalid temp subdirectory name: {subdir!r}")
    base_raw = Path(tempfile.gettempdir()) if temp_root is None else _assert_no_raw_hazards(temp_root)
    if _is_link_or_reparse(base_raw):
        raise PathGuardError(f"Temporary root must not be a symbolic link: {base_raw}")
    base_resolved = base_raw.resolve()
    if _is_unc_string(str(base_resolved)):
        raise PathGuardError(f"Temporary root must not be a UNC path: {base_resolved}")
    return validate_path(base_resolved / subdir, allowed_roots=[base_resolved])


def resolve_cache_dir(app_home: str | Path, *, create: bool = True) -> Path:
    """Resolve and optionally create ``<app_home>/cache`` under ``app_home``."""
    home_raw = _assert_no_raw_hazards(app_home)
    if _is_link_or_reparse(home_raw):
        raise PathGuardError(f"Application home must not be a symbolic link: {home_raw}")
    home_res = home_raw.resolve()
    cache_path = validate_path(home_res / "cache", allowed_roots=[home_res])
    if create:
        cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


def default_picked_roots(
    *,
    app_home: Path | None = None,
    resource_dir: Path | None = None,
) -> list[Path]:
    """Default allowed root directories for user-selected files."""
    roots: list[Path] = []
    candidates: list[Path] = [
        Path.home(),
        Path(tempfile.gettempdir()),
        Path.cwd(),
    ]
    if app_home is not None:
        candidates.append(app_home)
    if resource_dir is not None:
        candidates.append(resource_dir)
    else:
        candidates.append(Path(__file__).resolve().parents[2])

    seen: set[Path] = set()
    for c in candidates:
        try:
            if not _is_unc_string(str(c)) and not _is_link_or_reparse(c):
                rc = c.resolve()
                if rc not in seen and not _is_unc_string(str(rc)):
                    seen.add(rc)
                    roots.append(rc)
        except OSError:
            continue
    return roots


def resolve_picked_path(
    candidate: str | Path,
    *,
    allowed_roots: Sequence[str | Path] | None = None,
    must_exist: bool = True,
) -> Path:
    """Validate a user-picked input or export file path."""
    roots = list(allowed_roots) if allowed_roots is not None else default_picked_roots()
    return validate_path(candidate, allowed_roots=roots, must_exist=must_exist)
