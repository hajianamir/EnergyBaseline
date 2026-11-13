"""Automated future-perturbation data-leakage canary test (VAL-5).

Proves mathematically that corrupting future target values (and future predictor rows strictly beyond
the validation block) never alters any historical walk-forward fold prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd


class DataLeakageDetectedError(AssertionError):
    """Raised when future target/predictor perturbations alter historical fold predictions (VAL-5)."""


@dataclass(frozen=True)
class LeakageCanaryResult:
    """Diagnostic outcome of the VAL-5 future-perturbation canary."""

    passed: bool
    max_abs_historical_diff: float
    tolerance: float
    folds_checked: int
    fold_max_diffs: tuple[float, ...]
    spike_magnitude: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_max_diffs": [float(d) for d in self.fold_max_diffs],
            "folds_checked": int(self.folds_checked),
            "max_abs_historical_diff": float(self.max_abs_historical_diff),
            "passed": bool(self.passed),
            "reason": self.reason,
            "spike_magnitude": float(self.spike_magnitude),
            "tolerance": float(self.tolerance),
        }


def run_future_perturbation_canary(
    fold_predictor: Callable[[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray], np.ndarray],
    X: pd.DataFrame,
    y: Any,
    folds: Sequence[tuple[np.ndarray, np.ndarray]],
    *,
    spike_magnitude: float = 1_000_000.0,
    perturb_future_predictors: bool = True,
    tol: float = 1e-11,
    raise_on_leak: bool = False,
) -> LeakageCanaryResult:
    """Verify that future perturbations never change historical fold predictions.

    Parameters
    ----------
    fold_predictor:
        Callable ``(X_full, y_full, train_idx, val_idx) -> y_pred_val``. A leakage-safe implementation
        slices ``X_full.iloc[train_idx]`` / ``y_full[train_idx]`` before fitting any imputer, scaler,
        knot finder, selector, or estimator. A leaky implementation (e.g., scaling or selecting on
        ``X_full, y_full`` prior to fold slicing) will change ``y_pred_val`` when future rows are spiked.
    """
    if not isinstance(X, pd.DataFrame):
        X = pd.DataFrame(X)
    y_arr = np.asarray(y, dtype=float).reshape(-1)
    n = int(y_arr.size)
    if len(X) != n:
        raise ValueError(f"X ({len(X)}) and y ({n}) length mismatch.")

    diffs: list[float] = []
    checked = 0
    for tr_idx, va_idx in folds:
        tr = np.asarray(tr_idx, dtype=int).reshape(-1)
        va = np.asarray(va_idx, dtype=int).reshape(-1)
        if tr.size == 0 or va.size == 0:
            continue
        # Only forward-causal folds (max(tr) < min(va)) have a pure future horizon >= min(va)
        if int(np.max(tr)) >= int(np.min(va)):
            continue

        checked += 1
        pred_clean = np.asarray(fold_predictor(X.copy(), y_arr.copy(), tr, va), dtype=float).reshape(-1)

        y_spiked = y_arr.copy()
        # Perturb ALL target values from the start of validation onward (future relative to training!)
        y_spiked[int(np.min(va)) :] += float(spike_magnitude)

        X_spiked = X.copy()
        if perturb_future_predictors and int(np.max(va)) + 1 < n:
            # Perturb all predictor rows strictly after the end of this validation horizon
            future_slice = X_spiked.index[int(np.max(va)) + 1 :]
            for col in X_spiked.columns:
                if pd.api.types.is_numeric_dtype(X_spiked[col]):
                    X_spiked.loc[future_slice, col] = X_spiked.loc[future_slice, col].astype(float) + float(
                        spike_magnitude
                    )

        pred_spiked = np.asarray(fold_predictor(X_spiked, y_spiked, tr, va), dtype=float).reshape(-1)
        if pred_clean.shape != pred_spiked.shape:
            max_d = float("inf")
        else:
            max_d = float(np.max(np.abs(pred_clean - pred_spiked)))
        diffs.append(max_d)

    if checked == 0:
        raise ValueError("Leakage canary requires at least one forward-causal fold with max(train_idx) < min(val_idx).")

    worst = max(diffs)
    ok = bool(worst <= float(tol))
    reason = (
        f"VAL-5 passed: max historical prediction drift {worst:.3e} <= {tol:.3e} across {checked} causal fold(s)."
        if ok
        else f"VAL-5 LEAKAGE DETECTED: historical predictions shifted by {worst:.6g} (> {tol:.3e}) when future rows were perturbed."
    )
    res = LeakageCanaryResult(
        passed=ok,
        max_abs_historical_diff=worst,
        tolerance=float(tol),
        folds_checked=checked,
        fold_max_diffs=tuple(diffs),
        spike_magnitude=float(spike_magnitude),
        reason=reason,
    )
    if not ok and raise_on_leak:
        raise DataLeakageDetectedError(reason)
    return res
