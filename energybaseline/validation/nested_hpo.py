"""Nested walk-forward hyperparameter optimization strictly inside outer training folds (VAL-3).

Enforces both trial-count and wall-clock caps per Phase 5/6 §9 Risk 1 so complex GBDT / NN / SARIMAX
families remain bounded inside inner walk-forward folds, and guarantees zero outer-fold data access.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import time
from typing import Any, Callable

import numpy as np
import pandas as pd

from .preprocessing import InFoldPreprocessor
from .walkforward import WalkForwardMode, generate_walkforward_folds


@dataclass(frozen=True)
class NestedHPOResult:
    """Audit record of inner-fold hyperparameter tuning."""

    best_params: dict[str, Any]
    best_inner_rmse: float
    n_trials_executed: int
    max_trials_budget: int
    timeout_seconds: float
    elapsed_seconds: float
    timed_out: bool
    inner_fold_count: int
    outer_train_rows: int
    outer_train_sha256: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_inner_rmse": round(float(self.best_inner_rmse), 8) if math.isfinite(self.best_inner_rmse) else None,
            "best_params": dict(self.best_params),
            "elapsed_seconds": round(float(self.elapsed_seconds), 4),
            "inner_fold_count": int(self.inner_fold_count),
            "max_trials_budget": int(self.max_trials_budget),
            "n_trials_executed": int(self.n_trials_executed),
            "note": self.note,
            "outer_train_rows": int(self.outer_train_rows),
            "outer_train_sha256": self.outer_train_sha256,
            "timed_out": bool(self.timed_out),
            "timeout_seconds": float(self.timeout_seconds),
        }


def run_nested_walkforward_hpo(
    build_estimator: Callable[[dict[str, Any]], Any],
    space_fn: Callable[[Any], dict[str, Any]],
    X_outer_train: pd.DataFrame,
    y_outer_train: Any,
    *,
    max_trials: int = 8,
    timeout_seconds: float = 5.0,
    inner_folds: int = 3,
    mode: WalkForwardMode = "expanding",
    steps_per_day: float = 1.0,
    max_lag: int = 0,
    max_rolling_window: int = 0,
    scale_in_inner_folds: bool = True,
    enforce_k_max: bool = True,
    min_samples: int = 30,
    seed: int = 42,
) -> NestedHPOResult:
    """Tune hyperparameters strictly on inner walk-forward folds carved from ``(X_outer_train, y_outer_train)``."""
    t0 = time.perf_counter()
    if not isinstance(X_outer_train, pd.DataFrame):
        X_outer_train = pd.DataFrame(X_outer_train)
    yv = np.asarray(y_outer_train, dtype=float).reshape(-1)
    n = int(yv.size)

    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(yv, dtype="<f8").tobytes())
    digest.update(np.ascontiguousarray(X_outer_train.fillna(0.0).to_numpy(dtype=float), dtype="<f8").tobytes())
    outer_sha = digest.hexdigest()

    if max_trials <= 0 or n < int(min_samples):
        return NestedHPOResult(
            best_params={},
            best_inner_rmse=float("nan"),
            n_trials_executed=0,
            max_trials_budget=int(max_trials),
            timeout_seconds=float(timeout_seconds),
            elapsed_seconds=time.perf_counter() - t0,
            timed_out=False,
            inner_fold_count=0,
            outer_train_rows=n,
            outer_train_sha256=outer_sha,
            note=f"Nested HPO skipped cleanly (n={n} < min_samples={min_samples} or max_trials={max_trials} <= 0).",
        )

    wf_folds, gap_spec = generate_walkforward_folds(
        n,
        n_folds=max(2, int(inner_folds)),
        mode=mode,
        min_train_size=max(8, n // 5),
        steps_per_day=steps_per_day,
        max_lag=max_lag,
        max_rolling_window=max_rolling_window,
    )
    if not wf_folds:
        return NestedHPOResult(
            best_params={},
            best_inner_rmse=float("nan"),
            n_trials_executed=0,
            max_trials_budget=int(max_trials),
            timeout_seconds=float(timeout_seconds),
            elapsed_seconds=time.perf_counter() - t0,
            timed_out=False,
            inner_fold_count=0,
            outer_train_rows=n,
            outer_train_sha256=outer_sha,
            note="Nested HPO skipped (insufficient outer-train rows for purged inner folds).",
        )

    # Pre-fit inner-fold preprocessors once (strictly per inner fold!) so every trial sees identical leakage-safe matrices
    prepared_folds: list[tuple[pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray]] = []
    for f in wf_folds:
        prep = InFoldPreprocessor(
            scale=scale_in_inner_folds,
            enforce_k_max=enforce_k_max,
            seed=seed,
        )
        X_itr = prep.fit_transform(X_outer_train.iloc[f.train_idx], yv[f.train_idx])
        X_iva = prep.transform(X_outer_train.iloc[f.val_idx])
        prepared_folds.append((X_itr, yv[f.train_idx], X_iva, yv[f.val_idx]))

    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sampler = optuna.samplers.TPESampler(seed=int(seed))
    study = optuna.create_study(direction="minimize", sampler=sampler)

    def _objective(trial: optuna.Trial) -> float:
        params = space_fn(trial)
        rmses: list[float] = []
        for X_itr, y_itr, X_iva, y_iva in prepared_folds:
            est = build_estimator(params)
            est.fit(X_itr, y_itr)
            pred = np.asarray(est.predict(X_iva), dtype=float).reshape(-1)
            rmses.append(float(np.sqrt(np.mean((y_iva - pred) ** 2))))
        return float(np.mean(rmses))

    study.optimize(_objective, n_trials=int(max_trials), timeout=float(timeout_seconds), show_progress_bar=False)
    elapsed = time.perf_counter() - t0
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    best_p = dict(study.best_params) if completed else {}
    best_val = float(study.best_value) if completed else float("nan")
    timed_out = len(completed) < int(max_trials) and elapsed >= 0.95 * float(timeout_seconds)
    return NestedHPOResult(
        best_params=best_p,
        best_inner_rmse=best_val,
        n_trials_executed=len(completed),
        max_trials_budget=int(max_trials),
        timeout_seconds=float(timeout_seconds),
        elapsed_seconds=elapsed,
        timed_out=timed_out,
        inner_fold_count=len(wf_folds),
        outer_train_rows=n,
        outer_train_sha256=outer_sha,
        note=(
            f"Optuna TPE inner walk-forward HPO: {len(completed)}/{max_trials} trials across "
            f"{len(wf_folds)} inner folds (purge_gap={gap_spec.purge_gap}, elapsed={elapsed:.2f}s)."
        ),
    )
