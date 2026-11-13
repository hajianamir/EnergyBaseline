"""Inner-fold-only NNLS ensemble stacking with strict > 3% CV(RMSE) gain gate (Phase 6 Gate G6).

Prevents outer-fold data leakage:
1. For every outer training fold ``(X_outer_train, y_outer_train)``, inner walk-forward folds are
   generated strictly inside ``outer_train``.
2. Base learners are fitted on each inner training fold and predict on the inner validation fold.
3. Non-Negative Least Squares (``scipy.optimize.nnls``) weights are fitted EXCLUSIVELY on those
   inner-fold OOF predictions, subject to ``k_max = min(M, n_inner // 20)`` constituent sparsity.
4. Outer-fold validation targets are NEVER passed into ``nnls``.
5. The ensemble is accepted ONLY when outer-fold OOS ``CV(RMSE)`` improves by strictly ``> 3.0%``
   over the single best constituent model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from ..mining.clustering import assert_no_cluster_labels_in_model_inputs
from ..validation.ashrae14_metrics import ASHRAE14Metrics, compute_ashrae14_oos_metrics
from ..validation.preprocessing import InFoldPreprocessor, compute_k_max
from ..validation.walkforward import WalkForwardMode, generate_walkforward_folds


@dataclass(frozen=True)
class NNLSEnsembleResult:
    """Audit record for inner-fold NNLS ensemble evaluation and > 3% gain gate."""

    accepted: bool
    status: str
    constituent_names: tuple[str, ...]
    final_weights: dict[str, float]
    per_outer_fold_weights: list[dict[str, float]]
    oof_predictions: np.ndarray
    valid_mask: np.ndarray
    ensemble_metrics: ASHRAE14Metrics
    best_single_name: str
    best_single_cvrmse: float
    relative_cvrmse_gain_pct: float
    required_gain_pct: float
    k_max_constituents: int
    fitted_solely_on_inner_oof: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": bool(self.accepted),
            "best_single_cvrmse": round(float(self.best_single_cvrmse), 5),
            "best_single_name": self.best_single_name,
            "constituent_names": list(self.constituent_names),
            "ensemble_cvrmse": round(float(self.ensemble_metrics.cvrmse), 5),
            "ensemble_metrics": self.ensemble_metrics.to_dict(),
            "final_weights": {k: round(float(v), 6) for k, v in self.final_weights.items()},
            "fitted_solely_on_inner_oof": bool(self.fitted_solely_on_inner_oof),
            "k_max_constituents": int(self.k_max_constituents),
            "per_outer_fold_weights": [
                {k: round(float(v), 6) for k, v in fw.items()} for fw in self.per_outer_fold_weights
            ],
            "reason": self.reason,
            "relative_cvrmse_gain_pct": round(float(self.relative_cvrmse_gain_pct), 4),
            "required_gain_pct": float(self.required_gain_pct),
            "status": self.status,
        }


def fit_nnls_weights_on_inner_oof(
    P_inner_oof: np.ndarray,
    y_inner_oof: np.ndarray,
    *,
    normalize_simplex: bool = True,
    enforce_k_max: bool = True,
) -> tuple[np.ndarray, int]:
    """Fit non-negative least-squares weights strictly on inner-fold OOF predictions.

    Enforces ``k_max = min(M, n_valid // 20)`` constituent cap when ``n_valid >= 20`` (and ``min(M, 2)``
    when ``0 < n_valid < 20``) by re-solving ``nnls`` on the top-``k_max`` active columns.
    """
    P = np.asarray(P_inner_oof, dtype=float)
    yv = np.asarray(y_inner_oof, dtype=float).reshape(-1)
    if P.ndim != 2 or P.shape[0] != yv.size:
        raise ValueError(f"P_inner_oof shape {P.shape} incompatible with y_inner_oof length {yv.size}.")
    m = P.shape[1]
    if m == 0:
        raise ValueError("fit_nnls_weights_on_inner_oof requires >= 1 candidate column.")

    valid = np.isfinite(yv) & np.all(np.isfinite(P), axis=1)
    P_v = P[valid]
    y_v = yv[valid]
    n_v = int(y_v.size)
    if n_v < 3:
        w = np.zeros(m, dtype=float)
        w[0] = 1.0
        return w, 1

    raw_k = compute_k_max(m, n_v)
    cap_k = raw_k if raw_k >= 1 else min(m, 2)
    if not enforce_k_max:
        cap_k = m

    w_full, _ = nnls(P_v, y_v)
    if np.sum(w_full > 1e-10) > cap_k:
        keep_idx = np.argsort(-w_full, kind="mergesort")[:cap_k]
        w_sub, _ = nnls(P_v[:, keep_idx], y_v)
        w = np.zeros(m, dtype=float)
        w[keep_idx] = w_sub
    else:
        w = w_full

    w_sum = float(np.sum(w))
    if w_sum <= 1e-12:
        rmses = np.sqrt(np.mean((P_v - y_v[:, None]) ** 2, axis=0))
        best_j = int(np.argmin(rmses))
        w = np.zeros(m, dtype=float)
        w[best_j] = 1.0
    elif normalize_simplex:
        w = w / w_sum

    return w, int(cap_k)


class InnerFoldNNLSEnsemble:
    """Leakage-free NNLS stacking estimator whose meta-weights are learned solely on inner walk-forward OOF."""

    def __init__(
        self,
        base_factories: Mapping[str, Callable[[], Any]],
        *,
        inner_folds: int = 3,
        inner_mode: WalkForwardMode = "expanding",
        steps_per_day: float = 1.0,
        max_lag: int = 0,
        max_rolling_window: int = 0,
        scale_features: bool = True,
        enforce_k_max: bool = True,
        normalize_simplex: bool = True,
        seed: int = 42,
    ) -> None:
        if len(base_factories) < 2:
            raise ValueError("InnerFoldNNLSEnsemble requires >= 2 base learner factories.")
        self.base_factories = dict(base_factories)
        self.inner_folds = max(2, int(inner_folds))
        self.inner_mode: WalkForwardMode = inner_mode
        self.steps_per_day = float(steps_per_day)
        self.max_lag = int(max_lag)
        self.max_rolling_window = int(max_rolling_window)
        self.scale_features = bool(scale_features)
        self.enforce_k_max = bool(enforce_k_max)
        self.normalize_simplex = bool(normalize_simplex)
        self.seed = int(seed)

        self.names_: tuple[str, ...] = tuple(self.base_factories.keys())
        self.weights_: np.ndarray | None = None
        self.k_max_constituents_: int = 0
        self.preprocessor_: InFoldPreprocessor | None = None
        self.fitted_models_: dict[str, Any] = {}

    def fit(self, X_train: pd.DataFrame, y_train: Any) -> "InnerFoldNNLSEnsemble":
        assert_no_cluster_labels_in_model_inputs(X_train)
        if not isinstance(X_train, pd.DataFrame):
            X_train = pd.DataFrame(X_train)
        yv = np.asarray(y_train, dtype=float).reshape(-1)
        n = int(yv.size)
        m = len(self.names_)

        wf_inner, _ = generate_walkforward_folds(
            n,
            n_folds=self.inner_folds,
            mode=self.inner_mode,
            min_train_size=max(6, n // 5),
            steps_per_day=self.steps_per_day,
            max_lag=self.max_lag,
            max_rolling_window=self.max_rolling_window,
        )
        P_inner = np.full((n, m), np.nan, dtype=float)
        for wf in wf_inner:
            prep_in = InFoldPreprocessor(
                scale=self.scale_features,
                enforce_k_max=self.enforce_k_max,
                seed=self.seed,
            )
            X_itr = prep_in.fit_transform(X_train.iloc[wf.train_idx], yv[wf.train_idx])
            X_iva = prep_in.transform(X_train.iloc[wf.val_idx])
            for j, name in enumerate(self.names_):
                est = self.base_factories[name]()
                est.fit(X_itr, yv[wf.train_idx])
                P_inner[wf.val_idx, j] = np.asarray(est.predict(X_iva), dtype=float).reshape(-1)

        w, cap_k = fit_nnls_weights_on_inner_oof(
            P_inner,
            yv,
            normalize_simplex=self.normalize_simplex,
            enforce_k_max=self.enforce_k_max,
        )
        self.weights_ = w
        self.k_max_constituents_ = cap_k

        prep_full = InFoldPreprocessor(
            scale=self.scale_features,
            enforce_k_max=self.enforce_k_max,
            seed=self.seed,
        )
        X_full = prep_full.fit_transform(X_train, yv)
        self.preprocessor_ = prep_full
        self.fitted_models_ = {}
        for j, name in enumerate(self.names_):
            if w[j] > 1e-10:
                est = self.base_factories[name]()
                est.fit(X_full, yv)
                self.fitted_models_[name] = est
        return self

    def predict(self, X_eval: pd.DataFrame) -> np.ndarray:
        if self.weights_ is None or self.preprocessor_ is None:
            raise RuntimeError("InnerFoldNNLSEnsemble.predict() called before fit().")
        X_proc = self.preprocessor_.transform(X_eval)
        out = np.zeros(len(X_proc), dtype=float)
        for j, name in enumerate(self.names_):
            wj = float(self.weights_[j])
            if wj > 1e-10 and name in self.fitted_models_:
                out += wj * np.asarray(self.fitted_models_[name].predict(X_proc), dtype=float).reshape(-1)
        return out

    @property
    def weight_dict(self) -> dict[str, float]:
        if self.weights_ is None:
            return {}
        return {name: float(w) for name, w in zip(self.names_, self.weights_)}


def evaluate_inner_oof_nnls_ensemble(
    base_factories: Mapping[str, Callable[[], Any]],
    X: pd.DataFrame,
    y: Any,
    *,
    outer_folds: int = 4,
    inner_folds: int = 3,
    mode: WalkForwardMode = "expanding",
    steps_per_day: float = 1.0,
    max_lag: int = 0,
    max_rolling_window: int = 0,
    granularity: str = "monthly",
    min_gain_pct: float = 3.0,
    scale_features: bool = True,
    enforce_k_max: bool = True,
    seed: int = 42,
) -> NNLSEnsembleResult:
    """Evaluate NNLS ensemble with inner-fold-only weight estimation and strict ``> 3.0%`` OOS gain gate."""
    assert_no_cluster_labels_in_model_inputs(X)
    if not isinstance(X, pd.DataFrame):
        X = pd.DataFrame(X)
    yv = np.asarray(y, dtype=float).reshape(-1)
    n = int(yv.size)
    names = tuple(base_factories.keys())
    m = len(names)
    if m < 2:
        raise ValueError("evaluate_inner_oof_nnls_ensemble requires >= 2 base models.")

    wf_outer, _ = generate_walkforward_folds(
        n,
        n_folds=outer_folds,
        mode=mode,
        min_train_size=max(12, n // 4),
        steps_per_day=steps_per_day,
        max_lag=max_lag,
        max_rolling_window=max_rolling_window,
    )
    if not wf_outer:
        raise ValueError(f"Insufficient observations (n={n}) for outer walk-forward NNLS evaluation.")

    oof_ens = np.full(n, np.nan, dtype=float)
    oof_singles = np.full((n, m), np.nan, dtype=float)
    per_outer_weights: list[dict[str, float]] = []

    for wf in wf_outer:
        X_otr = X.iloc[wf.train_idx]
        y_otr = yv[wf.train_idx]
        X_ova = X.iloc[wf.val_idx]

        # 1. Fit InnerFoldNNLSEnsemble strictly on outer_train (weights learned on inner folds of outer_train ONLY)
        ens_fold = InnerFoldNNLSEnsemble(
            base_factories,
            inner_folds=inner_folds,
            inner_mode=mode,
            steps_per_day=steps_per_day,
            max_lag=max_lag,
            max_rolling_window=max_rolling_window,
            scale_features=scale_features,
            enforce_k_max=enforce_k_max,
            seed=seed,
        ).fit(X_otr, y_otr)
        per_outer_weights.append(ens_fold.weight_dict)
        oof_ens[wf.val_idx] = ens_fold.predict(X_ova)

        # 2. Evaluate each constituent on the exact same outer fold preprocessing for apples-to-apples comparison
        assert ens_fold.preprocessor_ is not None
        X_otr_proc = ens_fold.preprocessor_.transform(X_otr)
        X_ova_proc = ens_fold.preprocessor_.transform(X_ova)
        for j, name in enumerate(names):
            est_j = base_factories[name]()
            est_j.fit(X_otr_proc, y_otr)
            oof_singles[wf.val_idx, j] = np.asarray(est_j.predict(X_ova_proc), dtype=float).reshape(-1)

    valid = np.isfinite(oof_ens) & np.all(np.isfinite(oof_singles), axis=1)
    single_cvrmses: dict[str, float] = {}
    for j, name in enumerate(names):
        m_j = compute_ashrae14_oos_metrics(yv[valid], oof_singles[valid, j], granularity=granularity)
        single_cvrmses[name] = m_j.cvrmse

    best_single_name = min(single_cvrmses, key=lambda k: (round(single_cvrmses[k], 10), k))
    best_single_cv = float(single_cvrmses[best_single_name])

    final_ens = InnerFoldNNLSEnsemble(
        base_factories,
        inner_folds=inner_folds,
        inner_mode=mode,
        steps_per_day=steps_per_day,
        max_lag=max_lag,
        max_rolling_window=max_rolling_window,
        scale_features=scale_features,
        enforce_k_max=enforce_k_max,
        seed=seed,
    ).fit(X, yv)
    active_cnt = sum(1 for v in final_ens.weight_dict.values() if v > 1e-6)
    ens_metrics = compute_ashrae14_oos_metrics(
        yv[valid], oof_ens[valid], n_params=max(1, active_cnt), granularity=granularity
    )
    gain_pct = (
        100.0 * (best_single_cv - ens_metrics.cvrmse) / best_single_cv
        if best_single_cv > 1e-12
        else 0.0
    )
    # STRICT > 3.0% threshold per Gate G6 specification ("Ensembles accepted only with > 3% gain")
    accepted = bool(gain_pct > float(min_gain_pct))
    status = "accepted_ensemble" if accepted else "rejected_insufficient_ensemble_gain"
    reason = (
        f"NNLS ensemble ACCEPTED: inner-OOF simplex weights achieved {ens_metrics.cvrmse:.3f}% CV(RMSE) vs "
        f"best single {best_single_name} ({best_single_cv:.3f}%), gain={gain_pct:+.2f}% > {min_gain_pct:.2f}%."
        if accepted
        else f"NNLS ensemble REJECTED: inner-OOF simplex weights achieved {ens_metrics.cvrmse:.3f}% CV(RMSE) vs "
        f"best single {best_single_name} ({best_single_cv:.3f}%), gain={gain_pct:+.2f}% <= required {min_gain_pct:.2f}%."
    )
    return NNLSEnsembleResult(
        accepted=accepted,
        status=status,
        constituent_names=names,
        final_weights=final_ens.weight_dict,
        per_outer_fold_weights=per_outer_weights,
        oof_predictions=oof_ens,
        valid_mask=valid,
        ensemble_metrics=ens_metrics,
        best_single_name=best_single_name,
        best_single_cvrmse=best_single_cv,
        relative_cvrmse_gain_pct=gain_pct,
        required_gain_pct=float(min_gain_pct),
        k_max_constituents=final_ens.k_max_constituents_,
        fitted_solely_on_inner_oof=True,
        reason=reason,
    )
