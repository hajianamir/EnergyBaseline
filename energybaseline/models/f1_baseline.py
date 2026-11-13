"""F1 — Ordinary Least Squares (OLS), Weighted Least Squares (WLS), and HAC (Newey-West) linear baselines."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats as sp_stats

from .base import compute_baseline_metrics, prepare_design_frame


class F1LinearBaseline:
    """Linear energy baseline estimator supporting OLS, feasible/explicit WLS, and Newey-West HAC inference."""

    family_id = "F1"
    family_name = "Linear Baseline (OLS / WLS / HAC)"

    def __init__(
        self,
        variant: str = "ols",
        *,
        maxlags: int | None = None,
        confidence: float = 0.95,
    ) -> None:
        v = str(variant).strip().lower()
        if v not in ("ols", "wls", "hac"):
            raise ValueError(f"Unsupported F1 variant '{variant}'; expected 'ols', 'wls', or 'hac'.")
        self.variant = v
        self.maxlags = maxlags
        self.confidence = float(np.clip(confidence, 0.50, 0.999))
        self.feature_names_: list[str] = []
        self.intercept_: float = 0.0
        self.coef_: np.ndarray = np.empty(0, dtype=float)
        self.coefficients_: dict[str, float] = {}
        self.se_: dict[str, float] = {}
        self.ci_: dict[str, tuple[float, float]] = {}
        self.p_values_: dict[str, float] = {}
        self.vcov_: np.ndarray = np.empty((0, 0), dtype=float)
        self.cov_type_: str = v
        self.maxlags_used_: int = 0
        self.df_resid_: int = 0
        self.train_metrics_: dict[str, float] = {}

    def fit(self, X: Any, y: Any, *, weights: Any | None = None) -> "F1LinearBaseline":
        X_df, cols = prepare_design_frame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        if len(X_df) != yv.size:
            raise ValueError("X and y must have identical row counts.")
        medians = X_df.median(numeric_only=True).fillna(0.0)
        X_mat = X_df.fillna(medians).fillna(0.0).to_numpy(dtype=float)
        self._impute_medians_ = medians.to_dict()
        self.feature_names_ = cols

        n, p = X_mat.shape
        k = p + 1
        if n <= k:
            raise ValueError(f"Need n > {k} observations for F1 {self.variant.upper()} regression (got n={n}).")
        D = np.column_stack([np.ones(n, dtype=float), X_mat])
        df_resid = max(1, n - k)
        self.df_resid_ = df_resid

        if self.variant == "wls":
            if weights is not None:
                w = np.asarray(weights, dtype=float).reshape(-1)
                if w.size != n or np.any(w <= 0) or not np.all(np.isfinite(w)):
                    raise ValueError("WLS weights must be positive finite values of length n.")
            else:
                # Two-stage feasible WLS: log(e^2) auxiliary skedastic regression
                b_ols, *_ = np.linalg.lstsq(D, yv, rcond=None)
                e0 = yv - D @ b_ols
                log_e2 = np.log(np.maximum(e0 * e0, 1e-8))
                gamma, *_ = np.linalg.lstsq(D, log_e2, rcond=None)
                var_hat = np.exp(np.clip(D @ gamma, -12.0, 12.0))
                w = 1.0 / np.maximum(var_hat, 1e-8)
            w = w / float(np.mean(w))
            sw = np.sqrt(w)
            Dw = D * sw[:, None]
            yw = yv * sw
            beta, *_ = np.linalg.lstsq(Dw, yw, rcond=None)
            u_w = yw - Dw @ beta
            s2 = float(np.dot(u_w, u_w)) / float(df_resid)
            xtwx_inv = np.linalg.pinv(Dw.T @ Dw, rcond=1e-12)
            vcov = s2 * xtwx_inv
            self.cov_type_ = "wls"
            self.maxlags_used_ = 0
        else:
            beta, *_ = np.linalg.lstsq(D, yv, rcond=None)
            u = yv - D @ beta
            xtx_inv = np.linalg.pinv(D.T @ D, rcond=1e-12)
            if self.variant == "ols":
                s2 = float(np.dot(u, u)) / float(df_resid)
                vcov = s2 * xtx_inv
                self.cov_type_ = "ols_homoskedastic"
                self.maxlags_used_ = 0
            else:
                # Newey-West (1987) Bartlett-kernel HAC covariance matrix with finite-sample n/(n-k) scaling
                L = (
                    int(self.maxlags)
                    if self.maxlags is not None
                    else max(1, int(math.floor(4.0 * ((float(n) / 100.0) ** (2.0 / 9.0)))))
                )
                L = min(L, max(1, n // 3))
                xu = D * u[:, None]
                meat = xu.T @ xu
                for lag in range(1, L + 1):
                    w_l = 1.0 - float(lag) / float(L + 1)
                    gamma_l = xu[lag:].T @ xu[:-lag]
                    meat = meat + w_l * (gamma_l + gamma_l.T)
                meat = (float(n) / float(df_resid)) * meat
                vcov = xtx_inv @ meat @ xtx_inv
                self.cov_type_ = f"hac_newey_west_L{L}"
                self.maxlags_used_ = L

        self.vcov_ = vcov
        self.intercept_ = float(beta[0])
        self.coef_ = np.asarray(beta[1:], dtype=float)
        se_vec = np.sqrt(np.maximum(np.diag(vcov), 1e-18))
        t_crit = float(sp_stats.t.ppf(0.5 + 0.5 * self.confidence, df=df_resid))

        names = ["intercept", *cols]
        self.coefficients_ = {nm: float(beta[idx]) for idx, nm in enumerate(names)}
        self.se_ = {nm: float(se_vec[idx]) for idx, nm in enumerate(names)}
        self.ci_ = {
            nm: (float(beta[idx] - t_crit * se_vec[idx]), float(beta[idx] + t_crit * se_vec[idx]))
            for idx, nm in enumerate(names)
        }
        self.p_values_ = {
            nm: float(2.0 * sp_stats.t.sf(abs(float(beta[idx])) / float(se_vec[idx]), df=df_resid))
            for idx, nm in enumerate(names)
        }
        self.train_metrics_ = compute_baseline_metrics(yv, D @ beta)
        return self

    def predict(self, X: Any) -> np.ndarray:
        X_df, _ = prepare_design_frame(X, self.feature_names_)
        X_mat = X_df.fillna(self._impute_medians_).fillna(0.0).to_numpy(dtype=float)
        return self.intercept_ + X_mat @ self.coef_

    @property
    def n_params(self) -> int:
        return 1 + len(self.feature_names_)

    def formula_text(self) -> str:
        terms = [f"{self.intercept_:+.6g}"] + [
            f"{float(c):+.6g}*{nm}" for nm, c in zip(self.feature_names_, self.coef_)
        ]
        return f"y_hat = {' '.join(terms)}  [{self.cov_type_}]"
