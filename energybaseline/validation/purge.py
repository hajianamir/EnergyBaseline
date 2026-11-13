"""Purge / embargo gap sizing for leakage-safe temporal cross-validation (Phase 6 Gate G6).

Enforces the exact rule:
    purge_gap = max(1 day in steps, 1 step, max_lag, max_rolling_window)
so that larger lags or rolling windows strictly increase the purge gap between training and
validation blocks rather than remaining stuck at a fixed 1-day gap.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class PurgeGapSpec:
    """Auditable breakdown of how the temporal purge/embargo gap was sized."""

    purge_gap: int
    one_day_steps: int
    min_step_floor: int
    max_lag: int
    max_rolling_window: int
    governing_term: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "governing_term": self.governing_term,
            "max_lag": self.max_lag,
            "max_rolling_window": self.max_rolling_window,
            "min_step_floor": self.min_step_floor,
            "one_day_steps": self.one_day_steps,
            "purge_gap": self.purge_gap,
        }


def compute_purge_gap_spec(
    *,
    steps_per_day: float = 1.0,
    lags: Iterable[int] | None = None,
    rolling_windows: Iterable[int] | None = None,
    max_lag: int = 0,
    max_rolling_window: int = 0,
    min_steps: int = 1,
) -> PurgeGapSpec:
    """Compute ``max(1 day, 1 step, max lag, max rolling window)`` with provenance."""
    spd = float(steps_per_day) if math.isfinite(float(steps_per_day)) and float(steps_per_day) > 0 else 1.0
    one_day = max(1, int(math.ceil(spd - 1e-12)))
    floor_step = max(1, int(min_steps))

    lag_vals = [int(v) for v in (lags or ()) if int(v) > 0]
    roll_vals = [int(v) for v in (rolling_windows or ()) if int(v) > 0]
    eff_lag = max([0, int(max_lag), *lag_vals])
    eff_roll = max([0, int(max_rolling_window), *roll_vals])

    candidates = [
        ("1_step", floor_step),
        ("1_day", one_day),
        ("max_lag", eff_lag),
        ("max_rolling_window", eff_roll),
    ]
    # Deterministic tie-break: if max_lag or max_rolling_window ties/exceeds 1_day, report the structural driver
    best_val = max(v for _, v in candidates)
    priority_order = ("max_lag", "max_rolling_window", "1_day", "1_step")
    gov = next(name for name in priority_order if dict(candidates)[name] == best_val)
    return PurgeGapSpec(
        purge_gap=int(best_val),
        one_day_steps=int(one_day),
        min_step_floor=int(floor_step),
        max_lag=int(eff_lag),
        max_rolling_window=int(eff_roll),
        governing_term=gov,
    )


def compute_purge_gap(
    *,
    steps_per_day: float = 1.0,
    lags: Iterable[int] | None = None,
    rolling_windows: Iterable[int] | None = None,
    max_lag: int = 0,
    max_rolling_window: int = 0,
    min_steps: int = 1,
) -> int:
    """Return integer purge gap ``max(1 day, 1 step, max lag, max rolling window)``."""
    return compute_purge_gap_spec(
        steps_per_day=steps_per_day,
        lags=lags,
        rolling_windows=rolling_windows,
        max_lag=max_lag,
        max_rolling_window=max_rolling_window,
        min_steps=min_steps,
    ).purge_gap
