"""F10 — Constrained convex stacking / weighted ensemble of top diverse candidate families."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ..core.models import make_folds
from .base import align_oof_predictions, compute_baseline_metrics, prepare_design_frame
from .f1_baseline import F1LinearBaseline
from .f3_regularized import F3RegularizedBaseline
from .f4_basis import F4BasisBaseline
from .f5_ensemble import F5EnsembleBaseline


def solve_simplex_stacking_weights(
    P_oof: np.ndarray,
    y_oof: np.ndarray,
    *,
    family_ids: Sequence[str] | None = None,
    diversity_bonus: float = 1e-4,
) -> np.ndarray:
    """Solve min_w ||y - P w||^2 + penalty on probability simplex {w >= 0, sum(w) == 1}."""
    P = np.asarray(P_oof, dtype=float)
    yv = np.asarray(y_oof, dtype=float).reshape(-1)
    if P.ndim != 2 or P.shape[0] != yv.size or P.shape[1] < 1:
        raise ValueError("Invalid OOF matrix shape for convex stacking.")
    m = P.shape[1]
    if m == 1:
        return np.array([1.0], dtype=float)

    # Mild L2 regularization when multiple candidates share identical family_id to encourage cross-family diversity
    same_fam = np.eye(m, dtype=float)
    if family_ids is not None and len(family_ids) == m:
        for i in range(m):
            for j in range(m):
                if family_ids[i] == family_ids[j]:
                    same_fam[i, j] = 1.0
    scale = max(float(np.var(yv)), 1e-8)
    reg = float(max(0.0, diversity_bonus)) * scale

    def _obj(w: np.ndarray) -> tuple[float, np.ndarray]:
        r = P @ w - yv
        loss = float(np.dot(r, r)) / float(yv.size) + reg * float(w @ same_fam @ w)
        grad = (2.0 / float(yv.size)) * (P.T @ r) + 2.0 * reg * (same_fam @ w)
        return loss, grad

    w0 = np.full(m, 1.0 / float(m), dtype=float)
    res = minimize(
        lambda w: _obj(w)[0],
        w0,
        jac=lambda w: _obj(w)[1],
        method="SLSQP",
        bounds=[(0.0, 1.0)] * m,
        constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0), "jac": lambda w: np.ones(m, dtype=float)}],
        options={"ftol": 1e-12, "maxiter": 500},
    )
    w = np.clip(np.asarray(res.x, dtype=float), 0.0, 1.0)
    s = float(np.sum(w))
    w = w / s if s > 1e-12 else w0
    return w


class F10ConvexStackingBaseline:
    """Out-of-fold constrained convex stacking ensemble over diverse baseline model families."""

    family_id = "F10"
    family_name = "Constrained Convex Stacking Ensemble"

    def __init__(
        self,
        candidates: Sequence[tuple[str, str, Callable[[], Any]]] | None = None,
        *,
        cv_folds: int = 4,
        gap: int = 1,
        diversity_bonus: float = 1e-4,
        seed: int = 42,
    ) -> None:
        self.candidates = list(candidates) if candidates is not None else None
        self.cv_folds = max(2, int(cv_folds))
        self.gap = max(0, int(gap))
        self.diversity_bonus = float(max(0.0, diversity_bonus))
        self.seed = int(seed)

        self.feature_names_: list[str] = []
        self.candidate_names_: list[str] = []
        self.candidate_families_: list[str] = []
        self.weights_: np.ndarray = np.empty(0, dtype=float)
        self.weight_map_: dict[str, float] = {}
        self.fitted_models_: list[Any] = []
        self.oof_metrics_: dict[str, float] = {}
        self.train_metrics_: dict[str, float] = {}

    def _default_candidates(self) -> list[tuple[str, str, Callable[[], Any]]]:
        return [
            ("F1_OLS", "F1", lambda: F1LinearBaseline("ols")),
            ("F3_Huber", "F3", lambda: F3RegularizedBaseline("huber", alpha=0.05, seed=self.seed)),
            ("F4_Spline", "F4", lambda: F4BasisBaseline("gam_spline", n_knots=5, alpha=1e-2)),
            ("F5_RandomForest", "F5", lambda: F5EnsembleBaseline("random_forest", n_estimators=80, max_depth=5, seed=self.seed)),
        ]

    def fit_from_oof(
        self,
        oof_predictions: Mapping[str, pd.Series | np.ndarray],
        y_true: pd.Series | np.ndarray,
        *,
        family_map: Mapping[str, str] | None = None,
    ) -> "F10ConvexStackingBaseline":
        """Fit simplex weights directly from pre-computed candidate OOF predictions."""
        _, y_aligned, aligned_preds = align_oof_predictions(oof_predictions, y_true)
        names = list(aligned_preds.keys())
        fams = [str((family_map or {}).get(nm, nm.split("_")[0])) for nm in names]
        P = np.column_stack([aligned_preds[nm] for nm in names])
        w = solve_simplex_stacking_weights(P, y_aligned, family_ids=fams, diversity_bonus=self.diversity_bonus)
        self.candidate_names_ = names
        self.candidate_families_ = fams
        self.weights_ = w
        self.weight_map_ = {nm: float(w[i]) for i, nm in enumerate(names)}
        self.oof_metrics_ = compute_baseline_metrics(y_aligned, P @ w)
        return self

    def fit(self, X: Any, y: Any) -> "F10ConvexStackingBaseline":
        X_df, cols = prepare_design_frame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        n = int(yv.size)
        if len(X_df) != n or n < 24:
            raise ValueError("F10 convex stacking requires matching X/y with >= 24 observations.")
        self.feature_names_ = cols
        specs = self.candidates if self.candidates is not None else self._default_candidates()
        folds = make_folds(n, self.cv_folds, "blocked_kfold", self.gap)
        if not folds:
            raise ValueError("Unable to construct OOF folds for F10 stacking.")

        oof_map: dict[str, pd.Series] = {}
        fam_map: dict[str, str] = {}
        full_models: list[Any] = []
        valid_names: list[str] = []

        for nm, fam_id, factory in specs:
            oof_vec = np.full(n, np.nan, dtype=float)
            for tr_idx, te_idx in folds:
                est_fold = factory()
                est_fold.fit(X_df.iloc[tr_idx], yv[tr_idx])
                oof_vec[te_idx] = est_fold.predict(X_df.iloc[te_idx])
            est_full = factory()
            est_full.fit(X_df, yv)
            oof_map[nm] = pd.Series(oof_vec, index=X_df.index)
            fam_map[nm] = fam_id
            full_models.append(est_full)
            valid_names.append(nm)

        y_ser = pd.Series(yv, index=X_df.index)
        self.fit_from_oof(oof_map, y_ser, family_map=fam_map)
        self.fitted_models_ = full_models
        self.candidate_names_ = valid_names
        yhat_full = self.predict(X_df)
        self.train_metrics_ = compute_baseline_metrics(yv, yhat_full)
        return self

    def predict(self, X: Any) -> np.ndarray:
        X_df, _ = prepare_design_frame(X, self.feature_names_)
        if not self.fitted_models_:
            raise RuntimeError("F10 model was fitted via fit_from_oof only; supply constituent models via fit().")
        preds = np.column_stack([m.predict(X_df) for m in self.fitted_models_])
        return (preds @ self.weights_).reshape(-1)

    @property
    def n_params(self) -> int:
        return int(np.sum(self.weights_ > 1e-6))

    def formula_text(self) -> str:
        active = [
            f"{float(w):.4f}*{nm}"
            for nm, w in zip(self.candidate_names_, self.weights_)
            if float(w) > 1e-4
        ]
        return f"y_hat = {' + '.join(active)}  [simplex sum=1.0000]"
