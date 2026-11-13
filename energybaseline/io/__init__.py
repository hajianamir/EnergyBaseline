"""Safe filesystem path resolution and validation utilities (P1.4, SHL-3/SHL-4, AC-04)."""

from .paths import (
    PathGuardError,
    default_picked_roots,
    resolve_app_home,
    resolve_cache_dir,
    resolve_picked_path,
    resolve_temp_dir,
    validate_path,
)

__all__ = [
    "PathGuardError",
    "default_picked_roots",
    "resolve_app_home",
    "resolve_cache_dir",
    "resolve_picked_path",
    "resolve_temp_dir",
    "validate_path",
]
