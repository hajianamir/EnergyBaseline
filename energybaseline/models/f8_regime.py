"""F8 — Regime-switching / operational-mode segmented baselines & multi-method consensus via Kendall's W."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from sklearn.ensemble import RandomForestRegressor

from ..mining.clustering import (
    RegimeLabelLeakageError,
    assert_no_cluster_labels_in_model_inputs,
    is_discovery_cluster_column_name,
)
from .base import compute_baseline_metrics, prepare_design_frame


@dataclass(frozen=True)
class KendallsWResult:
    """Kendall's coefficient of concordance W across m ranking methods and k items."""

    w: float
    chi2_stat: float
    df: int
    p_value: float
    n_methods: int
    n_items: int
    mean_ranks: dict[str, float]
    consensus_order: tuple[str, ...]
    agreement_label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agreement_label": self.agreement_label,
            "chi2_stat": round(self.chi2_stat, 6),
            "consensus_order": list(self.consensus_order),
            "df": self.df,
            "mean_ranks": {k: round(v, 4) for k, v in self.mean_ranks.items()},
            "n_items": self.n_items,
            "n_methods": self.n_methods,
            "p_value": round(self.p_value, 8),
            "w": round(self.w, 6),
        }


def compute_kendalls_w(
    rankings_or_scores: Mapping[str, Sequence[float]] | np.ndarray,
    *,
    item_names: Sequence[str] | None = None,
    higher_score_is_rank1: bool = True,
    input_is_ranks: bool = False,
) -> KendallsWResult:
    """Compute tie-corrected Kendall's W concordance across m >= 2 methods ranking k >= 2 items."""
    if isinstance(rankings_or_scores, Mapping):
        mat = np.vstack([np.asarray(v, dtype=float).reshape(-1) for v in rankings_or_scores.values()])
    else:
        mat = np.asarray(rankings_or_scores, dtype=float)
        if mat.ndim != 2:
            raise ValueError("rankings_or_scores must be a 2-D array (n_methods, n_items) or mapping.")

    m, k = mat.shape
    if m < 2 or k < 2:
        raise ValueError(f"Kendall's W requires m >= 2 methods and k >= 2 items (got m={m}, k={k}).")
    names = list(item_names) if item_names is not None else [f"item_{j}" for j in range(k)]
    if len(names) != k:
        raise ValueError("len(item_names) must match number of items k.")

    ranks = np.zeros_like(mat, dtype=float)
    tie_correction_total = 0.0
    for i in range(m):
        row = mat[i]
        if input_is_ranks:
            r_i = sp_stats.rankdata(row, method="average")
        else:
            r_i = sp_stats.rankdata(-row if higher_score_is_rank1 else row, method="average")
        ranks[i] = r_i
        _, counts = np.unique(r_i, return_counts=True)
        tie_correction_total += float(np.sum(counts**3 - counts))

    col_sums = np.sum(ranks, axis=0)
    s_dev = float(np.sum((col_sums - float(np.mean(col_sums))) ** 2))
    denom = float(m * m * (k**3 - k)) - float(m) * tie_correction_total
    w = float(np.clip((12.0 * s_dev) / max(denom, 1e-12), 0.0, 1.0)) if denom > 1e-12 else 1.0
    chi2_stat = float(m * (k - 1) * w)
    df = k - 1
    p_val = float(np.clip(sp_stats.chi2.sf(chi2_stat, df=df), 0.0, 1.0))

    mean_r = np.mean(ranks, axis=0)
    order_idx = np.argsort(mean_r, kind="mergesort")
    mean_ranks_map = {names[j]: float(mean_r[j]) for j in range(k)}
    consensus = tuple(names[int(j)] for j in order_idx)
    label = "strong_agreement" if w >= 0.70 else ("moderate_agreement" if w >= 0.40 else "weak_agreement")
    return KendallsWResult(
        w=w,
        chi2_stat=chi2_stat,
        df=df,
        p_value=p_val,
        n_methods=m,
        n_items=k,
        mean_ranks=mean_ranks_map,
        consensus_order=consensus,
        agreement_label=label,
    )


