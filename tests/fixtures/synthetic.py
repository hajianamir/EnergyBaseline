"""Test-only re-export of the deterministic synthetic dataset generator (Phase 0 D0.9 / S6)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from samples import generate_samples as _generator
from samples.generate_samples import daily_plant, hourly_office, main, monthly_building, weather

__all__ = [
    "daily_plant",
    "generate_samples_into",
    "hourly_office",
    "main",
    "monthly_building",
    "weather",
]


def generate_samples_into(target_dir: Path, seed: int = 2024) -> Path:
    """Run ``samples/generate_samples.py`` deterministically into ``target_dir``."""
    target_dir.mkdir(parents=True, exist_ok=True)
    previous_out = _generator.OUT
    previous_rng = _generator.rng
    try:
        _generator.OUT = target_dir
        _generator.rng = np.random.default_rng(seed)
        _generator.main()
    finally:
        _generator.OUT = previous_out
        _generator.rng = previous_rng
    return target_dir
