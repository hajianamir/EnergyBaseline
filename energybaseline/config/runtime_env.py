"""Centralized non-DB runtime process environment helpers under ``energybaseline.config``.

Database connection environment variables remain strictly confined to ``energybaseline/config/settings.py``.
All worker-process markers, Qt headless/HiDPI flags, and path-resolution ``os.environ`` reads live here so
that zero ``os.environ`` references exist outside ``energybaseline/config/``.
"""

from __future__ import annotations

from collections.abc import Mapping
import os


def get_process_environ() -> Mapping[str, str]:
    """Return read-only view of current OS process environment."""
    return os.environ


def is_in_worker_process() -> bool:
    """Return True when executing inside a spawned EnergyBaseline worker process."""
    return os.environ.get("_EB_IN_WORKER") == "1"


def mark_worker_process_env() -> None:
    """Stamp worker isolation and single-thread BLAS defaults inside spawned worker processes."""
    os.environ["_EB_IN_WORKER"] = "1"
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")


def is_interactive_ui() -> bool:
    """Return True unless ``EB_NONINTERACTIVE=1`` suppresses modal dialogs/shortcuts."""
    return os.environ.get("EB_NONINTERACTIVE") != "1"


def configure_qt_runtime_env(*, enable_highdpi: bool = False) -> None:
    """Set default Qt binding and optional HiDPI environment flags before QApplication creation."""
    os.environ.setdefault("QT_API", "pyside6")
    if enable_highdpi:
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
