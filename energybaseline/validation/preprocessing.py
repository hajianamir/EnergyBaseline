"""Fold-encapsulated preprocessing: imputation, scaling, degree-day knots, causal lags & k_max selection (VAL-4).

Guarantees that NO statistic (median, mean, std, balance-point knot, correlation, or Lasso selector)
is ever computed outside the training indices of each CV fold, and enforces ``k_max = min(p, n // 20)``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_regression
from sklearn.linear_model import LassoCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

from ..core.features import find_temperature_col, fit_balance_points
from ..mining.clustering import assert_no_cluster_labels_in_model_inputs


def compute_k_max(p: int, n_train: int) -> int:
    """Enforce exact Gate G6 feature-selection / regularization capacity rule ``k_max = min(p, n // 20)``."""
    p_i = max(0, int(p))
    n_i = max(0, int(n_train))
    if p_i == 0 or n_i == 0:
        return 0
    return min(p_i, n_i // 20)


@dataclass(frozen=True)
class FoldPreprocessingSummary:
    """Audit snapshot of per-fold fitted preprocessing statistics."""

    n_train: int
    n_candidate_features: int
    k_max: int
    selected_features: tuple[str, ...]
    medians: dict[str, float]
    scaler_means: dict[str, float]
    scaler_scales: dict[str, float]
    heating_knot: float | None
    cooling_knot: float | None
    train_fingerprint_sha256: str


class InFoldPreprocessor:
    """Leakage-free transformer fitting imputation, DD knots, scaling, and feature selection per fold (VAL-4)."""

    def __init__(
        self,
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
        protected_prefixes: tuple[str, ...] = (),
        seed: int = 42,
    ) -> None:
        self.scale = bool(scale)
        self.fit_degree_day_knots = bool(fit_degree_day_knots)
        self.temp_col = temp_col
        self.lags = tuple(sorted({int(L) for L in lags if int(L) > 0}))
        self.lag_cols = tuple(str(c) for c in lag_cols)
        self.rolling_windows = tuple(sorted({int(w) for w in rolling_windows if int(w) > 1}))
        self.roll_cols = tuple(str(c) for c in roll_cols)
        self.selection_method = str(selection_method)
        self.enforce_k_max = bool(enforce_k_max)
        self.protected_prefixes = tuple(protected_prefixes)
        self.seed = int(seed)

        self.is_fitted_: bool = False
        self.raw_columns_: list[str] = []
        self.expanded_columns_: list[str] = []
        self.selected_features_: list[str] = []
        self.k_max_: int = 0
        self.medians_: dict[str, float] = {}
        self.balance_heat_: float | None = None
        self.balance_cool_: float | None = None
        self.resolved_temp_col_: str | None = None
        self.scaler_: StandardScaler | None = None
        self.train_fingerprint_sha256_: str = ""
        self.n_train_: int = 0

    def _expand_deterministic_features(self, X_df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=X_df.index)
        for c in self.raw_columns_:
            out[c] = pd.to_numeric(X_df[c], errors="coerce") if c in X_df.columns else np.nan

        if self.fit_degree_day_knots and self.resolved_temp_col_ and self.resolved_temp_col_ in out.columns:
            t_ser = out[self.resolved_temp_col_]
            if self.balance_heat_ is not None:
                out[f"HDD(Tb={self.balance_heat_:.1f})"] = np.maximum(0.0, self.balance_heat_ - t_ser)
            if self.balance_cool_ is not None:
                out[f"CDD(Tb={self.balance_cool_:.1f})"] = np.maximum(0.0, t_ser - self.balance_cool_)

        for c in self.lag_cols:
            if c in out.columns:
                for L in self.lags:
                    out[f"{c}_lag{L}"] = out[c].shift(L)

        for c in self.roll_cols:
            if c in out.columns:
                for w in self.rolling_windows:
                    out[f"{c}_roll{w}"] = out[c].rolling(w, min_periods=1).mean()

        return out.replace([np.inf, -np.inf], np.nan)

    def fit(self, X_train: pd.DataFrame, y_train: Any) -> "InFoldPreprocessor":
        """Fit all imputation, knot, feature-selection, and scaling parameters strictly on ``(X_train, y_train)``."""
        assert_no_cluster_labels_in_model_inputs(X_train)
        if not isinstance(X_train, pd.DataFrame):
            X_train = pd.DataFrame(X_train)
        yv = np.asarray(y_train, dtype=float).reshape(-1)
        if len(X_train) != yv.size:
            raise ValueError(f"X_train ({len(X_train)}) and y_train ({yv.size}) length mismatch.")
        n = int(yv.size)
        if n < 5:
            raise ValueError(f"InFoldPreprocessor requires >= 5 training rows, got {n}.")

        self.n_train_ = n
        self.raw_columns_ = [str(c) for c in X_train.columns]
        h = hashlib.sha256()
        h.update(np.ascontiguousarray(yv, dtype="<f8").tobytes())
        h.update(str(list(X_train.index[:5]) + list(X_train.index[-5:])).encode("utf-8"))
        self.train_fingerprint_sha256_ = h.hexdigest()

        self.balance_heat_, self.balance_cool_ = None, None
        self.resolved_temp_col_ = self.temp_col or find_temperature_col(self.raw_columns_)
        if self.fit_degree_day_knots and self.resolved_temp_col_ and self.resolved_temp_col_ in X_train.columns:
            bh, bc, _ = fit_balance_points(
                pd.to_numeric(X_train[self.resolved_temp_col_], errors="coerce"),
                pd.Series(yv, index=X_train.index),
            )
            self.balance_heat_, self.balance_cool_ = bh, bc

        expanded = self._expand_deterministic_features(X_train)
        self.expanded_columns_ = list(expanded.columns)
        self.medians_ = {
            c: (float(expanded[c].median()) if expanded[c].notna().any() else 0.0)
            for c in self.expanded_columns_
        }
        imputed = expanded.fillna(self.medians_).fillna(0.0)

        p = len(self.expanded_columns_)
        raw_k_max = compute_k_max(p, n)
        if not self.enforce_k_max:
            eff_k = p
        else:
            # For all n >= 20, raw_k_max == min(p, n // 20) >= 1 (when p >= 1); for ultra-short early folds (n < 20), keep 1 feature
            eff_k = raw_k_max if (raw_k_max >= 1 or p == 0) else 1
        self.k_max_ = raw_k_max

        if p == 0 or eff_k == 0:
            self.selected_features_ = []
        elif not self.enforce_k_max and self.selection_method == "none":
            self.selected_features_ = list(self.expanded_columns_)
        elif p <= eff_k and self.selection_method in ("auto", "none"):
            self.selected_features_ = list(self.expanded_columns_)
        else:
            std_s = imputed.std(ddof=0).replace(0.0, 1.0)
            Z = ((imputed - imputed.mean()) / std_s).fillna(0.0)
            method = self.selection_method
            if method == "auto":
                method = "lasso" if n > max(30, 6 * p) else "correlation"

            if method == "lasso" and n >= 16:
                n_splits = min(4, max(2, n // 15))
                las = LassoCV(cv=TimeSeriesSplit(n_splits=n_splits), random_state=self.seed, max_iter=10000).fit(
                    Z.to_numpy(dtype=float), yv
                )
                scores = pd.Series(np.abs(las.coef_), index=self.expanded_columns_)
                if int((scores > 1e-12).sum()) == 0:
                    scores = Z.apply(lambda col: abs(float(np.corrcoef(col.to_numpy(), yv)[0, 1])) if col.std() > 0 else 0.0).fillna(0.0)
            elif method == "mutual_info":
                scores = pd.Series(
                    mutual_info_regression(Z.to_numpy(dtype=float), yv, random_state=self.seed),
                    index=self.expanded_columns_,
                )
            else:
                y_sd = float(np.std(yv))
                if y_sd <= 1e-12:
                    scores = pd.Series(0.0, index=self.expanded_columns_)
                else:
                    scores = pd.Series(
                        {
                            c: (
                                abs(float(np.corrcoef(imputed[c].to_numpy(dtype=float), yv)[0, 1]))
                                if float(imputed[c].std(ddof=0)) > 1e-12
                                else 0.0
                            )
                            for c in self.expanded_columns_
                        }
                    ).fillna(0.0)

            # Deterministic tie-break: (-score, original_column_order)
            col_pos = {c: idx_c for idx_c, c in enumerate(self.expanded_columns_)}
            ranked = sorted(self.expanded_columns_, key=lambda c: (-round(float(scores[c]), 12), col_pos[c]))
            chosen = ranked[:eff_k]
            if self.protected_prefixes and not self.enforce_k_max:
                for c in self.expanded_columns_:
                    if c.startswith(self.protected_prefixes) and c not in chosen:
                        chosen.append(c)
            self.selected_features_ = [c for c in self.expanded_columns_ if c in set(chosen)]

        if self.scale and self.selected_features_:
            self.scaler_ = StandardScaler().fit(imputed[self.selected_features_].to_numpy(dtype=float))
        else:
            self.scaler_ = None

        self.is_fitted_ = True
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """Transform ``X`` using ONLY statistics learned during ``fit(X_train, y_train)``."""
        if not self.is_fitted_:
            raise RuntimeError("InFoldPreprocessor.transform() called before fit().")
        assert_no_cluster_labels_in_model_inputs(X)
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X, columns=self.raw_columns_)
        expanded = self._expand_deterministic_features(X)
        for c in self.expanded_columns_:
            if c not in expanded.columns:
                expanded[c] = 0.0
        imputed = expanded[self.expanded_columns_].fillna(self.medians_).fillna(0.0)
        if not self.selected_features_:
            return pd.DataFrame(index=X.index)
        sub = imputed[self.selected_features_]
        if self.scaler_ is not None:
            scaled = self.scaler_.transform(sub.to_numpy(dtype=float))
            return pd.DataFrame(scaled, index=X.index, columns=self.selected_features_)
        return sub.copy()

    def fit_transform(self, X_train: pd.DataFrame, y_train: Any) -> pd.DataFrame:
        return self.fit(X_train, y_train).transform(X_train)

    def summary(self) -> FoldPreprocessingSummary:
        if not self.is_fitted_:
            raise RuntimeError("InFoldPreprocessor has not been fitted.")
        means = (
            {c: float(m) for c, m in zip(self.selected_features_, self.scaler_.mean_)}
            if self.scaler_ is not None
            else {}
        )
        scales = (
            {c: float(s) for c, s in zip(self.selected_features_, self.scaler_.scale_)}
            if self.scaler_ is not None
            else {}
        )
        return FoldPreprocessingSummary(
            n_train=self.n_train_,
            n_candidate_features=len(self.expanded_columns_),
            k_max=self.k_max_,
            selected_features=tuple(self.selected_features_),
            medians={c: float(self.medians_[c]) for c in self.selected_features_},
            scaler_means=means,
            scaler_scales=scales,
            heating_knot=self.balance_heat_,
            cooling_knot=self.balance_cool_,
            train_fingerprint_sha256=self.train_fingerprint_sha256_,
        )
