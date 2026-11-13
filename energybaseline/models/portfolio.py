"""Phase 6 [M] / [S] / [O] Model Portfolio Wrappers and Requirement-Tier Registry (Gate G6).

Provides uniform, leakage-safe wrappers for the complete mandatory [M] portfolio:
- Mean (1P), Degree-Day (HDD/CDD), MLR (OLS+HAC), Ridge, Lasso, ElasticNet
- Piecewise ASHRAE Change-Point 1P, 2P, 3P-Cooling, 3P-Heating, 4P, 5P
- Time-of-Week & Temperature (TOWT)
- Random Forest (RF), ExtraTrees, Gradient Boosting (GB), XGBoost, LightGBM
- SVR-RBF
- MLP (tabular neural network gated by ``evaluate_nn_gate``)
- Sequence LSTM (1D temporal neural network gated by ``evaluate_nn_gate``)

Plus explicit [S] (Should) and [O] (Optional) requirement-tier status tracking across F1-F10.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import ElasticNetCV, LassoCV, LinearRegression, RidgeCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
import xgboost as xgb

from ..core.features import find_temperature_col, fit_balance_points
from ..mining.clustering import assert_no_cluster_labels_in_model_inputs
from ..validation.preprocessing import compute_k_max
from .base import NNGateDecision, SampleSizeGateSkippedError, evaluate_nn_gate
from .f2_ashrae import F2ChangePointBaseline
from .f6_neural import F6NeuralBaseline


@dataclass(frozen=True)
class PortfolioModelSpec:
    """Requirement-tier metadata for every model in the [M] / [S] / [O] portfolio."""

    key: str
    display_name: str
    family_id: str
    requirement_tier: str  # "M", "S", or "O"
    is_parametric: bool
    wrapper_class_name: str
    installed_in_lock: bool
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "family_id": self.family_id,
            "installed_in_lock": bool(self.installed_in_lock),
            "is_parametric": bool(self.is_parametric),
            "key": self.key,
            "notes": self.notes,
            "requirement_tier": self.requirement_tier,
            "wrapper_class_name": self.wrapper_class_name,
        }


class BasePortfolioWrapper:
    """Common interface across all Phase 6 [M] portfolio wrappers."""

    model_key: str = "base"
    family_id: str = "F1"
    requirement_tier: str = "M"
    is_parametric: bool = True

    def fit(self, X: pd.DataFrame, y: Any) -> "BasePortfolioWrapper":  # pragma: no cover - abstract
        raise NotImplementedError

    def predict(self, X: pd.DataFrame) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def n_params(self) -> int:
        return 1

    def formula_text(self) -> str:
        return self.model_key

    def hyperparameters(self) -> dict[str, Any]:
        return {}


def _clean_numeric_df(X: Any, cols: list[str] | None = None, medians: dict[str, float] | None = None) -> tuple[pd.DataFrame, dict[str, float]]:
    assert_no_cluster_labels_in_model_inputs(X)
    df = X.copy() if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
    if cols is not None:
        out = pd.DataFrame(index=df.index)
        for c in cols:
            out[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else np.nan
    else:
        out = pd.DataFrame(index=df.index)
        for c in df.columns:
            ser = pd.to_numeric(df[c], errors="coerce")
            if ser.notna().any():
                out[str(c)] = ser
    out = out.replace([np.inf, -np.inf], np.nan)
    if medians is None:
        med = {c: (float(out[c].median()) if out[c].notna().any() else 0.0) for c in out.columns}
    else:
        med = medians
    return out.fillna(med).fillna(0.0), med


class MeanBaselineWrapper(BasePortfolioWrapper):
    """[M] Constant 1P mean baseline wrapper."""

    model_key = "mean"
    family_id = "F1"
    requirement_tier = "M"
    is_parametric = True

    def __init__(self) -> None:
        self.mean_: float = 0.0

    def fit(self, X: pd.DataFrame, y: Any) -> "MeanBaselineWrapper":
        assert_no_cluster_labels_in_model_inputs(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.mean_ = float(np.nanmean(yv))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        assert_no_cluster_labels_in_model_inputs(X)
        return np.full(len(X), self.mean_, dtype=float)

    @property
    def n_params(self) -> int:
        return 1

    def formula_text(self) -> str:
        return f"y = {self.mean_:.6g}"


class DegreeDayBaselineWrapper(BasePortfolioWrapper):
    """[M] Degree-Day (HDD + CDD) linear baseline with balance points fitted strictly on training data."""

    model_key = "degree_day"
    family_id = "F1"
    requirement_tier = "M"
    is_parametric = True

    def __init__(self, temp_col: str | None = None) -> None:
        self.temp_col = temp_col
        self.resolved_temp_col_: str | None = None
        self.tb_heat_: float = 15.5
        self.tb_cool_: float = 18.5
        self.intercept_: float = 0.0
        self.beta_hdd_: float = 0.0
        self.beta_cdd_: float = 0.0

    def _dd_matrix(self, t: np.ndarray) -> np.ndarray:
        hdd = np.maximum(0.0, self.tb_heat_ - t)
        cdd = np.maximum(0.0, t - self.tb_cool_)
        return np.column_stack([hdd, cdd])

    def fit(self, X: pd.DataFrame, y: Any) -> "DegreeDayBaselineWrapper":
        df, med = _clean_numeric_df(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        tcol = self.temp_col if (self.temp_col and self.temp_col in df.columns) else find_temperature_col(list(df.columns))
        if tcol is None and len(df.columns) > 0:
            tcol = str(df.columns[0])
        if tcol is None:
            raise ValueError("DegreeDayBaselineWrapper requires at least one numeric temperature column.")
        self.resolved_temp_col_ = tcol
        self._temp_median = med.get(tcol, 0.0)
        t_ser = df[tcol]
        bh, bc, _ = fit_balance_points(t_ser, pd.Series(yv, index=df.index))
        t_med = float(t_ser.median())
        self.tb_heat_ = float(bh) if bh is not None else t_med - 1.5
        self.tb_cool_ = float(bc) if bc is not None else t_med + 1.5
        D = self._dd_matrix(t_ser.to_numpy(dtype=float))
        reg = LinearRegression().fit(D, yv)
        self.intercept_ = float(reg.intercept_)
        self.beta_hdd_ = float(reg.coef_[0])
        self.beta_cdd_ = float(reg.coef_[1])
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.resolved_temp_col_ is None:
            raise RuntimeError("DegreeDayBaselineWrapper not fitted.")
        df, _ = _clean_numeric_df(X, [self.resolved_temp_col_], {self.resolved_temp_col_: self._temp_median})
        D = self._dd_matrix(df[self.resolved_temp_col_].to_numpy(dtype=float))
        return self.intercept_ + self.beta_hdd_ * D[:, 0] + self.beta_cdd_ * D[:, 1]

    @property
    def n_params(self) -> int:
        return 5

    def formula_text(self) -> str:
        c = self.resolved_temp_col_ or "T"
        return (
            f"y = {self.intercept_:.6g} + ({self.beta_hdd_:.6g})*max(0, {self.tb_heat_:.2f} - {c}) "
            f"+ ({self.beta_cdd_:.6g})*max(0, {c} - {self.tb_cool_:.2f})"
        )

    def hyperparameters(self) -> dict[str, Any]:
        return {"tb_cool": self.tb_cool_, "tb_heat": self.tb_heat_, "temp_col": self.resolved_temp_col_}


class _LinearFamilyWrapper(BasePortfolioWrapper):
    """Shared base for [M] MLR, Ridge, Lasso, and ElasticNet with ``k_max = min(p, n // 20)``."""

    is_parametric = True

    def __init__(self, *, enforce_k_max: bool = True, seed: int = 42) -> None:
        self.enforce_k_max = bool(enforce_k_max)
        self.seed = int(seed)
        self.cols_: list[str] = []
        self.selected_cols_: list[str] = []
        self.medians_: dict[str, float] = {}
        self.scaler_: StandardScaler | None = None
        self.intercept_: float = 0.0
        self.coef_raw_: dict[str, float] = {}
        self.alpha_: float | None = None
        self.l1_ratio_: float | None = None

    def _select_columns(self, df: pd.DataFrame, yv: np.ndarray) -> list[str]:
        cols = list(df.columns)
        if not self.enforce_k_max or len(cols) == 0:
            return cols
        k = compute_k_max(len(cols), len(yv))
        eff_k = k if k >= 1 else min(len(cols), 1)
        if len(cols) <= eff_k:
            return cols
        corrs = {
            c: (abs(float(np.corrcoef(df[c].to_numpy(dtype=float), yv)[0, 1])) if float(df[c].std(ddof=0)) > 1e-12 else 0.0)
            for c in cols
        }
        pos = {c: idx for idx, c in enumerate(cols)}
        ranked = sorted(cols, key=lambda c: (-round(corrs[c], 12), pos[c]))
        keep = set(ranked[:eff_k])
        return [c for c in cols if c in keep]

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        if not self.selected_cols_:
            return np.full(len(df), self.intercept_, dtype=float)
        arr = df[self.selected_cols_].to_numpy(dtype=float)
        betas = np.array([self.coef_raw_[c] for c in self.selected_cols_], dtype=float)
        return self.intercept_ + arr @ betas

    @property
    def n_params(self) -> int:
        active = sum(1 for v in self.coef_raw_.values() if abs(v) > 1e-10)
        return 1 + active

    def formula_text(self) -> str:
        terms = [f"{self.intercept_:.6g}"]
        for c in self.selected_cols_:
            b = self.coef_raw_.get(c, 0.0)
            if abs(b) > 1e-10:
                terms.append(f"({b:+.6g})*{c}")
        return "y = " + " ".join(terms)


class MLRBaselineWrapper(_LinearFamilyWrapper):
    """[M] Ordinary Least Squares Multiple Linear Regression wrapper."""

    model_key = "mlr"
    family_id = "F1"
    requirement_tier = "M"

    def fit(self, X: pd.DataFrame, y: Any) -> "MLRBaselineWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.selected_cols_ = self._select_columns(df, yv)
        if not self.selected_cols_:
            self.intercept_ = float(np.mean(yv))
            self.coef_raw_ = {}
            return self
        reg = LinearRegression().fit(df[self.selected_cols_].to_numpy(dtype=float), yv)
        self.intercept_ = float(reg.intercept_)
        self.coef_raw_ = {c: float(b) for c, b in zip(self.selected_cols_, reg.coef_)}
        return self


class RidgeBaselineWrapper(_LinearFamilyWrapper):
    """[M] Ridge L2-penalized regression wrapper with temporal CV alpha selection."""

    model_key = "ridge"
    family_id = "F3"
    requirement_tier = "M"

    def fit(self, X: pd.DataFrame, y: Any) -> "RidgeBaselineWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.selected_cols_ = self._select_columns(df, yv)
        if not self.selected_cols_:
            self.intercept_ = float(np.mean(yv))
            self.coef_raw_ = {}
            return self
        sc = StandardScaler().fit(df[self.selected_cols_].to_numpy(dtype=float))
        Z = sc.transform(df[self.selected_cols_].to_numpy(dtype=float))
        tscv = TimeSeriesSplit(n_splits=min(4, max(2, len(yv) // 12)))
        reg = RidgeCV(alphas=np.logspace(-3, 3, 19), cv=tscv).fit(Z, yv)
        scale_safe = np.where(sc.scale_ > 1e-12, sc.scale_, 1.0)
        beta_raw = reg.coef_ / scale_safe
        self.intercept_ = float(reg.intercept_ - np.dot(beta_raw, sc.mean_))
        self.coef_raw_ = {c: float(b) for c, b in zip(self.selected_cols_, beta_raw)}
        self.alpha_ = float(reg.alpha_)
        return self

    def hyperparameters(self) -> dict[str, Any]:
        return {"alpha": self.alpha_}


class LassoBaselineWrapper(_LinearFamilyWrapper):
    """[M] Lasso L1-penalized regression wrapper enforcing sparsity + ``k_max``."""

    model_key = "lasso"
    family_id = "F3"
    requirement_tier = "M"

    def fit(self, X: pd.DataFrame, y: Any) -> "LassoBaselineWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.selected_cols_ = self._select_columns(df, yv)
        if not self.selected_cols_:
            self.intercept_ = float(np.mean(yv))
            self.coef_raw_ = {}
            return self
        sc = StandardScaler().fit(df[self.selected_cols_].to_numpy(dtype=float))
        Z = sc.transform(df[self.selected_cols_].to_numpy(dtype=float))
        tscv = TimeSeriesSplit(n_splits=min(4, max(2, len(yv) // 12)))
        reg = LassoCV(cv=tscv, random_state=self.seed, max_iter=12000).fit(Z, yv)
        scale_safe = np.where(sc.scale_ > 1e-12, sc.scale_, 1.0)
        beta_raw = reg.coef_ / scale_safe
        self.intercept_ = float(reg.intercept_ - np.dot(beta_raw, sc.mean_))
        self.coef_raw_ = {c: float(b) for c, b in zip(self.selected_cols_, beta_raw)}
        self.alpha_ = float(reg.alpha_)
        return self

    def hyperparameters(self) -> dict[str, Any]:
        return {"alpha": self.alpha_}


class ElasticNetBaselineWrapper(_LinearFamilyWrapper):
    """[M] ElasticNet (L1 + L2) regression wrapper with temporal CV tuning."""

    model_key = "elasticnet"
    family_id = "F3"
    requirement_tier = "M"

    def fit(self, X: pd.DataFrame, y: Any) -> "ElasticNetBaselineWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.selected_cols_ = self._select_columns(df, yv)
        if not self.selected_cols_:
            self.intercept_ = float(np.mean(yv))
            self.coef_raw_ = {}
            return self
        sc = StandardScaler().fit(df[self.selected_cols_].to_numpy(dtype=float))
        Z = sc.transform(df[self.selected_cols_].to_numpy(dtype=float))
        tscv = TimeSeriesSplit(n_splits=min(4, max(2, len(yv) // 12)))
        reg = ElasticNetCV(
            l1_ratio=[0.15, 0.5, 0.85, 0.99],
            eps=1e-5,
            cv=tscv,
            random_state=self.seed,
            max_iter=12000,
        ).fit(Z, yv)
        scale_safe = np.where(sc.scale_ > 1e-12, sc.scale_, 1.0)
        beta_raw = reg.coef_ / scale_safe
        self.intercept_ = float(reg.intercept_ - np.dot(beta_raw, sc.mean_))
        self.coef_raw_ = {c: float(b) for c, b in zip(self.selected_cols_, beta_raw)}
        self.alpha_ = float(reg.alpha_)
        self.l1_ratio_ = float(reg.l1_ratio_)
        return self

    def hyperparameters(self) -> dict[str, Any]:
        return {"alpha": self.alpha_, "l1_ratio": self.l1_ratio_}


class Piecewise1PTo5PWrapper(BasePortfolioWrapper):
    """[M] ASHRAE Guideline 14 Change-Point family wrapper supporting 1P, 2P, 3P-cooling, 3P-heating, 4P, 5P, or auto-BIC."""

    model_key = "piecewise_1p_5p"
    family_id = "F2"
    requirement_tier = "M"
    is_parametric = True

    KIND_MAP = {
        "auto": "auto",
        "1P": "1P",
        "2P": "2P",
        "3PC": "3P-cooling",
        "3P-COOLING": "3P-cooling",
        "3PH": "3P-heating",
        "3P-HEATING": "3P-heating",
        "4P": "4P",
        "5P": "5P",
    }

    def __init__(self, kind: str = "auto", *, temp_col: str | None = None, seed: int = 42) -> None:
        k_key = "auto" if str(kind).lower() == "auto" else str(kind).upper()
        if k_key not in self.KIND_MAP:
            raise ValueError(f"Unsupported piecewise kind {kind!r}; expected one of {tuple(self.KIND_MAP)}.")
        self.kind = self.KIND_MAP[k_key]
        self.temp_col = temp_col
        self.seed = int(seed)
        self.model_: F2ChangePointBaseline | None = None
        self.mean_1p_: float = 0.0
        self.selected_kind_: str = self.kind

    def fit(self, X: pd.DataFrame, y: Any) -> "Piecewise1PTo5PWrapper":
        df, _ = _clean_numeric_df(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        if self.kind == "1P" or df.shape[1] == 0:
            self.mean_1p_ = float(np.mean(yv))
            self.selected_kind_ = "1P"
            self.model_ = None
            return self

        tcol = self.temp_col if (self.temp_col and self.temp_col in df.columns) else find_temperature_col(list(df.columns))
        if tcol is None:
            tcol = str(df.columns[0])

        if self.kind != "auto":
            self.model_ = F2ChangePointBaseline(
                self.kind, temp_col=tcol, n_grid=25, n_boot_knots=8, seed=self.seed
            ).fit(df, yv)
            self.selected_kind_ = self.kind
            return self

        n = len(yv)
        sse_1p = max(float(np.sum((yv - np.mean(yv)) ** 2)), 1e-12)
        best_bic = n * np.log(sse_1p / n) + 1.0 * np.log(max(n, 2))
        best_m: F2ChangePointBaseline | None = None
        best_k = "1P"
        for k_cand in ("2P", "3P-cooling", "3P-heating", "4P", "5P"):
            try:
                cand = F2ChangePointBaseline(
                    k_cand, temp_col=tcol, n_grid=20, n_boot_knots=4, seed=self.seed
                ).fit(df, yv)
                pred = cand.predict(df)
                sse = max(float(np.sum((yv - pred) ** 2)), 1e-12)
                bic = n * np.log(sse / n) + cand.n_params * np.log(max(n, 2))
                if bic < best_bic:
                    best_bic = bic
                    best_m = cand
                    best_k = k_cand
            except Exception:
                continue
        if best_m is None:
            self.mean_1p_ = float(np.mean(yv))
            self.selected_kind_ = "1P"
            self.model_ = None
        else:
            self.model_ = best_m
            self.selected_kind_ = best_k
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        df, _ = _clean_numeric_df(X)
        if self.selected_kind_ == "1P" or self.model_ is None:
            return np.full(len(df), self.mean_1p_, dtype=float)
        return np.asarray(self.model_.predict(df), dtype=float).reshape(-1)

    @property
    def n_params(self) -> int:
        if self.selected_kind_ == "1P" or self.model_ is None:
            return 1
        return int(self.model_.n_params)

    def formula_text(self) -> str:
        if self.selected_kind_ == "1P" or self.model_ is None:
            return f"1P: y = {self.mean_1p_:.6g}"
        return self.model_.formula_text()

    def hyperparameters(self) -> dict[str, Any]:
        if self.model_ is None:
            return {"selected_kind": "1P"}
        return {
            "knots": [float(k) for k in getattr(self.model_, "knots_", ())],
            "selected_kind": self.selected_kind_,
            "temp_col": getattr(self.model_, "temp_col_", None),
        }


class TOWTBaselineWrapper(BasePortfolioWrapper):
    """[M] Time-of-Week and Piecewise Temperature (TOWT) baseline wrapper."""

    model_key = "towt"
    family_id = "F2"
    requirement_tier = "M"
    is_parametric = True

    def __init__(self, *, temp_col: str | None = None, n_temp_bins: int = 5, alpha: float = 1.0) -> None:
        self.temp_col = temp_col
        self.n_temp_bins = max(2, int(n_temp_bins))
        self.alpha = float(alpha)
        self.resolved_temp_col_: str | None = None
        self.knots_: list[float] = []
        self.schedule_buckets_: list[int] = []
        self.schedule_mode_: str = "none"
        self.extra_cols_: list[str] = []
        self.medians_: dict[str, float] = {}
        self.design_cols_: list[str] = []
        self.intercept_: float = 0.0
        self.coef_raw_: dict[str, float] = {}

    def _schedule_codes(self, X_df: pd.DataFrame) -> tuple[np.ndarray | None, str]:
        if isinstance(X_df.index, pd.DatetimeIndex):
            diffs = X_df.index.to_series().diff().dropna()
            med_sec = float(diffs.dt.total_seconds().median()) if len(diffs) else 86400.0
            if med_sec <= 7200.0:
                return (X_df.index.dayofweek.to_numpy(dtype=int) * 24 + X_df.index.hour.to_numpy(dtype=int)), "tow_168"
            if med_sec <= 172800.0:
                return X_df.index.dayofweek.to_numpy(dtype=int), "dow_7"
            return X_df.index.month.to_numpy(dtype=int), "month_12"
        if "dow" in X_df.columns and "hour" in X_df.columns:
            return (X_df["dow"].astype(int).to_numpy() * 24 + X_df["hour"].astype(int).to_numpy()), "tow_168"
        if "dow" in X_df.columns:
            return X_df["dow"].astype(int).to_numpy(), "dow_7"
        return None, "temp_spline_only"

    def _build_design(self, df: pd.DataFrame, raw_X: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        codes, _ = self._schedule_codes(raw_X)
        if codes is not None and self.schedule_buckets_:
            for b in self.schedule_buckets_:
                out[f"sched_{b}"] = (codes == b).astype(float)
        if self.resolved_temp_col_ and self.resolved_temp_col_ in df.columns:
            T = df[self.resolved_temp_col_].to_numpy(dtype=float)
            out[self.resolved_temp_col_] = T
            for k in self.knots_:
                out[f"towt_hinge_{k:.2f}"] = np.maximum(0.0, T - k)
        for c in self.extra_cols_:
            if c in df.columns:
                out[c] = df[c].to_numpy(dtype=float)
        return out

    def fit(self, X: pd.DataFrame, y: Any) -> "TOWTBaselineWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        tcol = self.temp_col if (self.temp_col and self.temp_col in df.columns) else find_temperature_col(list(df.columns))
        if tcol is None and len(df.columns) > 0:
            tcol = str(df.columns[0])
        self.resolved_temp_col_ = tcol
        if tcol:
            qs = np.linspace(0.2, 0.8, max(2, self.n_temp_bins - 1))
            self.knots_ = [float(v) for v in np.unique(np.quantile(df[tcol].to_numpy(dtype=float), qs))]
        else:
            self.knots_ = []
        codes, mode = self._schedule_codes(X if isinstance(X, pd.DataFrame) else df)
        self.schedule_mode_ = mode
        self.schedule_buckets_ = sorted(set(codes.tolist()))[1:] if codes is not None else []
        self.extra_cols_ = [c for c in df.columns if c != tcol and c not in ("dow", "hour")][:3]

        D = self._build_design(df, X if isinstance(X, pd.DataFrame) else df)
        self.design_cols_ = list(D.columns)
        if not self.design_cols_:
            self.intercept_ = float(np.mean(yv))
            self.coef_raw_ = {}
            return self
        from sklearn.linear_model import Ridge

        reg = Ridge(alpha=self.alpha).fit(D.to_numpy(dtype=float), yv)
        self.intercept_ = float(reg.intercept_)
        self.coef_raw_ = {c: float(b) for c, b in zip(self.design_cols_, reg.coef_)}
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        df, _ = _clean_numeric_df(X, list(self.medians_.keys()), self.medians_)
        if not self.design_cols_:
            return np.full(len(df), self.intercept_, dtype=float)
        D = self._build_design(df, X if isinstance(X, pd.DataFrame) else df)
        for c in self.design_cols_:
            if c not in D.columns:
                D[c] = 0.0
        betas = np.array([self.coef_raw_[c] for c in self.design_cols_], dtype=float)
        return self.intercept_ + D[self.design_cols_].to_numpy(dtype=float) @ betas

    @property
    def n_params(self) -> int:
        return 1 + len(self.design_cols_)

    def formula_text(self) -> str:
        return (
            f"TOWT(mode={self.schedule_mode_}, n_schedule_bins={len(self.schedule_buckets_)}, "
            f"n_temp_knots={len(self.knots_)}, alpha={self.alpha})"
        )

    def hyperparameters(self) -> dict[str, Any]:
        return {"alpha": self.alpha, "mode": self.schedule_mode_, "n_temp_bins": self.n_temp_bins}


class _NonParametricTabularWrapper(BasePortfolioWrapper):
    """Shared base for non-parametric [M] tree/kernel wrappers."""

    is_parametric = False

    def __init__(self, *, seed: int = 42) -> None:
        self.seed = int(seed)
        self.cols_: list[str] = []
        self.medians_: dict[str, float] = {}
        self.est_: Any = None


class RandomForestWrapper(_NonParametricTabularWrapper):
    """[M] Random Forest Regressor wrapper."""

    model_key = "rf"
    family_id = "F5"
    requirement_tier = "M"

    def __init__(self, *, n_estimators: int = 160, max_depth: int = 6, min_samples_leaf: int = 2, seed: int = 42) -> None:
        super().__init__(seed=seed)
        self.n_estimators = int(n_estimators)
        self.max_depth = int(max_depth)
        self.min_samples_leaf = int(min_samples_leaf)

    def fit(self, X: pd.DataFrame, y: Any) -> "RandomForestWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.est_ = RandomForestRegressor(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf,
            random_state=self.seed,
            n_jobs=1,
        ).fit(df.to_numpy(dtype=float), yv)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        return np.asarray(self.est_.predict(df.to_numpy(dtype=float)), dtype=float).reshape(-1)

    @property
    def n_params(self) -> int:
        return max(8, len(self.cols_) * 4)

    def formula_text(self) -> str:
        return f"RandomForestRegressor(n_estimators={self.n_estimators}, max_depth={self.max_depth})"

    def hyperparameters(self) -> dict[str, Any]:
        return {"max_depth": self.max_depth, "min_samples_leaf": self.min_samples_leaf, "n_estimators": self.n_estimators}


class ExtraTreesWrapper(_NonParametricTabularWrapper):
    """[M] Extremely Randomized Trees (ExtraTrees) Regressor wrapper."""

    model_key = "extratrees"
    family_id = "F5"
    requirement_tier = "M"

    def __init__(self, *, n_estimators: int = 160, max_depth: int = 7, min_samples_leaf: int = 2, seed: int = 42) -> None:
        super().__init__(seed=seed)
        self.n_estimators = int(n_estimators)
        self.max_depth = int(max_depth)
        self.min_samples_leaf = int(min_samples_leaf)

    def fit(self, X: pd.DataFrame, y: Any) -> "ExtraTreesWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.est_ = ExtraTreesRegressor(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf,
            random_state=self.seed,
            n_jobs=1,
        ).fit(df.to_numpy(dtype=float), yv)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        return np.asarray(self.est_.predict(df.to_numpy(dtype=float)), dtype=float).reshape(-1)

    @property
    def n_params(self) -> int:
        return max(8, len(self.cols_) * 4)

    def formula_text(self) -> str:
        return f"ExtraTreesRegressor(n_estimators={self.n_estimators}, max_depth={self.max_depth})"

    def hyperparameters(self) -> dict[str, Any]:
        return {"max_depth": self.max_depth, "min_samples_leaf": self.min_samples_leaf, "n_estimators": self.n_estimators}


class GradientBoostingWrapper(_NonParametricTabularWrapper):
    """[M] Scikit-learn GradientBoostingRegressor (GB) wrapper."""

    model_key = "gb"
    family_id = "F6"
    requirement_tier = "M"

    def __init__(
        self,
        *,
        n_estimators: int = 140,
        learning_rate: float = 0.05,
        max_depth: int = 3,
        subsample: float = 0.85,
        seed: int = 42,
    ) -> None:
        super().__init__(seed=seed)
        self.n_estimators = int(n_estimators)
        self.learning_rate = float(learning_rate)
        self.max_depth = int(max_depth)
        self.subsample = float(subsample)

    def fit(self, X: pd.DataFrame, y: Any) -> "GradientBoostingWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.est_ = GradientBoostingRegressor(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            subsample=self.subsample,
            random_state=self.seed,
        ).fit(df.to_numpy(dtype=float), yv)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        return np.asarray(self.est_.predict(df.to_numpy(dtype=float)), dtype=float).reshape(-1)

    @property
    def n_params(self) -> int:
        return max(10, len(self.cols_) * 4)

    def formula_text(self) -> str:
        return f"GradientBoostingRegressor(n_estimators={self.n_estimators}, lr={self.learning_rate}, max_depth={self.max_depth})"

    def hyperparameters(self) -> dict[str, Any]:
        return {
            "learning_rate": self.learning_rate,
            "max_depth": self.max_depth,
            "n_estimators": self.n_estimators,
            "subsample": self.subsample,
        }


class XGBoostWrapper(_NonParametricTabularWrapper):
    """[M] XGBoost gradient boosted decision trees wrapper."""

    model_key = "xgboost"
    family_id = "F6"
    requirement_tier = "M"

    def __init__(
        self,
        *,
        n_estimators: int = 140,
        learning_rate: float = 0.05,
        max_depth: int = 4,
        subsample: float = 0.85,
        reg_lambda: float = 1.0,
        seed: int = 42,
    ) -> None:
        super().__init__(seed=seed)
        self.n_estimators = int(n_estimators)
        self.learning_rate = float(learning_rate)
        self.max_depth = int(max_depth)
        self.subsample = float(subsample)
        self.reg_lambda = float(reg_lambda)

    def fit(self, X: pd.DataFrame, y: Any) -> "XGBoostWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.est_ = xgb.XGBRegressor(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            subsample=self.subsample,
            reg_lambda=self.reg_lambda,
            random_state=self.seed,
            n_jobs=1,
            verbosity=0,
        ).fit(df.to_numpy(dtype=float), yv)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        return np.asarray(self.est_.predict(df.to_numpy(dtype=float)), dtype=float).reshape(-1)

    @property
    def n_params(self) -> int:
        return max(12, len(self.cols_) * 5)

    def formula_text(self) -> str:
        return f"XGBRegressor(n_estimators={self.n_estimators}, lr={self.learning_rate}, max_depth={self.max_depth})"

    def hyperparameters(self) -> dict[str, Any]:
        return {
            "learning_rate": self.learning_rate,
            "max_depth": self.max_depth,
            "n_estimators": self.n_estimators,
            "reg_lambda": self.reg_lambda,
            "subsample": self.subsample,
        }


class LightGBMWrapper(_NonParametricTabularWrapper):
    """[M] LightGBM histogram gradient boosted decision trees wrapper."""

    model_key = "lightgbm"
    family_id = "F6"
    requirement_tier = "M"

    def __init__(
        self,
        *,
        n_estimators: int = 140,
        learning_rate: float = 0.05,
        num_leaves: int = 15,
        min_child_samples: int = 5,
        seed: int = 42,
    ) -> None:
        super().__init__(seed=seed)
        self.n_estimators = int(n_estimators)
        self.learning_rate = float(learning_rate)
        self.num_leaves = int(num_leaves)
        self.min_child_samples = int(min_child_samples)

    def fit(self, X: pd.DataFrame, y: Any) -> "LightGBMWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        mcs = max(2, min(self.min_child_samples, max(2, len(yv) // 8)))
        self.est_ = lgb.LGBMRegressor(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            num_leaves=self.num_leaves,
            min_child_samples=mcs,
            random_state=self.seed,
            n_jobs=1,
            verbose=-1,
        ).fit(df, yv)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        return np.asarray(self.est_.predict(df), dtype=float).reshape(-1)

    @property
    def n_params(self) -> int:
        return max(12, len(self.cols_) * 5)

    def formula_text(self) -> str:
        return f"LGBMRegressor(n_estimators={self.n_estimators}, lr={self.learning_rate}, num_leaves={self.num_leaves})"

    def hyperparameters(self) -> dict[str, Any]:
        return {
            "learning_rate": self.learning_rate,
            "min_child_samples": self.min_child_samples,
            "n_estimators": self.n_estimators,
            "num_leaves": self.num_leaves,
        }


class SVRRBFWrapper(_NonParametricTabularWrapper):
    """[M] Radial-Basis-Function Support Vector Regression wrapper with in-wrapper feature + target scaling."""

    model_key = "svr_rbf"
    family_id = "F5"
    requirement_tier = "M"

    def __init__(self, *, C: float = 10.0, epsilon: float = 0.05, gamma: str | float = "scale") -> None:
        super().__init__(seed=42)
        self.C = float(C)
        self.epsilon = float(epsilon)
        self.gamma = gamma
        self.x_scaler_: StandardScaler | None = None
        self.y_mean_: float = 0.0
        self.y_std_: float = 1.0

    def fit(self, X: pd.DataFrame, y: Any) -> "SVRRBFWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        self.x_scaler_ = StandardScaler().fit(df.to_numpy(dtype=float))
        Z = self.x_scaler_.transform(df.to_numpy(dtype=float))
        self.y_mean_ = float(np.mean(yv))
        self.y_std_ = float(np.std(yv)) or 1.0
        yz = (yv - self.y_mean_) / self.y_std_
        self.est_ = SVR(kernel="rbf", C=self.C, epsilon=self.epsilon, gamma=self.gamma).fit(Z, yz)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        assert self.x_scaler_ is not None
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        Z = self.x_scaler_.transform(df.to_numpy(dtype=float))
        yz_hat = np.asarray(self.est_.predict(Z), dtype=float).reshape(-1)
        return self.y_mean_ + self.y_std_ * yz_hat

    @property
    def n_params(self) -> int:
        n_sv = len(getattr(self.est_, "support_", []))
        return max(4, n_sv)

    def formula_text(self) -> str:
        n_sv = len(getattr(self.est_, "support_", []))
        return f"SVR(kernel='rbf', C={self.C}, epsilon={self.epsilon}, n_support={n_sv})"

    def hyperparameters(self) -> dict[str, Any]:
        return {"C": self.C, "epsilon": self.epsilon, "gamma": self.gamma, "kernel": "rbf"}


class MLPWrapper(_NonParametricTabularWrapper):
    """[M] Multi-Layer Perceptron wrapper gated by ``evaluate_nn_gate``."""

    model_key = "mlp"
    family_id = "F9"
    requirement_tier = "M"

    def __init__(
        self,
        *,
        hidden_layer_sizes: tuple[int, ...] = (32, 16),
        alpha: float = 0.05,
        min_samples: int = 50,
        seed: int = 42,
    ) -> None:
        super().__init__(seed=seed)
        self.hidden_layer_sizes = tuple(int(h) for h in hidden_layer_sizes)
        self.alpha = float(alpha)
        self.min_samples = int(min_samples)
        self.x_scaler_: StandardScaler | None = None
        self.y_mean_: float = 0.0
        self.y_std_: float = 1.0
        self.gate_decision_: NNGateDecision | None = None

    def fit(self, X: pd.DataFrame, y: Any) -> "MLPWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        gate = evaluate_nn_gate("MLP", n_obs=len(yv), n_features=max(1, len(self.cols_)), min_samples=self.min_samples)
        self.gate_decision_ = gate
        if not gate.accepted:
            raise SampleSizeGateSkippedError(gate.reason)

        self.x_scaler_ = StandardScaler().fit(df.to_numpy(dtype=float))
        Z = self.x_scaler_.transform(df.to_numpy(dtype=float))
        self.y_mean_ = float(np.mean(yv))
        self.y_std_ = float(np.std(yv)) or 1.0
        yz = (yv - self.y_mean_) / self.y_std_
        self.est_ = MLPRegressor(
            hidden_layer_sizes=self.hidden_layer_sizes,
            alpha=self.alpha,
            max_iter=700,
            early_stopping=len(yv) >= 80,
            random_state=self.seed,
        ).fit(Z, yz)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        assert self.x_scaler_ is not None
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        Z = self.x_scaler_.transform(df.to_numpy(dtype=float))
        yz_hat = np.asarray(self.est_.predict(Z), dtype=float).reshape(-1)
        return self.y_mean_ + self.y_std_ * yz_hat

    @property
    def n_params(self) -> int:
        p = max(1, len(self.cols_))
        h0 = self.hidden_layer_sizes[0]
        h1 = self.hidden_layer_sizes[1] if len(self.hidden_layer_sizes) > 1 else 1
        return p * h0 + h0 * h1 + h1 + 1

    def formula_text(self) -> str:
        return f"MLPRegressor(hidden_layer_sizes={self.hidden_layer_sizes}, alpha={self.alpha})"

    def hyperparameters(self) -> dict[str, Any]:
        return {"alpha": self.alpha, "hidden_layer_sizes": list(self.hidden_layer_sizes), "min_samples": self.min_samples}


class SequenceLSTMWrapper(_NonParametricTabularWrapper):
    """[M] PyTorch CPU 1D Sequence LSTM wrapper gated by ``evaluate_nn_gate``."""

    model_key = "lstm"
    family_id = "F6"
    requirement_tier = "M"

    def __init__(
        self,
        *,
        seq_len: int = 6,
        hidden_size: int = 24,
        epochs: int = 60,
        min_samples: int = 60,
        force_lite_profile: bool = False,
        seed: int = 42,
    ) -> None:
        super().__init__(seed=seed)
        self.seq_len = int(seq_len)
        self.hidden_size = int(hidden_size)
        self.epochs = int(epochs)
        self.min_samples = int(min_samples)
        self.force_lite_profile = bool(force_lite_profile)
        self.gate_decision_: NNGateDecision | None = None
        self._delegate: F6NeuralBaseline | None = None

    def fit(self, X: pd.DataFrame, y: Any) -> "SequenceLSTMWrapper":
        df, self.medians_ = _clean_numeric_df(X)
        self.cols_ = list(df.columns)
        yv = np.asarray(y, dtype=float).reshape(-1)
        gate = evaluate_nn_gate("LSTM", n_obs=len(yv), n_features=max(1, len(self.cols_)), min_samples=self.min_samples)
        self.gate_decision_ = gate
        if not gate.accepted:
            raise SampleSizeGateSkippedError(gate.reason)

        self._delegate = F6NeuralBaseline(
            "lstm",
            min_samples=20,
            hidden_sizes=(self.hidden_size,),
            seq_len=self.seq_len,
            max_epochs=self.epochs,
            force_lite_profile=self.force_lite_profile,
            seed=self.seed,
        ).fit(df, yv)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._delegate is None:
            raise RuntimeError("SequenceLSTMWrapper.predict() called before fit().")
        df, _ = _clean_numeric_df(X, self.cols_, self.medians_)
        return np.asarray(self._delegate.predict(df), dtype=float).reshape(-1)

    @property
    def n_params(self) -> int:
        if self._delegate is not None:
            return int(self._delegate.n_params)
        p = max(1, len(self.cols_))
        h = self.hidden_size
        return 4 * h * (p + h + 1) + h + 1

    def formula_text(self) -> str:
        return f"SequenceLSTM(seq_len={self.seq_len}, hidden_size={self.hidden_size}, epochs={self.epochs})"

    def hyperparameters(self) -> dict[str, Any]:
        return {
            "epochs": self.epochs,
            "hidden_size": self.hidden_size,
            "min_samples": self.min_samples,
            "seq_len": self.seq_len,
        }


def get_portfolio_registry() -> list[PortfolioModelSpec]:
    """Return the complete [M] / [S] / [O] model portfolio registry with backend availability."""
    has_prophet = importlib.util.find_spec("prophet") is not None
    has_pf = importlib.util.find_spec("pytorch_forecasting") is not None
    return [
        # Mandatory [M] portfolio (16 explicit model families/wrappers)
        PortfolioModelSpec("mean", "1P Constant Mean", "F1", "M", True, "MeanBaselineWrapper", True, "ASHRAE 1P baseline"),
        PortfolioModelSpec("degree_day", "HDD + CDD Degree-Day OLS", "F1", "M", True, "DegreeDayBaselineWrapper", True, "Variable-base degree-day linear model"),
        PortfolioModelSpec("mlr", "OLS Multiple Linear Regression + HAC", "F1", "M", True, "MLRBaselineWrapper", True, "Closed-form parametric OLS + Newey-West SEs"),
        PortfolioModelSpec("ridge", "Ridge Regression (L2)", "F3", "M", True, "RidgeBaselineWrapper", True, "Temporal-CV L2 shrinkage with k_max"),
        PortfolioModelSpec("lasso", "Lasso Regression (L1)", "F3", "M", True, "LassoBaselineWrapper", True, "Temporal-CV L1 sparse selection with k_max"),
        PortfolioModelSpec("elasticnet", "ElasticNet Regression (L1+L2)", "F3", "M", True, "ElasticNetBaselineWrapper", True, "Temporal-CV ElasticNet with k_max"),
        PortfolioModelSpec("piecewise_1p_5p", "ASHRAE Piecewise Change-Point (1P-5P)", "F2", "M", True, "Piecewise1PTo5PWrapper", True, "1P, 2P, 3PC, 3PH, 4P, 5P continuous change-point"),
        PortfolioModelSpec("towt", "Time-of-Week & Piecewise Temperature (TOWT)", "F2", "M", True, "TOWTBaselineWrapper", True, "LBNL TOWT occupancy + piecewise temperature"),
        PortfolioModelSpec("rf", "Random Forest Regressor", "F5", "M", False, "RandomForestWrapper", True, "Bagged CART ensemble"),
        PortfolioModelSpec("extratrees", "ExtraTrees Regressor", "F5", "M", False, "ExtraTreesWrapper", True, "Extremely randomized trees ensemble"),
        PortfolioModelSpec("gb", "Gradient Boosting Regressor (GB)", "F6", "M", False, "GradientBoostingWrapper", True, "Scikit-learn stagewise additive trees"),
        PortfolioModelSpec("xgboost", "XGBoost Regressor", "F6", "M", False, "XGBoostWrapper", True, "Second-order regularized GBDT"),
        PortfolioModelSpec("lightgbm", "LightGBM Regressor", "F6", "M", False, "LightGBMWrapper", True, "Leaf-wise histogram GBDT"),
        PortfolioModelSpec("svr_rbf", "Support Vector Regression (RBF)", "F5", "M", False, "SVRRBFWrapper", True, "Standardized kernel SVR-RBF"),
        PortfolioModelSpec("mlp", "Multi-Layer Perceptron (MLP)", "F9", "M", False, "MLPWrapper", True, "Bidirectionally sample-gated feedforward NN"),
        PortfolioModelSpec("lstm", "Sequence LSTM (1D Temporal NN)", "F9", "M", False, "SequenceLSTMWrapper", True, "Bidirectionally sample-gated PyTorch CPU LSTM"),
        # Should [S] portfolio
        PortfolioModelSpec("catboost", "CatBoost Regressor", "F6", "S", False, "CatBoostRegressor", True, "Symmetric oblivious GBDT (F6)"),
        PortfolioModelSpec("huber_quantile", "Huber & Quantile Robust Regression", "F3", "S", True, "HuberRegressor/QuantileRegressor", True, "Outlier-resistant linear estimators (F3)"),
        PortfolioModelSpec("gam_spline", "Penalized B-Spline Additive GAM", "F4", "S", True, "SplineTransformer+RidgeCV", True, "Smooth nonlinear thermal response (F4)"),
        PortfolioModelSpec("sarimax_arima", "SARIMAX / Dynamic ARX", "F7", "S", True, "SARIMAX", True, "Autoregressive exogenous state-space (F7)"),
        PortfolioModelSpec("stl_fourier", "STL + Fourier Harmonic Regression", "F8", "S", True, "STL+HarmonicOLS", True, "Explicit seasonal decomposition (F8)"),
        PortfolioModelSpec("regime_segmented", "Exogenous Regime-Segmented Linear", "F2", "S", True, "RegimeSegmented", True, "Calendar/driver-only segmented OLS (MT-5 safe)"),
        PortfolioModelSpec("nnls_ensemble", "Inner-OOF NNLS Ensemble (>3% Gate)", "F10", "S", False, "InnerFoldNNLSEnsemble", True, "Simplex NNLS meta-learner on inner OOF only"),
        # Optional [O] portfolio
        PortfolioModelSpec("prophet_optional", "Prophet Decomposable Trend", "F8", "O", False, "OptionalPlugin", has_prophet, "Optional backend; skipped cleanly when uninstalled"),
        PortfolioModelSpec("tft_patchtst_optional", "TFT / PatchTST Deep Horizon Forecasters", "F9", "O", False, "OptionalPlugin", has_pf, "Optional backend; skipped cleanly when uninstalled"),
    ]


MANDATORY_PORTFOLIO_CLASSES: dict[str, type[BasePortfolioWrapper]] = {
    "mean": MeanBaselineWrapper,
    "degree_day": DegreeDayBaselineWrapper,
    "mlr": MLRBaselineWrapper,
    "ridge": RidgeBaselineWrapper,
    "lasso": LassoBaselineWrapper,
    "elasticnet": ElasticNetBaselineWrapper,
    "piecewise_1p_5p": Piecewise1PTo5PWrapper,
    "towt": TOWTBaselineWrapper,
    "rf": RandomForestWrapper,
    "extratrees": ExtraTreesWrapper,
    "gb": GradientBoostingWrapper,
    "xgboost": XGBoostWrapper,
    "lightgbm": LightGBMWrapper,
    "svr_rbf": SVRRBFWrapper,
    "mlp": MLPWrapper,
    "lstm": SequenceLSTMWrapper,
}
