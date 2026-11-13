"""F7 — Dynamic regression & classical time-series models: SARIMAX / ARIMAX with exogenous drivers + ETS / Holt-Winters."""

from __future__ import annotations

from typing import Any
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

from .base import compute_baseline_metrics, prepare_design_frame

SUPPORTED_F7_VARIANTS = ("arimax", "sarimax", "ets")


class F7TimeSeriesBaseline:
    """Classical time-series and dynamic exogenous baseline supporting ARIMAX, SARIMAX, and ETS/Holt-Winters."""

    family_id = "F7"
    family_name = "Dynamic Regression & Time-Series (SARIMAX / ETS)"

    def __init__(
        self,
        variant: str = "sarimax",
        *,
        order: tuple[int, int, int] = (1, 0, 0),
        seasonal_order: tuple[int, int, int, int] = (0, 0, 0, 0),
        seasonal_periods: int = 12,
        trend: str | None = "add",
        seasonal: str | None = "add",
    ) -> None:
        v = str(variant).strip().lower()
        if v not in SUPPORTED_F7_VARIANTS:
            raise ValueError(f"Unsupported F7 variant '{variant}'; expected one of {SUPPORTED_F7_VARIANTS}.")
        self.variant = v
        self.order = tuple(int(x) for x in order)
        self.seasonal_order = tuple(int(x) for x in seasonal_order) if v == "sarimax" else (0, 0, 0, 0)
        self.seasonal_periods = max(2, int(seasonal_periods))
        self.trend = trend
        self.seasonal = seasonal

        self.feature_names_: list[str] = []
        self.exog_params_: dict[str, float] = {}
        self.n_params_: int = 0
        self.train_metrics_: dict[str, float] = {}

    def fit(self, X: Any | None, y: Any) -> "F7TimeSeriesBaseline":
        yv = np.asarray(y, dtype=float).reshape(-1)
        n = int(yv.size)
        if n < max(16, 2 * self.seasonal_periods + 2):
            raise ValueError(f"F7 {self.variant} requires >= {max(16, 2 * self.seasonal_periods + 2)} observations.")

        if X is not None:
            X_df, cols = prepare_design_frame(X)
            if len(X_df) != n:
                raise ValueError("X and y must have identical row counts.")
            medians = X_df.median(numeric_only=True).fillna(0.0)
            self._impute_medians_ = medians.to_dict()
            X_mat: np.ndarray | None = X_df.fillna(medians).fillna(0.0).to_numpy(dtype=float)
            self.feature_names_ = cols
        else:
            X_mat = None
            self.feature_names_ = []
            self._impute_medians_ = {}

        self._n_train_ = n
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if self.variant in ("arimax", "sarimax"):
                mod = SARIMAX(
                    yv,
                    exog=X_mat,
                    order=self.order,
                    seasonal_order=self.seasonal_order,
                    trend="c",
                    enforce_stationarity=False,
                    enforce_invertibility=False,
                )
                res = mod.fit(disp=False, maxiter=120)
                self._sarimax_res_ = res
                pnames = list(mod.param_names)
                pvals = np.asarray(res.params, dtype=float)
                self.exog_params_ = {}
                for idx_c, cname in enumerate(self.feature_names_):
                    key = f"x{idx_c + 1}"
                    if key in pnames:
                        self.exog_params_[cname] = float(pvals[pnames.index(key)])
                self.n_params_ = int(pvals.size)
                yhat = np.asarray(res.fittedvalues, dtype=float).reshape(-1)
            else:
                # ETS / Holt-Winters: extract exogenous linear component first when predictors supplied, then fit ETS
                if X_mat is not None and X_mat.shape[1] > 0:
                    D = np.column_stack([np.ones(n, dtype=float), X_mat])
                    beta, *_ = np.linalg.lstsq(D, yv, rcond=None)
                    self._ets_intercept_ = float(beta[0])
                    self._ets_coef_ = np.asarray(beta[1:], dtype=float)
                    self.exog_params_ = {c: float(v) for c, v in zip(self.feature_names_, self._ets_coef_)}
                    exog_part = X_mat @ self._ets_coef_
                    y_target_ets = yv - exog_part
                else:
                    self._ets_intercept_ = 0.0
                    self._ets_coef_ = np.empty(0, dtype=float)
                    self.exog_params_ = {}
                    exog_part = np.zeros(n, dtype=float)
                    y_target_ets = yv

                ets_mod = ExponentialSmoothing(
                    y_target_ets,
                    trend=self.trend,
                    seasonal=self.seasonal,
                    seasonal_periods=self.seasonal_periods,
                    initialization_method="estimated",
                )
                ets_res = ets_mod.fit(optimized=True)
                self._ets_res_ = ets_res
                self.n_params_ = int(len(ets_res.params) + self._ets_coef_.size)
                yhat = np.asarray(ets_res.fittedvalues, dtype=float).reshape(-1) + exog_part

        self.train_metrics_ = compute_baseline_metrics(yv, yhat)
        return self

    def predict(self, X: Any | None = None, *, steps: int | None = None, in_sample: bool = False) -> np.ndarray:
        if X is not None:
            X_df, _ = prepare_design_frame(X, self.feature_names_ if self.feature_names_ else None)
            X_mat: np.ndarray | None = (
                X_df.fillna(self._impute_medians_).fillna(0.0).to_numpy(dtype=float)
                if self.feature_names_
                else None
            )
            h = len(X_df)
        else:
            X_mat = None
            h = int(steps if steps is not None else self._n_train_)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if self.variant in ("arimax", "sarimax"):
                if in_sample and h == self._n_train_:
                    return np.asarray(self._sarimax_res_.fittedvalues, dtype=float).reshape(-1)
                fc = self._sarimax_res_.get_forecast(steps=h, exog=X_mat)
                return np.asarray(fc.predicted_mean, dtype=float).reshape(-1)

            if in_sample and h == self._n_train_:
                base_pred = np.asarray(self._ets_res_.fittedvalues, dtype=float).reshape(-1)
            else:
                base_pred = np.asarray(self._ets_res_.forecast(steps=h), dtype=float).reshape(-1)
            if X_mat is not None and self._ets_coef_.size > 0:
                return base_pred + X_mat @ self._ets_coef_
            return base_pred

    def predict_series(self, X: pd.DataFrame, *, in_sample: bool = False) -> pd.Series:
        return pd.Series(self.predict(X, in_sample=in_sample), index=X.index, name=f"F7_{self.variant}")

    @property
    def n_params(self) -> int:
        return self.n_params_

    def formula_text(self) -> str:
        ex = ", ".join(f"{k}:{v:+.4g}" for k, v in self.exog_params_.items())
        if self.variant in ("arimax", "sarimax"):
            return f"{self.variant.upper()}(order={self.order}, seasonal={self.seasonal_order}, exog=[{ex}])"
        return f"ETS(trend={self.trend}, seasonal={self.seasonal}, m={self.seasonal_periods}, exog=[{ex}])"
