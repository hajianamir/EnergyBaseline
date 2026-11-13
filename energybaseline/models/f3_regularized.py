"""F3 — Regularized & robust linear models: Ridge, Lasso, ElasticNet, HuberRegressor, QuantileRegressor."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
from sklearn.linear_model import ElasticNet, HuberRegressor, Lasso, QuantileRegressor, Ridge

from .base import compute_baseline_metrics, prepare_design_frame

SUPPORTED_F3_VARIANTS = ("ridge", "lasso", "elasticnet", "huber", "quantile")


def compute_regularization_path(
    X: Any,
    y: Any,
    *,
    variant: str = "lasso",
    alphas: Sequence[float] | None = None,
    l1_ratio: float = 0.5,
) -> dict[str, Any]:
    """Compute standardized coefficient trajectories across a grid of penalty strengths ``alphas``."""
    v = str(variant).strip().lower()
    if v not in ("ridge", "lasso", "elasticnet"):
        raise ValueError(f"Regularization path supports 'ridge', 'lasso', or 'elasticnet' (got '{variant}').")
    X_df, cols = prepare_design_frame(X)
    yv = np.asarray(y, dtype=float).reshape(-1)
    X_mat = X_df.fillna(X_df.median(numeric_only=True)).fillna(0.0).to_numpy(dtype=float)
    mu = X_mat.mean(axis=0)
    sd = np.where(X_mat.std(axis=0, ddof=0) > 1e-12, X_mat.std(axis=0, ddof=0), 1.0)
    Z = (X_mat - mu) / sd
    alpha_grid = np.asarray(alphas if alphas is not None else np.logspace(-3, 2, 12), dtype=float)
    coefs = np.zeros((alpha_grid.size, len(cols)), dtype=float)
    nonzero_counts: list[int] = []
    for i, a in enumerate(alpha_grid):
        if v == "ridge":
            est = Ridge(alpha=float(a), random_state=42)
        elif v == "lasso":
            est = Lasso(alpha=float(a), max_iter=5000, random_state=42)
        else:
            est = ElasticNet(alpha=float(a), l1_ratio=float(l1_ratio), max_iter=5000, random_state=42)
        est.fit(Z, yv)
        c = np.asarray(est.coef_, dtype=float) / sd
        coefs[i, :] = c
        nonzero_counts.append(int(np.sum(np.abs(c) > 1e-7)))
    return {
        "alphas": alpha_grid.tolist(),
        "coef_matrix": coefs,
        "feature_names": cols,
        "nonzero_counts": nonzero_counts,
        "variant": v,
    }


class F3RegularizedBaseline:
    """Regularized (L2/L1/ElasticNet) and outlier-robust (Huber/Quantile) linear energy baseline."""

    family_id = "F3"
    family_name = "Regularized & Robust Linear"

    def __init__(
        self,
        variant: str = "ridge",
        *,
        alpha: float = 1.0,
        l1_ratio: float = 0.5,
        epsilon: float = 1.35,
        quantile: float = 0.5,
        seed: int = 42,
    ) -> None:
        v = str(variant).strip().lower()
        if v not in SUPPORTED_F3_VARIANTS:
            raise ValueError(f"Unsupported F3 variant '{variant}'; expected one of {SUPPORTED_F3_VARIANTS}.")
        self.variant = v
        self.alpha = float(max(0.0, alpha))
        self.l1_ratio = float(np.clip(l1_ratio, 0.01, 0.99))
        self.epsilon = float(max(1.01, epsilon))
        self.quantile = float(np.clip(quantile, 0.05, 0.95))
        self.seed = int(seed)

        self.feature_names_: list[str] = []
        self.intercept_: float = 0.0
        self.coef_: np.ndarray = np.empty(0, dtype=float)
        self.coefficients_: dict[str, float] = {}
        self.train_metrics_: dict[str, float] = {}

    def fit(self, X: Any, y: Any) -> "F3RegularizedBaseline":
        X_df, cols = prepare_design_frame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        if len(X_df) != yv.size or yv.size < 8:
            raise ValueError("F3 requires matching X/y with >= 8 rows.")
        self.feature_names_ = cols
        medians = X_df.median(numeric_only=True).fillna(0.0)
        self._impute_medians_ = medians.to_dict()
        X_mat = X_df.fillna(medians).fillna(0.0).to_numpy(dtype=float)

        mu = X_mat.mean(axis=0)
        sd = np.where(X_mat.std(axis=0, ddof=0) > 1e-12, X_mat.std(axis=0, ddof=0), 1.0)
        Z = (X_mat - mu) / sd
        self._mu_, self._sd_ = mu, sd

        if self.variant == "ridge":
            est = Ridge(alpha=self.alpha, random_state=self.seed)
        elif self.variant == "lasso":
            est = Lasso(alpha=max(self.alpha, 1e-8), max_iter=6000, random_state=self.seed)
        elif self.variant == "elasticnet":
            est = ElasticNet(alpha=max(self.alpha, 1e-8), l1_ratio=self.l1_ratio, max_iter=6000, random_state=self.seed)
        elif self.variant == "huber":
            est = HuberRegressor(alpha=self.alpha, epsilon=self.epsilon, max_iter=1000)
        else:
            est = QuantileRegressor(quantile=self.quantile, alpha=self.alpha, solver="highs")

        est.fit(Z, yv)
        self._estimator_ = est
        coef_z = np.asarray(est.coef_, dtype=float).reshape(-1)
        coef_phys = coef_z / sd
        intercept_phys = float(est.intercept_) - float(np.dot(mu, coef_phys))

        self.coef_ = coef_phys
        self.intercept_ = intercept_phys
        self.coefficients_ = {"intercept": intercept_phys, **{c: float(v) for c, v in zip(cols, coef_phys)}}
        self.train_metrics_ = compute_baseline_metrics(yv, intercept_phys + X_mat @ coef_phys)
        return self

    def predict(self, X: Any) -> np.ndarray:
        X_df, _ = prepare_design_frame(X, self.feature_names_)
        X_mat = X_df.fillna(self._impute_medians_).fillna(0.0).to_numpy(dtype=float)
        return self.intercept_ + X_mat @ self.coef_

    @property
    def n_params(self) -> int:
        return 1 + int(np.sum(np.abs(self.coef_) > 1e-9))

    def formula_text(self) -> str:
        terms = [f"{self.intercept_:+.6g}"] + [
            f"{float(v):+.6g}*{c}" for c, v in zip(self.feature_names_, self.coef_) if abs(float(v)) > 1e-10
        ]
        return f"y_hat = {' '.join(terms)}  [F3:{self.variant}]"
