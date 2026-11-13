"""F9 — Decomposition + regression hybrid (STL/Fourier + regressor) with transparent per-variable breakdown & optional Prophet."""

from __future__ import annotations

import importlib.util
import math
from typing import Any

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from .base import OptionalBackendSkippedError, compute_baseline_metrics, prepare_design_frame

SUPPORTED_F9_VARIANTS = ("stl_fourier", "fourier_ols", "prophet")


def _fourier_design(t_steps: np.ndarray, period: float, order: int) -> tuple[np.ndarray, list[str]]:
    cols: list[np.ndarray] = []
    names: list[str] = []
    p = max(2.0, float(period))
    for k in range(1, max(1, int(order)) + 1):
        ang = 2.0 * math.pi * float(k) * t_steps / p
        cols.append(np.sin(ang))
        cols.append(np.cos(ang))
        names.append(f"sin_{k}_p{int(round(p))}")
        names.append(f"cos_{k}_p{int(round(p))}")
    return np.column_stack(cols), names


class F9DecompositionBaseline:
    """STL/Fourier + exogenous regression hybrid providing an exact additive per-variable kWh breakdown."""

    family_id = "F9"
    family_name = "Decomposition + Exogenous Regression Hybrid"

    def __init__(
        self,
        variant: str = "stl_fourier",
        *,
        period: int = 12,
        fourier_order: int = 2,
    ) -> None:
        v = str(variant).strip().lower()
        if v not in SUPPORTED_F9_VARIANTS:
            raise ValueError(f"Unsupported F9 variant '{variant}'; expected one of {SUPPORTED_F9_VARIANTS}.")
        self.variant = v
        self.period = max(2, int(period))
        self.fourier_order = max(1, int(fourier_order))

        self.feature_names_: list[str] = []
        self.fourier_names_: list[str] = []
        self.intercept_: float = 0.0
        self.trend_slope_: float = 0.0
        self.seasonal_coef_: np.ndarray = np.empty(0, dtype=float)
        self.exog_coef_: np.ndarray = np.empty(0, dtype=float)
        self.stl_seasonal_strength_: float = 0.0
        self.train_metrics_: dict[str, float] = {}

    def fit(self, X: Any, y: Any) -> "F9DecompositionBaseline":
        if self.variant == "prophet":
            if importlib.util.find_spec("prophet") is None:
                raise OptionalBackendSkippedError(
                    "Optional extra 'prophet' is not installed — skipped cleanly per docs/not_implemented.md."
                )

        X_df, cols = prepare_design_frame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        n = int(yv.size)
        if len(X_df) != n or n < max(12, 2 * self.period):
            raise ValueError(f"F9 requires matching X/y with >= {max(12, 2 * self.period)} observations.")

        self.feature_names_ = cols
        medians = X_df.median(numeric_only=True).fillna(0.0)
        self._impute_medians_ = medians.to_dict()
        X_mat = X_df.fillna(medians).fillna(0.0).to_numpy(dtype=float)
        self._feature_means_ = X_mat.mean(axis=0)
        Xc = X_mat - self._feature_means_

        # Diagnostic STL seasonal strength on target before joint harmonic + exogenous estimation
        if self.variant == "stl_fourier" and n >= 2 * self.period:
            stl_p = self.period if self.period >= 2 else 2
            stl_res = STL(yv, period=stl_p, robust=True).fit()
            var_sr = float(np.var(stl_res.seasonal + stl_res.resid))
            self.stl_seasonal_strength_ = (
                float(max(0.0, 1.0 - float(np.var(stl_res.resid)) / var_sr)) if var_sr > 1e-12 else 0.0
            )
        else:
            self.stl_seasonal_strength_ = 0.0

        t_steps = np.arange(n, dtype=float)
        self._t_mean_ = float(np.mean(t_steps))
        t_centered = t_steps - self._t_mean_
        F_mat, f_names = _fourier_design(t_steps, self.period, self.fourier_order)
        self.fourier_names_ = f_names

        # Joint OLS design: [1, (t - t_bar), Fourier harmonics, centered exogenous predictors]
        D = np.column_stack([np.ones(n, dtype=float), t_centered, F_mat, Xc])
        beta, *_ = np.linalg.lstsq(D, yv, rcond=None)

        k_f = F_mat.shape[1]
        self.intercept_ = float(beta[0])
        self.trend_slope_ = float(beta[1])
        self.seasonal_coef_ = np.asarray(beta[2 : 2 + k_f], dtype=float)
        self.exog_coef_ = np.asarray(beta[2 + k_f :], dtype=float)
        self._n_train_ = n

        yhat = D @ beta
        self.train_metrics_ = compute_baseline_metrics(yv, yhat)
        return self

    def decompose_predict(self, X: Any, *, start_step: int = 0) -> pd.DataFrame:
        """Return exact additive per-variable breakdown summing identically to ``predicted_total``."""
        X_df, _ = prepare_design_frame(X, self.feature_names_)
        n = len(X_df)
        X_mat = X_df.fillna(self._impute_medians_).fillna(0.0).to_numpy(dtype=float)
        Xc = X_mat - self._feature_means_

        t_steps = np.arange(start_step, start_step + n, dtype=float)
        t_centered = t_steps - self._t_mean_
        F_mat, _ = _fourier_design(t_steps, self.period, self.fourier_order)

        out = pd.DataFrame(index=X_df.index)
        out["base_intercept"] = np.full(n, self.intercept_, dtype=float)
        out["trend"] = self.trend_slope_ * t_centered
        out["seasonal"] = F_mat @ self.seasonal_coef_
        for j, cname in enumerate(self.feature_names_):
            out[cname] = Xc[:, j] * float(self.exog_coef_[j])
        out["predicted_total"] = out.sum(axis=1)
        return out

    def predict(self, X: Any, *, start_step: int = 0) -> np.ndarray:
        bd = self.decompose_predict(X, start_step=start_step)
        return bd["predicted_total"].to_numpy(dtype=float)

    @property
    def n_params(self) -> int:
        return int(2 + self.seasonal_coef_.size + self.exog_coef_.size)

    def formula_text(self) -> str:
        harm = " ".join(f"{float(c):+.4g}*{nm}" for nm, c in zip(self.fourier_names_, self.seasonal_coef_))
        exog = " ".join(
            f"{float(c):+.5g}*({nm} - {float(mu):.4g})"
            for nm, c, mu in zip(self.feature_names_, self.exog_coef_, self._feature_means_)
        )
        return (
            f"y_hat(t) = {self.intercept_:+.5g} {self.trend_slope_:+.5g}*(t - {self._t_mean_:.3g}) "
            f"{harm} {exog}  [F9:{self.variant}]"
        )
