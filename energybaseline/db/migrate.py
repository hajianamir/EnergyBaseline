"""Programmatic ``alembic upgrade head`` (works inside the frozen .exe)."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from ..config import get_resource_dir


def alembic_config() -> Config:
    res = get_resource_dir()
    ini = res / "alembic.ini"
    cfg = Config(str(ini)) if ini.exists() else Config()
    cfg.set_main_option("script_location", str(Path(res / "alembic")))
    return cfg


def upgrade_head() -> None:
    command.upgrade(alembic_config(), "head")
