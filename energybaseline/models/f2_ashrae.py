"""F2 — ASHRAE Guideline 14 / ISO 50006 piecewise change-point baselines (2P, 3P-cooling, 3P-heating, 4P, 5P) & HDD/CDD."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .base import compute_baseline_metrics, prepare_design_frame

SUPPORTED_F2_TYPES = ("2P", "3P-cooling", "3P-heating", "4P", "5P")


def compute_variable_base_degree_days(
    temperature: Any,
    *,
    heating_base: float = 15.5,
    cooling_base: float = 18.5,
) -> pd.DataFrame:
    """Compute variable-base Heating Degree-Days (HDD) and Cooling Degree-Days (CDD) from outdoor temperature."""
    t_arr = np.asarray(temperature, dtype=float).reshape(-1)
    idx = temperature.index if isinstance(temperature, (pd.Series, pd.DataFrame)) else pd.RangeIndex(t_arr.size)
    hdd = np.maximum(0.0, float(heating_base) - t_arr)
    cdd = np.maximum(0.0, t_arr - float(cooling_base))
    return pd.DataFrame(
        {
            f"HDD_{heating_base:g}": hdd,
            f"CDD_{cooling_base:g}": cdd,
            "hdd": hdd,
            "cdd": cdd,
        },
        index=idx,
    )


def _piecewise_basis(temp: np.ndarray, model_type: str, knots: tuple[float, ...], extra: np.ndarray | None) -> np.ndarray:
    n = temp.size
    cols = [np.ones(n, dtype=float)]
    if model_type == "2P":
        cols.append(temp)
    elif model_type == "3P-cooling":
        cols.append(np.maximum(0.0, temp - knots[0]))
    elif model_type == "3P-heating":
        cols.append(np.maximum(0.0, knots[0] - temp))
    elif model_type == "4P":
        # Continuous 2-slope model parameterized via (T_b - T)_+ and (T - T_b)_+
        cols.append(np.maximum(0.0, knots[0] - temp))
        cols.append(np.maximum(0.0, temp - knots[0]))
    elif model_type == "5P":
        t_bh, t_bc = knots[0], knots[1]
        cols.append(np.maximum(0.0, t_bh - temp))
        cols.append(np.maximum(0.0, temp - t_bc))
    else:
        raise ValueError(f"Unknown ASHRAE change-point type '{model_type}'.")
    if extra is not None and extra.shape[1] > 0:
        cols.append(extra)
    return np.column_stack(cols)


class F2ChangePointBaseline:
    """ASHRAE Guideline 14 change-point energy baseline with knot uncertainty CIs (AC-30)."""

    family_id = "F2"
    family_name = "ASHRAE Change-Point / Variable-Base Degree-Day"

    def __init__(
        self,
        model_type: str = "5P",
        *,
        temp_col: str | None = None,
        confidence: float = 0.95,
        n_grid: int = 45,
        n_boot_knots: int = 40,
        seed: int = 42,
    ) -> None:
        if model_type not in SUPPORTED_F2_TYPES:
            raise ValueError(f"Unsupported F2 model_type '{model_type}'; expected one of {SUPPORTED_F2_TYPES}.")
        self.model_type = model_type
        self.temp_col = temp_col
        self.confidence = float(np.clip(confidence, 0.50, 0.999))
        self.n_grid = max(15, int(n_grid))
        self.n_boot_knots = max(0, int(n_boot_knots))
        self.seed = int(seed)

        self.feature_names_: list[str] = []
        self.temp_col_: str = ""
        self.extra_cols_: list[str] = []
        self.knots_: tuple[float, ...] = ()
        self.knot_ci_: dict[str, tuple[float, float]] = {}
        self.beta_: np.ndarray = np.empty(0, dtype=float)
        self.coefficients_: dict[str, float] = {}
        self.train_metrics_: dict[str, float] = {}

    def _fit_knots_and_beta(
        self,
        temp: np.ndarray,
        yv: np.ndarray,
        extra: np.ndarray | None,
    ) -> tuple[tuple[float, ...], np.ndarray]:
        q10, q90 = float(np.quantile(temp, 0.10)), float(np.quantile(temp, 0.90))
        if q90 - q10 < 1e-6:
            q10, q90 = float(np.min(temp)), float(np.max(temp)) + 1e-3

        def _rss_for_knots(kn: tuple[float, ...]) -> tuple[float, np.ndarray]:
            D = _piecewise_basis(temp, self.model_type, kn, extra)
            b, *_ = np.linalg.lstsq(D, yv, rcond=None)
            # Enforce physical non-negativity on 3P/5P heating/cooling degree-day slopes during knot search
            if self.model_type in ("3P-cooling", "3P-heating"):
                b[1] = max(0.0, float(b[1]))
            elif self.model_type == "5P":
                b[1] = max(0.0, float(b[1]))
                b[2] = max(0.0, float(b[2]))
            r = yv - D @ b
            return float(np.dot(r, r)), b

        if self.model_type == "2P":
            _, b = _rss_for_knots(())
            return (), b

        grid = np.linspace(q10, q90, self.n_grid)
        if self.model_type in ("3P-cooling", "3P-heating", "4P"):
            best_rss = float("inf")
            best_k = float(np.median(temp))
            for kb in grid:
                rss, _ = _rss_for_knots((float(kb),))
                if rss < best_rss:
                    best_rss, best_k = rss, float(kb)
            opt = minimize(
                lambda z: _rss_for_knots((float(z[0]),))[0],
                x0=np.array([best_k]),
                bounds=[(q10, q90)],
                method="L-BFGS-B",
            )
            k_hat = float(np.clip(opt.x[0], q10, q90))
            _, b_hat = _rss_for_knots((k_hat,))
            return (k_hat,), b_hat

        # 5P: two knots t_bh <= t_bc
        g5 = np.linspace(q10, q90, max(16, self.n_grid // 2))
        best_rss = float("inf")
        best_pair = (float(g5[len(g5) // 3]), float(g5[(2 * len(g5)) // 3]))
        for i, kh in enumerate(g5[:-1]):
            for kc in g5[i:]:
                rss, _ = _rss_for_knots((float(kh), float(kc)))
                if rss < best_rss:
                    best_rss, best_pair = rss, (float(kh), float(kc))

        # Refine parameterized as (kh, delta >= 0) where kc = kh + delta
        def _obj5(z: np.ndarray) -> float:
            kh = float(np.clip(z[0], q10, q90))
            kc = float(np.clip(kh + max(0.0, float(z[1])), kh, q90))
            return _rss_for_knots((kh, kc))[0]

        opt5 = minimize(
            _obj5,
            x0=np.array([best_pair[0], max(0.0, best_pair[1] - best_pair[0])]),
            bounds=[(q10, q90), (0.0, max(1e-3, q90 - q10))],
            method="Nelder-Mead",
        )
        kh_hat = float(np.clip(opt5.x[0], q10, q90))
        kc_hat = float(np.clip(kh_hat + max(0.0, float(opt5.x[1])), kh_hat, q90))
        _, b_hat = _rss_for_knots((kh_hat, kc_hat))
        return (kh_hat, kc_hat), b_hat

    def fit(self, X: Any, y: Any) -> "F2ChangePointBaseline":
        X_df, cols = prepare_design_frame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        if len(X_df) != yv.size or yv.size < 12:
            raise ValueError(f"F2 {self.model_type} requires matching X/y with >= 12 observations.")
        self.feature_names_ = cols
        medians = X_df.median(numeric_only=True).fillna(0.0)
        self._impute_medians_ = medians.to_dict()
        clean = X_df.fillna(medians).fillna(0.0)

        t_col = self.temp_col
        if t_col is None:
            for c in cols:
                cl = c.lower()
                if any(tok in cl for tok in ("temp", "oat", "drybulb", "t_out", "ambient")):
                    t_col = c
                    break
            if t_col is None:
                t_col = cols[0]
        if t_col not in clean.columns:
            raise KeyError(f"Temperature column '{t_col}' not found in X.")
        self.temp_col_ = t_col
        self.extra_cols_ = [c for c in cols if c != t_col]

        temp = clean[t_col].to_numpy(dtype=float)
        extra = clean[self.extra_cols_].to_numpy(dtype=float) if self.extra_cols_ else None

        knots, beta = self._fit_knots_and_beta(temp, yv, extra)
        self.knots_ = knots
        self.beta_ = beta

        yhat = _piecewise_basis(temp, self.model_type, knots, extra) @ beta
        resid = yv - yhat

        # Residual-bootstrap knot uncertainty (AC-30)
        alpha_tail = 0.5 * (1.0 - self.confidence)
        if knots and self.n_boot_knots >= 8:
            rng = np.random.default_rng(self.seed)
            boot_knots: list[tuple[float, ...]] = []
            rc = resid - float(np.mean(resid))
            for _ in range(self.n_boot_knots):
                yb = yhat + rng.choice(rc, size=rc.size, replace=True)
                kb, _ = self._fit_knots_and_beta(temp, yb, extra)
                boot_knots.append(kb)
            arr_k = np.asarray(boot_knots, dtype=float)
            if len(knots) == 1:
                self.knot_ci_ = {
                    "balance_point": (
                        float(np.quantile(arr_k[:, 0], alpha_tail)),
                        float(np.quantile(arr_k[:, 0], 1.0 - alpha_tail)),
                    )
                }
            else:
                self.knot_ci_ = {
                    "heating_balance_point": (
                        float(np.quantile(arr_k[:, 0], alpha_tail)),
                        float(np.quantile(arr_k[:, 0], 1.0 - alpha_tail)),
                    ),
                    "cooling_balance_point": (
                        float(np.quantile(arr_k[:, 1], alpha_tail)),
                        float(np.quantile(arr_k[:, 1], 1.0 - alpha_tail)),
                    ),
                }
        else:
            self.knot_ci_ = {}

        coef_names = ["base_load"]
        if self.model_type == "2P":
            coef_names.append("temp_slope")
        elif self.model_type == "3P-cooling":
            coef_names.append("cooling_slope")
        elif self.model_type == "3P-heating":
            coef_names.append("heating_slope")
        elif self.model_type == "4P":
            coef_names.extend(["left_heating_slope", "right_cooling_slope"])
        elif self.model_type == "5P":
            coef_names.extend(["heating_slope", "cooling_slope"])
        coef_names.extend(self.extra_cols_)
        self.coefficients_ = {nm: float(beta[i]) for i, nm in enumerate(coef_names)}
        self.train_metrics_ = compute_baseline_metrics(yv, yhat)
        return self

    def predict(self, X: Any) -> np.ndarray:
        X_df, _ = prepare_design_frame(X, self.feature_names_)
        clean = X_df.fillna(self._impute_medians_).fillna(0.0)
        temp = clean[self.temp_col_].to_numpy(dtype=float)
        extra = clean[self.extra_cols_].to_numpy(dtype=float) if self.extra_cols_ else None
        return _piecewise_basis(temp, self.model_type, self.knots_, extra) @ self.beta_

    @property
    def n_params(self) -> int:
        return int(self.beta_.size + len(self.knots_))

    def formula_text(self) -> str:
        t = self.temp_col_
        b = self.beta_
        if self.model_type == "2P":
            core = f"{b[0]:+.5g} {b[1]:+.5g}*{t}"
        elif self.model_type == "3P-cooling":
            core = f"{b[0]:+.5g} {b[1]:+.5g}*max(0, {t} - {self.knots_[0]:.3f})"
        elif self.model_type == "3P-heating":
            core = f"{b[0]:+.5g} {b[1]:+.5g}*max(0, {self.knots_[0]:.3f} - {t})"
        elif self.model_type == "4P":
            core = f"{b[0]:+.5g} {b[1]:+.5g}*max(0, {self.knots_[0]:.3f} - {t}) {b[2]:+.5g}*max(0, {t} - {self.knots_[0]:.3f})"
        else:
            core = (
                f"{b[0]:+.5g} {b[1]:+.5g}*max(0, {self.knots_[0]:.3f} - {t}) "
                f"{b[2]:+.5g}*max(0, {t} - {self.knots_[1]:.3f})"
            )
        offset = 1 + (1 if self.model_type in ("2P", "3P-cooling", "3P-heating") else 2)
        extras = [f"{b[offset + j]:+.5g}*{c}" for j, c in enumerate(self.extra_cols_)]
        return f"y_hat = {' '.join([core, *extras])}  [ASHRAE {self.model_type}]"
