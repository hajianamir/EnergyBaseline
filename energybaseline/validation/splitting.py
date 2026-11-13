"""Strict chronological train / validation / holdout splitting (VAL-1).

Random k-fold shuffling or non-monotonic index reordering is explicitly forbidden on energy time series.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .purge import compute_purge_gap


class TimeSeriesShuffleForbiddenError(ValueError):
    """Raised when random shuffling or non-chronological splitting is attempted on time-series data (VAL-1)."""


@dataclass(frozen=True)
class ChronologicalSplit:
    """Strict chronological 3-way partition (train -> purge -> val -> purge -> holdout)."""

    n_total: int
    train_idx: np.ndarray
    val_idx: np.ndarray
    holdout_idx: np.ndarray
    purge_gap: int
    timestamps_monotonic: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "holdout_end": int(self.holdout_idx[-1]) if self.holdout_idx.size else None,
            "holdout_size": int(self.holdout_idx.size),
            "holdout_start": int(self.holdout_idx[0]) if self.holdout_idx.size else None,
            "n_total": int(self.n_total),
            "purge_gap": int(self.purge_gap),
            "timestamps_monotonic": bool(self.timestamps_monotonic),
            "train_end": int(self.train_idx[-1]),
            "train_size": int(self.train_idx.size),
            "train_start": int(self.train_idx[0]),
            "val_end": int(self.val_idx[-1]) if self.val_idx.size else None,
            "val_size": int(self.val_idx.size),
            "val_start": int(self.val_idx[0]) if self.val_idx.size else None,
        }


def _validate_monotonic_time_axis(data: Any) -> int:
    if isinstance(data, int):
        n = int(data)
        if n <= 0:
            raise ValueError(f"Sample count must be positive, got {n}.")
        return n
    if isinstance(data, (pd.DataFrame, pd.Series)):
        idx = data.index
        if isinstance(idx, pd.DatetimeIndex) and not idx.is_monotonic_increasing:
            raise TimeSeriesShuffleForbiddenError(
                "VAL-1 violation: DatetimeIndex must be monotonically non-decreasing before chronological splitting."
            )
        if isinstance(idx, pd.Index) and idx.dtype.kind in "iu" and not idx.is_monotonic_increasing:
            raise TimeSeriesShuffleForbiddenError(
                "VAL-1 violation: integer index is shuffled / non-monotonic; sort chronologically first."
            )
        return len(data)
    arr = np.asarray(data)
    return int(arr.shape[0])


def chronological_train_val_holdout_split(
    data: Any,
    *,
    train_frac: float = 0.70,
    val_frac: float = 0.15,
    holdout_frac: float = 0.15,
    steps_per_day: float = 1.0,
    max_lag: int = 0,
    max_rolling_window: int = 0,
    explicit_purge_gap: int | None = None,
    shuffle: bool = False,
) -> ChronologicalSplit:
    """Partition a time-ordered dataset into ``(train_idx, val_idx, holdout_idx)`` with purge gaps (VAL-1)."""
    if shuffle:
        raise TimeSeriesShuffleForbiddenError(
            "VAL-1 violation: random shuffling (shuffle=True) is strictly forbidden on energy time series."
        )
    if train_frac <= 0.0 or val_frac < 0.0 or holdout_frac < 0.0:
        raise ValueError("Split fractions must be non-negative with train_frac > 0.")
    total_frac = float(train_frac + val_frac + holdout_frac)
    if abs(total_frac - 1.0) > 1e-6:
        raise ValueError(f"train_frac + val_frac + holdout_frac must equal 1.0, got {total_frac:.6f}.")

    n = _validate_monotonic_time_axis(data)
    req_gap = compute_purge_gap(
        steps_per_day=steps_per_day,
        max_lag=max_lag,
        max_rolling_window=max_rolling_window,
    )
    if explicit_purge_gap is not None:
        req_gap = max(req_gap, int(explicit_purge_gap))

    n_splits_with_gap = (1 if val_frac > 0 else 0) + (1 if holdout_frac > 0 else 0)
    min_needed = 8 + n_splits_with_gap * (req_gap + 2)
    if n < min_needed:
        raise ValueError(
            f"Insufficient observations (n={n}) for chronological split with purge_gap={req_gap} (need >= {min_needed})."
        )

    usable = n - n_splits_with_gap * req_gap
    n_hold = int(max(2, round(usable * holdout_frac))) if holdout_frac > 0 else 0
    n_val = int(max(2, round(usable * val_frac))) if val_frac > 0 else 0
    n_train = usable - n_val - n_hold
    if n_train < 6:
        deficit = 6 - n_train
        take_val = min(deficit, max(0, n_val - 2))
        n_val -= take_val
        n_train += take_val
        deficit = 6 - n_train
        if deficit > 0 and n_hold > 2:
            take_hold = min(deficit, max(0, n_hold - 2))
            n_hold -= take_hold
            n_train += take_hold

    train_idx = np.arange(0, n_train, dtype=int)
    cursor = n_train
    if n_val > 0:
        cursor += req_gap
        val_idx = np.arange(cursor, cursor + n_val, dtype=int)
        cursor += n_val
    else:
        val_idx = np.empty(0, dtype=int)

    if n_hold > 0:
        cursor += req_gap
        holdout_idx = np.arange(cursor, n, dtype=int)
    else:
        holdout_idx = np.empty(0, dtype=int)

    return ChronologicalSplit(
        n_total=n,
        train_idx=train_idx,
        val_idx=val_idx,
        holdout_idx=holdout_idx,
        purge_gap=req_gap,
        timestamps_monotonic=True,
    )


def assert_strictly_chronological_folds(
    folds: Sequence[tuple[np.ndarray, np.ndarray]],
    *,
    require_future_validation: bool = False,
    min_purge_gap: int = 1,
) -> None:
    """Validate that CV folds were not produced by a random shuffler and honor ``min_purge_gap`` (VAL-1)."""
    if not folds:
        raise ValueError("Fold sequence is empty.")
    for fold_i, (tr, va) in enumerate(folds):
        tr_arr = np.asarray(tr, dtype=int).reshape(-1)
        va_arr = np.asarray(va, dtype=int).reshape(-1)
        if tr_arr.size == 0 or va_arr.size == 0:
            raise TimeSeriesShuffleForbiddenError(f"Fold {fold_i} has empty train or validation indices.")
        if np.any(np.diff(tr_arr) <= 0) or np.any(np.diff(va_arr) <= 0):
            raise TimeSeriesShuffleForbiddenError(
                f"Fold {fold_i} indices are unordered or contain duplicates (random shuffling forbidden)."
            )
        # Validation block in time-series CV must be a contiguous horizon, never randomly scattered rows
        if np.any(np.diff(va_arr) != 1):
            raise TimeSeriesShuffleForbiddenError(
                f"Fold {fold_i} validation indices are non-contiguous (shuffled k-fold forbidden on time series)."
            )
        if np.intersect1d(tr_arr, va_arr).size > 0:
            raise TimeSeriesShuffleForbiddenError(f"Fold {fold_i} has overlapping train and validation indices.")
        min_dist = int(np.min(np.abs(tr_arr[:, None] - va_arr[None, :])))
        if min_dist <= int(min_purge_gap):
            raise TimeSeriesShuffleForbiddenError(
                f"Fold {fold_i} violates purge gap: minimum |tr - va| = {min_dist} <= required {min_purge_gap}."
            )
        if require_future_validation and int(tr_arr[-1]) >= int(va_arr[0]):
            raise TimeSeriesShuffleForbiddenError(
                f"Fold {fold_i} uses future training rows ({tr_arr[-1]} >= {va_arr[0]}) in forward-only mode."
            )
