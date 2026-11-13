"""Expanding, rolling, and purged blocked walk-forward cross-validation (VAL-2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from .purge import PurgeGapSpec, compute_purge_gap_spec
from .splitting import TimeSeriesShuffleForbiddenError

WalkForwardMode = Literal["expanding", "rolling", "blocked"]


@dataclass(frozen=True)
class WalkForwardFold:
    """Single walk-forward cross-validation fold with explicit purge/embargo gap metadata."""

    fold_id: int
    train_idx: np.ndarray
    val_idx: np.ndarray
    purge_gap: int
    horizon: int
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_id": int(self.fold_id),
            "horizon": int(self.horizon),
            "mode": str(self.mode),
            "purge_gap": int(self.purge_gap),
            "train_end": int(self.train_idx[-1]),
            "train_size": int(self.train_idx.size),
            "train_start": int(self.train_idx[0]),
            "val_end": int(self.val_idx[-1]),
            "val_size": int(self.val_idx.size),
            "val_start": int(self.val_idx[0]),
        }


def generate_walkforward_folds(
    n_obs: int,
    *,
    n_folds: int = 4,
    mode: WalkForwardMode = "expanding",
    horizon: int | None = None,
    train_window: int | None = None,
    min_train_size: int = 8,
    steps_per_day: float = 1.0,
    max_lag: int = 0,
    max_rolling_window: int = 0,
    explicit_purge_gap: int | None = None,
    shuffle: bool = False,
) -> tuple[list[WalkForwardFold], PurgeGapSpec]:
    """Generate leakage-safe walk-forward CV folds whose embargo/purge gap grows with max lag/rolling window."""
    if shuffle:
        raise TimeSeriesShuffleForbiddenError("VAL-2 violation: shuffle=True is forbidden in walk-forward CV.")
    n = int(n_obs)
    k = max(2, int(n_folds))
    if mode not in ("expanding", "rolling", "blocked"):
        raise ValueError(f"Unsupported walk-forward mode {mode!r}; expected 'expanding', 'rolling', or 'blocked'.")

    gap_spec = compute_purge_gap_spec(
        steps_per_day=steps_per_day,
        max_lag=max_lag,
        max_rolling_window=max_rolling_window,
    )
    gap = gap_spec.purge_gap if explicit_purge_gap is None else max(gap_spec.purge_gap, int(explicit_purge_gap))
    if gap != gap_spec.purge_gap:
        gap_spec = PurgeGapSpec(
            purge_gap=gap,
            one_day_steps=gap_spec.one_day_steps,
            min_step_floor=gap_spec.min_step_floor,
            max_lag=gap_spec.max_lag,
            max_rolling_window=gap_spec.max_rolling_window,
            governing_term="explicit_purge_gap",
        )

    idx = np.arange(n, dtype=int)
    folds: list[WalkForwardFold] = []

    if mode in ("expanding", "rolling"):
        min_tr = max(5, int(min_train_size))
        if horizon is not None:
            h = max(1, int(horizon))
            val_total = k * h
            first_val_start = n - val_total
            while first_val_start - gap < min_tr and k > 1:
                k -= 1
                val_total = k * h
                first_val_start = n - val_total
            boundaries = [first_val_start + i * h for i in range(k + 1)]
        else:
            first_val_start = max(min_tr + gap, int(round(0.45 * n)))
            if n - first_val_start < k:
                first_val_start = max(min_tr + gap, n - k * 2)
            boundaries = np.linspace(first_val_start, n, k + 1, dtype=int).tolist()

        roll_w = int(train_window) if train_window is not None else max(min_tr, boundaries[0] - gap)
        for i in range(len(boundaries) - 1):
            v_start, v_stop = int(boundaries[i]), int(boundaries[i + 1])
            tr_end_excl = v_start - gap
            if v_stop <= v_start or tr_end_excl < min_tr:
                continue
            tr_start = 0 if mode == "expanding" else max(0, tr_end_excl - roll_w)
            tr_idx = idx[tr_start:tr_end_excl]
            va_idx = idx[v_start:v_stop]
            if tr_idx.size >= min_tr and va_idx.size >= 1:
                folds.append(
                    WalkForwardFold(
                        fold_id=len(folds),
                        train_idx=tr_idx,
                        val_idx=va_idx,
                        purge_gap=gap,
                        horizon=int(va_idx.size),
                        mode=mode,
                    )
                )
    else:
        boundaries = np.linspace(0, n, k + 1, dtype=int).tolist()
        min_tr = max(5, int(min_train_size))
        for i in range(k):
            v_start, v_stop = int(boundaries[i]), int(boundaries[i + 1])
            if v_stop <= v_start:
                continue
            mask = np.ones(n, dtype=bool)
            mask[max(0, v_start - gap) : min(n, v_stop + gap)] = False
            tr_idx = idx[mask]
            va_idx = idx[v_start:v_stop]
            if tr_idx.size >= min_tr and va_idx.size >= 1:
                folds.append(
                    WalkForwardFold(
                        fold_id=len(folds),
                        train_idx=tr_idx,
                        val_idx=va_idx,
                        purge_gap=gap,
                        horizon=int(va_idx.size),
                        mode="blocked",
                    )
                )

    return folds, gap_spec


def folds_as_index_tuples(folds: list[WalkForwardFold]) -> list[tuple[np.ndarray, np.ndarray]]:
    """Convert ``WalkForwardFold`` objects to ``(train_idx, val_idx)`` NumPy index pairs."""
    return [(f.train_idx, f.val_idx) for f in folds]
