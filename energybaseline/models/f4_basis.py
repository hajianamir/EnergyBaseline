"""F4 — Polynomial, B-spline / GAM-like additive smooth regressions, and interaction-term linear baselines."""

from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import PolynomialFeatures, SplineTransformer

from .base import compute_baseline_metrics, prepare_design_frame

SUPPORTED_F4_VARIANTS = ("polynomial", "spline", "gam_spline", "interaction")


class F4BasisBaseline:
    """Basis-expansion baseline supporting polynomial, B-spline / additive GAM-like splines, and pairwise interactions."""

    family_id = "F4"
    family_name = "Basis Expansion & Interactions (Poly / Spline / GAM)"

    def __init__(
        self,
        variant: str = "gam_spline",
        *,
        degree: int = 2,
        n_knots: int = 5,
        alpha: float = 1e-2,
    ) -> None:
        v = str(variant).strip().lower()
        if v not in SUPPORTED_F4_VARIANTS:
            raise ValueError(f"Unsupported F4 variant '{variant}'; expected one of {SUPPORTED_F4_VARIANTS}.")
        self.variant = v
        self.degree = max(2, int(degree))
        self.n_knots = max(3, int(n_knots))
        self.alpha = float(max(0.0, alpha))

        self.feature_names_: list[str] = []
        self.basis_names_: list[str] = []
        self.intercept_: float = 0.0
        self.coef_: np.ndarray = np.empty(0, dtype=float)
        self.coefficients_: dict[str, float] = {}
        self.train_metrics_: dict[str, float] = {}

    def _transform_basis(self, X_mat: np.ndarray, *, fit_mode: bool) -> np.ndarray:
        if self.variant == "polynomial":
            if fit_mode:
                self._mu_ = X_mat.mean(axis=0)
                self._poly_ = PolynomialFeatures(degree=self.degree, include_bias=False)
                Z = self._poly_.fit_transform(X_mat - self._mu_)
                self.basis_names_ = list(self._poly_.get_feature_names_out(self.feature_names_))
                return Z
            return self._poly_.transform(X_mat - self._mu_)

        if self.variant == "interaction":
            cols_list = [X_mat[:, j] for j in range(X_mat.shape[1])]
            names = list(self.feature_names_)
            if fit_mode:
                self._mu_ = X_mat.mean(axis=0)
            Xc = X_mat - self._mu_
            for i, j in combinations(range(X_mat.shape[1]), 2):
                cols_list.append(Xc[:, i] * Xc[:, j])
                names.append(f"{self.feature_names_[i]}*{self.feature_names_[j]}")
            if fit_mode:
                self.basis_names_ = names
            return np.column_stack(cols_list)

        # 'spline' or 'gam_spline': additive per-feature B-spline smooths
        if fit_mode:
            self._splines_: list[SplineTransformer | None] = []
            self._col_slices_: dict[str, slice] = {}
            blocks: list[np.ndarray] = []
            bnames: list[str] = []
            cursor = 0
            for j, cname in enumerate(self.feature_names_):
                col = X_mat[:, [j]]
                if np.unique(col).size >= 4:
                    sp = SplineTransformer(
                        n_knots=min(self.n_knots, max(3, np.unique(col).size // 2)),
                        degree=min(3, self.degree),
                        include_bias=False,
                        extrapolation="linear",
                    )
                    B = sp.fit_transform(col)
                    self._splines_.append(sp)
                    k_b = B.shape[1]
                    bnames.extend([f"s({cname})_{m}" for m in range(k_b)])
                else:
                    self._splines_.append(None)
                    B = col
                    k_b = 1
                    bnames.append(cname)
                self._col_slices_[cname] = slice(cursor, cursor + k_b)
                cursor += k_b
                blocks.append(B)
            self.basis_names_ = bnames
            return np.hstack(blocks)

        blocks_pred: list[np.ndarray] = []
        for j, sp in enumerate(self._splines_):
            col = X_mat[:, [j]]
            blocks_pred.append(sp.transform(col) if sp is not None else col)
        return np.hstack(blocks_pred)

    def fit(self, X: Any, y: Any) -> "F4BasisBaseline":
        X_df, cols = prepare_design_frame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        if len(X_df) != yv.size or yv.size < 10:
            raise ValueError("F4 requires matching X/y with >= 10 observations.")
        self.feature_names_ = cols
        medians = X_df.median(numeric_only=True).fillna(0.0)
        self._impute_medians_ = medians.to_dict()
        X_mat = X_df.fillna(medians).fillna(0.0).to_numpy(dtype=float)

        B = self._transform_basis(X_mat, fit_mode=True)
        if self.alpha > 0.0:
            est = Ridge(alpha=self.alpha, random_state=42)
            est.fit(B, yv)
            self.intercept_ = float(est.intercept_)
            self.coef_ = np.asarray(est.coef_, dtype=float).reshape(-1)
        else:
            D = np.column_stack([np.ones(B.shape[0], dtype=float), B])
            beta, *_ = np.linalg.lstsq(D, yv, rcond=None)
            self.intercept_ = float(beta[0])
            self.coef_ = np.asarray(beta[1:], dtype=float)

        self.coefficients_ = {"intercept": self.intercept_, **{nm: float(c) for nm, c in zip(self.basis_names_, self.coef_)}}
        self.train_metrics_ = compute_baseline_metrics(yv, self.intercept_ + B @ self.coef_)
        return self

    def predict(self, X: Any) -> np.ndarray:
        X_df, _ = prepare_design_frame(X, self.feature_names_)
        X_mat = X_df.fillna(self._impute_medians_).fillna(0.0).to_numpy(dtype=float)
        B = self._transform_basis(X_mat, fit_mode=False)
        return self.intercept_ + B @ self.coef_

    def partial_effect(self, feature: str, grid: np.ndarray) -> np.ndarray:
        """Evaluate additive GAM-spline smooth contribution f_j(x_j) along ``grid``."""
        if self.variant not in ("spline", "gam_spline") or feature not in self._col_slices_:
            raise ValueError(f"partial_effect requires fitted spline/gam_spline variant and valid feature '{feature}'.")
        j = self.feature_names_.index(feature)
        sp = self._splines_[j]
        sl = self._col_slices_[feature]
        col = np.asarray(grid, dtype=float).reshape(-1, 1)
        B_j = sp.transform(col) if sp is not None else col
        return (B_j @ self.coef_[sl]).reshape(-1)

    @property
    def n_params(self) -> int:
        return 1 + int(self.coef_.size)

    def formula_text(self) -> str:
        shown = [f"{float(c):+.5g}*{nm}" for nm, c in list(zip(self.basis_names_, self.coef_))[:10]]
        extra = f" + ... ({len(self.basis_names_) - 10} more terms)" if len(self.basis_names_) > 10 else ""
        return f"y_hat = {self.intercept_:+.5g} {' '.join(shown)}{extra}  [F4:{self.variant}]"
