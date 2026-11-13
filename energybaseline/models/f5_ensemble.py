"""F5 — Tree & gradient-boosted ensembles (RandomForest, ExtraTrees, XGBoost, LightGBM, CatBoost) with monotonic constraints."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor

from .base import OptionalBackendSkippedError, compute_baseline_metrics, prepare_design_frame

SUPPORTED_F5_VARIANTS = ("random_forest", "extra_trees", "xgboost", "lightgbm", "catboost")


class F5EnsembleBaseline:
    """Tree and gradient-boosted baseline supporting physical monotonic constraints (-1, 0, +1)."""

    family_id = "F5"
    family_name = "Tree & Gradient-Boosted Ensembles"

    def __init__(
        self,
        variant: str = "lightgbm",
        *,
        monotonic_constraints: Mapping[str, int] | None = None,
        n_estimators: int = 120,
        max_depth: int = 5,
        learning_rate: float = 0.06,
        min_samples_leaf: int = 3,
        seed: int = 42,
    ) -> None:
        v = str(variant).strip().lower()
        if v not in SUPPORTED_F5_VARIANTS:
            raise ValueError(f"Unsupported F5 variant '{variant}'; expected one of {SUPPORTED_F5_VARIANTS}.")
        self.variant = v
        self.monotonic_constraints = dict(monotonic_constraints or {})
        self.n_estimators = max(10, int(n_estimators))
        self.max_depth = max(2, int(max_depth))
        self.learning_rate = float(np.clip(learning_rate, 1e-3, 0.5))
        self.min_samples_leaf = max(1, int(min_samples_leaf))
        self.seed = int(seed)

        self.feature_names_: list[str] = []
        self.constraint_vector_: tuple[int, ...] = ()
        self.feature_importances_: dict[str, float] = {}
        self.train_metrics_: dict[str, float] = {}

    def _build_estimator(self, cst: list[int], n_rows: int) -> Any:
        has_cst = any(c != 0 for c in cst)
        leaf = min(self.min_samples_leaf, max(1, n_rows // 15))
        if self.variant == "random_forest":
            return RandomForestRegressor(
                n_estimators=self.n_estimators,
                max_depth=self.max_depth + 3,
                min_samples_leaf=leaf,
                monotonic_cst=cst if has_cst else None,
                n_jobs=1,
                random_state=self.seed,
            )
        if self.variant == "extra_trees":
            return ExtraTreesRegressor(
                n_estimators=self.n_estimators,
                max_depth=None if has_cst else (self.max_depth + 3),
                min_samples_leaf=1 if has_cst else leaf,
                monotonic_cst=cst if has_cst else None,
                n_jobs=1,
                random_state=self.seed,
            )
        if self.variant == "xgboost":
            try:
                from xgboost import XGBRegressor
            except ImportError as exc:  # pragma: no cover
                raise OptionalBackendSkippedError("xgboost is not installed.") from exc
            return XGBRegressor(
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                learning_rate=self.learning_rate,
                subsample=0.9,
                colsample_bytree=1.0,
                monotone_constraints=tuple(cst) if has_cst else None,
                n_jobs=1,
                random_state=self.seed,
                verbosity=0,
            )
        if self.variant == "lightgbm":
            try:
                from lightgbm import LGBMRegressor
            except ImportError as exc:  # pragma: no cover
                raise OptionalBackendSkippedError("lightgbm is not installed.") from exc
            return LGBMRegressor(
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                num_leaves=min(31, (2**self.max_depth) - 1),
                learning_rate=self.learning_rate,
                min_child_samples=max(3, leaf),
                monotone_constraints=cst if has_cst else None,
                n_jobs=1,
                random_state=self.seed,
                verbose=-1,
            )
        try:
            from catboost import CatBoostRegressor
        except ImportError as exc:  # pragma: no cover
            raise OptionalBackendSkippedError("catboost is not installed.") from exc
        return CatBoostRegressor(
            iterations=self.n_estimators,
            depth=min(8, self.max_depth),
            learning_rate=self.learning_rate,
            monotone_constraints=cst if has_cst else None,
            random_seed=self.seed,
            thread_count=1,
            verbose=False,
            allow_writing_files=False,
        )

    def fit(self, X: Any, y: Any) -> "F5EnsembleBaseline":
        X_df, cols = prepare_design_frame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        if len(X_df) != yv.size or yv.size < 12:
            raise ValueError("F5 requires matching X/y with >= 12 observations.")
        self.feature_names_ = cols
        medians = X_df.median(numeric_only=True).fillna(0.0)
        self._impute_medians_ = medians.to_dict()
        X_mat = X_df.fillna(medians).fillna(0.0).to_numpy(dtype=float)

        cst = [int(np.sign(int(self.monotonic_constraints.get(c, 0)))) for c in cols]
        self.constraint_vector_ = tuple(cst)

        est = self._build_estimator(cst, len(X_df))
        est.fit(X_mat, yv)
        self._estimator_ = est
        raw_imp = getattr(est, "feature_importances_", np.zeros(len(cols), dtype=float))
        imp_arr = np.asarray(raw_imp, dtype=float).reshape(-1)
        tot = float(np.sum(np.abs(imp_arr))) or 1.0
        self.feature_importances_ = {c: float(imp_arr[i]) / tot for i, c in enumerate(cols)}
        yhat = np.asarray(est.predict(X_mat), dtype=float).reshape(-1)
        self.train_metrics_ = compute_baseline_metrics(yv, yhat)
        return self

    def predict(self, X: Any) -> np.ndarray:
        X_df, _ = prepare_design_frame(X, self.feature_names_)
        X_mat = X_df.fillna(self._impute_medians_).fillna(0.0).to_numpy(dtype=float)
        return np.asarray(self._estimator_.predict(X_mat), dtype=float).reshape(-1)

    @property
    def n_params(self) -> int:
        return int(self.n_estimators * (2**min(4, self.max_depth)))

    def formula_text(self) -> str:
        cst_str = ", ".join(f"{c}:{s:+d}" for c, s in zip(self.feature_names_, self.constraint_vector_) if s != 0)
        cst_note = f", monotonic=({cst_str})" if cst_str else ""
        return f"Ensemble[{self.variant}](trees={self.n_estimators}, max_depth={self.max_depth}{cst_note})"
