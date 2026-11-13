"""Leakage-safe in-fold model evaluation pipeline (Phase 6 Gate G6).

Guarantees that imputation, degree-day knot estimation, standardization, and ``k_max = min(p, n // 20)``
feature selection are fitted strictly inside each training fold, with temporal purge gap
``max(1 day, 1 step, max lag, max rolling window)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from .mining.clustering import assert_no_cluster_labels_in_model_inputs
from .validation import (
    ASHRAE14Metrics,
    FoldPreprocessingSummary,
    InFoldPreprocessor,
    PurgeGapSpec,
    WalkForwardFold,
    WalkForwardMode,
    compute_ashrae14_oos_metrics,
    generate_walkforward_folds,
)


@dataclass
class InFoldCVResult:
    """Out-of-fold predictions, metrics, and per-fold preprocessing audit trail."""

    oof_predictions: np.ndarray
    valid_mask: np.ndarray
    oos_metrics: ASHRAE14Metrics
    fold_cvrmse: list[float]
    fold_summaries: list[FoldPreprocessingSummary]
    folds: list[WalkForwardFold]
    purge_spec: PurgeGapSpec
    final_preprocessor: InFoldPreprocessor
    final_estimator: Any


class InFoldModelPipeline:
    """Encapsulates ``InFoldPreprocessor`` + estimator so preprocessing never leaks across folds."""

    def __init__(
        self,
        estimator_factory: Callable[[], Any],
        *,
        scale: bool = True,
        fit_degree_day_knots: bool = False,
        temp_col: str | None = None,
        lags: Sequence[int] = (),
        lag_cols: Sequence[str] = (),
        rolling_windows: Sequence[int] = (),
        roll_cols: Sequence[str] = (),
        selection_method: str = "auto",
        enforce_k_max: bool = True,
        seed: int = 42,
    ) -> None:
        self.estimator_factory = estimator_factory
        self.scale = bool(scale)
        self.fit_degree_day_knots = bool(fit_degree_day_knots)
        self.temp_col = temp_col
        self.lags = tuple(int(v) for v in lags if int(v) > 0)
        self.lag_cols = tuple(str(c) for c in lag_cols)
        self.rolling_windows = tuple(int(v) for v in rolling_windows if int(v) > 1)
        self.roll_cols = tuple(str(c) for c in roll_cols)
        self.selection_method = str(selection_method)
        self.enforce_k_max = bool(enforce_k_max)
        self.seed = int(seed)

        self.preprocessor_: InFoldPreprocessor | None = None
        self.estimator_: Any = None

    def _new_preprocessor(self) -> InFoldPreprocessor:
        return InFoldPreprocessor(
            scale=self.scale,
            fit_degree_day_knots=self.fit_degree_day_knots,
            temp_col=self.temp_col,
            lags=self.lags,
            lag_cols=self.lag_cols,
            rolling_windows=self.rolling_windows,
            roll_cols=self.roll_cols,
            selection_method=self.selection_method,
            enforce_k_max=self.enforce_k_max,
            seed=self.seed,
        )

    def fit(self, X_train: pd.DataFrame, y_train: Any) -> "InFoldModelPipeline":
        assert_no_cluster_labels_in_model_inputs(X_train)
        prep = self._new_preprocessor()
        X_tr_proc = prep.fit_transform(X_train, y_train)
        est = self.estimator_factory()
        est.fit(X_tr_proc, np.asarray(y_train, dtype=float).reshape(-1))
        self.preprocessor_ = prep
        self.estimator_ = est
        return self

    def predict(self, X_eval: pd.DataFrame) -> np.ndarray:
        if self.preprocessor_ is None or self.estimator_ is None:
            raise RuntimeError("InFoldModelPipeline.predict() called before fit().")
        X_ev_proc = self.preprocessor_.transform(X_eval)
        return np.asarray(self.estimator_.predict(X_ev_proc), dtype=float).reshape(-1)


def run_leakage_safe_cv(
    pipeline_builder: Callable[[], InFoldModelPipeline],
    X: pd.DataFrame,
    y: Any,
    *,
    n_folds: int = 4,
    mode: WalkForwardMode = "expanding",
    steps_per_day: float = 1.0,
    granularity: str = "monthly",
    explicit_purge_gap: int | None = None,
) -> InFoldCVResult:
    """Execute walk-forward CV fitting imputer/scaler/selector exclusively inside each fold."""
    assert_no_cluster_labels_in_model_inputs(X)
    if not isinstance(X, pd.DataFrame):
        X = pd.DataFrame(X)
    yv = np.asarray(y, dtype=float).reshape(-1)
    n = int(yv.size)
    if len(X) != n:
        raise ValueError(f"X ({len(X)}) and y ({n}) length mismatch.")

    probe = pipeline_builder()
    max_lag = max(probe.lags) if probe.lags else 0
    max_roll = max(probe.rolling_windows) if probe.rolling_windows else 0
    folds, purge_spec = generate_walkforward_folds(
        n,
        n_folds=n_folds,
        mode=mode,
        steps_per_day=steps_per_day,
        max_lag=max_lag,
        max_rolling_window=max_roll,
        explicit_purge_gap=explicit_purge_gap,
    )
    if not folds:
        raise ValueError(f"Insufficient observations (n={n}) to construct purged walk-forward folds.")

    oof = np.full(n, np.nan, dtype=float)
    fold_cvrmse: list[float] = []
    fold_summaries: list[FoldPreprocessingSummary] = []

    for wf in folds:
        fold_pipe = pipeline_builder()
        # Causal predictor lags/rolls within validation block can see prior rows, NEVER future rows beyond va_end:
        va_end = int(wf.val_idx[-1]) + 1
        X_tr_slice = X.iloc[wf.train_idx]
        y_tr_slice = yv[wf.train_idx]
        fold_pipe.fit(X_tr_slice, y_tr_slice)
        assert fold_pipe.preprocessor_ is not None
        fold_summaries.append(fold_pipe.preprocessor_.summary())

        # Predict on causal prefix up to va_end, then slice exact validation indices so lags work without future access
        X_causal_prefix = X.iloc[:va_end]
        preds_prefix = fold_pipe.predict(X_causal_prefix)
        preds_va = preds_prefix[wf.val_idx]
        oof[wf.val_idx] = preds_va
        m_fold = compute_ashrae14_oos_metrics(yv[wf.val_idx], preds_va, granularity=granularity)
        fold_cvrmse.append(m_fold.cvrmse)

    valid = np.isfinite(oof)
    final_pipe = pipeline_builder().fit(X, yv)
    assert final_pipe.preprocessor_ is not None
    n_par = getattr(final_pipe.estimator_, "n_params", None)
    n_par_int = int(n_par() if callable(n_par) else (n_par or max(1, len(final_pipe.preprocessor_.selected_features_) + 1)))
    oos_m = compute_ashrae14_oos_metrics(yv[valid], oof[valid], n_params=n_par_int, granularity=granularity)
    return InFoldCVResult(
        oof_predictions=oof,
        valid_mask=valid,
        oos_metrics=oos_m,
        fold_cvrmse=fold_cvrmse,
        fold_summaries=fold_summaries,
        folds=folds,
        purge_spec=purge_spec,
        final_preprocessor=final_pipe.preprocessor_,
        final_estimator=final_pipe.estimator_,
    )