def compute_feature_consensus_f8(
    X: pd.DataFrame,
    y: Any,
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Compare predictor importance across 4 diverse ranking methods and summarize concordance via Kendall's W."""
    X_df, cols = prepare_design_frame(X)
    yv = np.asarray(y, dtype=float).reshape(-1)
    if len(cols) < 2:
        raise ValueError("Feature consensus requires at least 2 candidate predictor columns.")
    X_mat = X_df.fillna(X_df.median(numeric_only=True)).fillna(0.0).to_numpy(dtype=float)

    # 1. Absolute Pearson r
    pearson_abs = [
        abs(float(np.corrcoef(X_mat[:, j], yv)[0, 1])) if np.std(X_mat[:, j]) > 1e-12 else 0.0
        for j in range(len(cols))
    ]
    # 2. Absolute Spearman rho
    spearman_abs = [
        abs(float(pd.Series(X_mat[:, j]).corr(pd.Series(yv), method="spearman")))
        if np.std(X_mat[:, j]) > 1e-12
        else 0.0
        for j in range(len(cols))
    ]
    # 3. Standardized OLS |beta|
    mu = X_mat.mean(axis=0)
    sd = np.where(X_mat.std(axis=0, ddof=0) > 1e-12, X_mat.std(axis=0, ddof=0), 1.0)
    Z = (X_mat - mu) / sd
    D = np.column_stack([np.ones(Z.shape[0], dtype=float), Z])
    beta, *_ = np.linalg.lstsq(D, yv, rcond=None)
    ols_std_abs = [abs(float(v)) for v in beta[1:]]

    # 4. RandomForest impurity importance
    rf = RandomForestRegressor(n_estimators=60, max_depth=5, random_state=seed, n_jobs=1)
    rf.fit(Z, yv)
    rf_imp = [float(v) for v in rf.feature_importances_]

    scores = {
        "abs_pearson": pearson_abs,
        "abs_spearman": spearman_abs,
        "standardized_ols": ols_std_abs,
        "random_forest": rf_imp,
    }
    kw = compute_kendalls_w(scores, item_names=cols, higher_score_is_rank1=True)
    return {
        "kendalls_w": kw.to_dict(),
        "method_scores": {m: {c: round(float(vals[idx]), 6) for idx, c in enumerate(cols)} for m, vals in scores.items()},
    }


def _extract_calendar_regime(index: pd.Index, calendar_regime: str) -> np.ndarray:
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError(
            "F8 calendar_regime stratification requires a DatetimeIndex when regime_col is not specified."
        )
    mode = calendar_regime.strip().lower()
    if mode == "weekday_weekend":
        return np.where(index.dayofweek >= 5, "weekend", "weekday")
    if mode == "occupied_hours":
        occ = (index.dayofweek < 5) & (index.hour >= 7) & (index.hour < 19)
        return np.where(occ, "occupied", "unoccupied")
    if mode == "season":
        m = index.month
        return np.where(m.isin([12, 1, 2]), "winter", np.where(m.isin([6, 7, 8]), "summer", "shoulder"))
    raise ValueError(f"Unsupported calendar_regime '{calendar_regime}'.")


class F8RegimeBaseline:
    """Operational-mode / calendar regime-segmented baseline with Kendall's W consensus diagnostics (MT-5 safe)."""

    family_id = "F8"
    family_name = "Regime-Segmented Baseline & Kendall's W Consensus"

    def __init__(
        self,
        *,
        regime_col: str | None = None,
        calendar_regime: str = "weekday_weekend",
        min_regime_rows: int = 8,
        seed: int = 42,
    ) -> None:
        if regime_col is not None and is_discovery_cluster_column_name(regime_col):
            raise RegimeLabelLeakageError(
                f"MT-5 violation: discovery cluster column '{regime_col}' cannot stratify F8 regime baselines."
            )
        self.regime_col = regime_col
        self.calendar_regime = calendar_regime
        self.min_regime_rows = max(4, int(min_regime_rows))
        self.seed = int(seed)

        self.feature_names_: list[str] = []
        self.regime_models_: dict[str, tuple[float, np.ndarray]] = {}
        self.global_model_: tuple[float, np.ndarray] = (0.0, np.empty(0, dtype=float))
        self.kendalls_w_: KendallsWResult | None = None
        self.train_metrics_: dict[str, float] = {}

    def _resolve_regimes_and_features(self, X: pd.DataFrame, *, fit_mode: bool) -> tuple[np.ndarray, np.ndarray]:
        assert_no_cluster_labels_in_model_inputs(X)
        if self.regime_col is not None:
            if self.regime_col not in X.columns:
                raise KeyError(f"Operational state column '{self.regime_col}' missing from X.")
            r_ser = X[self.regime_col]
            assert_no_cluster_labels_in_model_inputs(r_ser)
            regimes = np.asarray([str(v) for v in r_ser.to_numpy()], dtype=object)
            feat_cols = [str(c) for c in X.columns if str(c) != self.regime_col] if fit_mode else self.feature_names_
        else:
            regimes = _extract_calendar_regime(X.index, self.calendar_regime)
            feat_cols = [str(c) for c in X.columns] if fit_mode else self.feature_names_

        if fit_mode:
            self.feature_names_ = feat_cols
            num_df = X[feat_cols].apply(pd.to_numeric, errors="coerce")
            medians = num_df.median(numeric_only=True).fillna(0.0)
            self._impute_medians_ = medians.to_dict()
        else:
            num_df = X[self.feature_names_].apply(pd.to_numeric, errors="coerce")
        X_mat = num_df.fillna(self._impute_medians_).fillna(0.0).to_numpy(dtype=float)
        return regimes, X_mat

    def fit(self, X: pd.DataFrame, y: Any) -> "F8RegimeBaseline":
        assert_no_cluster_labels_in_model_inputs(X)
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        if len(X) != yv.size or yv.size < 12:
            raise ValueError("F8 requires matching X/y DataFrame with >= 12 observations.")

        regimes, X_mat = self._resolve_regimes_and_features(X, fit_mode=True)
        n = yv.size
        Dg = np.column_stack([np.ones(n, dtype=float), X_mat])
        bg, *_ = np.linalg.lstsq(Dg, yv, rcond=None)
        self.global_model_ = (float(bg[0]), np.asarray(bg[1:], dtype=float))

        self.regime_models_ = {}
        regime_abs_coefs: dict[str, list[float]] = {}
        yhat = np.zeros(n, dtype=float)
        for reg in sorted({str(r) for r in regimes}):
            mask = regimes == reg
            if int(np.sum(mask)) >= max(self.min_regime_rows, X_mat.shape[1] + 2):
                Dr = np.column_stack([np.ones(int(np.sum(mask)), dtype=float), X_mat[mask]])
                br, *_ = np.linalg.lstsq(Dr, yv[mask], rcond=None)
                b0, b_vec = float(br[0]), np.asarray(br[1:], dtype=float)
                self.regime_models_[reg] = (b0, b_vec)
                yhat[mask] = b0 + X_mat[mask] @ b_vec
                if len(self.feature_names_) >= 2:
                    sd_r = np.where(X_mat[mask].std(axis=0) > 1e-12, X_mat[mask].std(axis=0), 1.0)
                    regime_abs_coefs[f"regime_{reg}"] = [abs(float(b_vec[j] * sd_r[j])) for j in range(len(self.feature_names_))]
            else:
                b0, b_vec = self.global_model_
                yhat[mask] = b0 + X_mat[mask] @ b_vec

        if len(self.feature_names_) >= 2:
            if len(regime_abs_coefs) >= 2:
                self.kendalls_w_ = compute_kendalls_w(
                    regime_abs_coefs,
                    item_names=self.feature_names_,
                    higher_score_is_rank1=True,
                )
            else:
                fc = compute_feature_consensus_f8(
                    pd.DataFrame(X_mat, columns=self.feature_names_, index=X.index),
                    yv,
                    seed=self.seed,
                )
                w_d = fc["kendalls_w"]
                self.kendalls_w_ = KendallsWResult(
                    w=float(w_d["w"]),
                    chi2_stat=float(w_d["chi2_stat"]),
                    df=int(w_d["df"]),
                    p_value=float(w_d["p_value"]),
                    n_methods=int(w_d["n_methods"]),
                    n_items=int(w_d["n_items"]),
                    mean_ranks=dict(w_d["mean_ranks"]),
                    consensus_order=tuple(w_d["consensus_order"]),
                    agreement_label=str(w_d["agreement_label"]),
                )
        else:
            self.kendalls_w_ = None

        self.train_metrics_ = compute_baseline_metrics(yv, yhat)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        assert_no_cluster_labels_in_model_inputs(X)
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X, columns=self.feature_names_)
        regimes, X_mat = self._resolve_regimes_and_features(X, fit_mode=False)
        yhat = np.zeros(len(X), dtype=float)
        for reg in sorted({str(r) for r in regimes}):
            mask = regimes == reg
            b0, b_vec = self.regime_models_.get(reg, self.global_model_)
            yhat[mask] = b0 + X_mat[mask] @ b_vec
        return yhat

    @property
    def n_params(self) -> int:
        k_each = 1 + len(self.feature_names_)
        return max(k_each, len(self.regime_models_) * k_each)

    def formula_text(self) -> str:
        parts = []
        for reg, (b0, b_vec) in sorted(self.regime_models_.items()):
            slopes = " ".join(f"{float(c):+.4g}*{nm}" for nm, c in zip(self.feature_names_, b_vec))
            parts.append(f"[{reg}]: {b0:+.5g} {slopes}")
        w_txt = f", Kendall_W={self.kendalls_w_.w:.3f}" if self.kendalls_w_ is not None else ""
        return f"PiecewiseRegime({'; '.join(parts)}{w_txt})"
