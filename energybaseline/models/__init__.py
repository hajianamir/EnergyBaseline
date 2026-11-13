"""Phase 5 Model Families Catalog (F1..F10) & Gated Model Evaluation Register (AC-13, VAL-7, UX-5, MT-5)."""

from __future__ import annotations

import time
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from ..core.tcp import TestConfigPanel
from ..core.vsg import VariableSelectionGate
from ..mining.clustering import strip_discovery_cluster_labels
from ..tests_catalog.t10_diebold_mariano import compute_mcs_exact
from .base import (
    ModelEvaluationEntry,
    NNGateDecision,
    OptionalBackendSkippedError,
    SampleSizeGateSkippedError,
    align_oof_predictions,
    compute_baseline_metrics,
    evaluate_nn_gate,
    prepare_design_frame,
    recommend_autocorr_embargo_gap,
)
from .ensemble import (
    InnerFoldNNLSEnsemble,
    NNLSEnsembleResult,
    evaluate_inner_oof_nnls_ensemble,
    fit_nnls_weights_on_inner_oof,
)
from .portfolio import (
    MANDATORY_PORTFOLIO_CLASSES,
    BasePortfolioWrapper,
    DegreeDayBaselineWrapper,
    ElasticNetBaselineWrapper,
    ExtraTreesWrapper,
    GradientBoostingWrapper,
    LassoBaselineWrapper,
    LightGBMWrapper,
    MLRBaselineWrapper,
    MLPWrapper,
    MeanBaselineWrapper,
    Piecewise1PTo5PWrapper,
    PortfolioModelSpec,
    RandomForestWrapper,
    RidgeBaselineWrapper,
    SVRRBFWrapper,
    SequenceLSTMWrapper,
    TOWTBaselineWrapper,
    XGBoostWrapper,
    get_portfolio_registry,
)
from .top5 import (
    SURROGATE_BANNER,
    SURROGATE_DISCLAIMER,
    DriverAttributionReport,
    ModelRepresentation,
    SurrogateApproximation,
    Top5CandidateCard,
    Top5ShortlistView,
    build_model_representation,
    compute_driver_attributions,
    fit_labelled_diagnostic_surrogate,
)
from .f1_baseline import F1LinearBaseline
from .f2_ashrae import SUPPORTED_F2_TYPES, F2ChangePointBaseline, compute_variable_base_degree_days
from .f3_regularized import SUPPORTED_F3_VARIANTS, F3RegularizedBaseline, compute_regularization_path
from .f4_basis import SUPPORTED_F4_VARIANTS, F4BasisBaseline
from .f5_ensemble import SUPPORTED_F5_VARIANTS, F5EnsembleBaseline
from .f6_neural import SUPPORTED_F6_VARIANTS, F6NeuralBaseline
from .f7_timeseries import SUPPORTED_F7_VARIANTS, F7TimeSeriesBaseline
from .f8_regime import F8RegimeBaseline, KendallsWResult, compute_feature_consensus_f8, compute_kendalls_w
from .f9_decomposition import SUPPORTED_F9_VARIANTS, F9DecompositionBaseline
from .f10_stacking import F10ConvexStackingBaseline, solve_simplex_stacking_weights


def evaluate_f1_f10_catalog(
    df: pd.DataFrame,
    *,
    target_col: str,
    predictor_cols: Sequence[str] | None = None,
    vsg: VariableSelectionGate | None = None,
    tcp: TestConfigPanel | None = None,
    regime_col: str | None = None,
    seasonal_period: int = 12,
    lite_profile: bool = False,
    seed: int = 42,
) -> list[ModelEvaluationEntry]:
    """Evaluate all F1..F10 baseline families gated by VSG/TCP acceptance and MT-5 cluster-label isolation."""
    if vsg is None or tcp is None:
        raise ValueError("VSG and TCP instances are required before inferential F1..F10 model evaluation (PIPE-1).")
    vsg.assert_accepted()
    tcp.assert_accepted()

    # Enforce MT-5: strip any exploratory cluster labels from candidate DataFrame before building model inputs
    clean_df, stripped_cols = strip_discovery_cluster_labels(df)
    _ = stripped_cols

    if predictor_cols is None:
        preds = [c for c in vsg.included_variables(target_col) if c in clean_df.columns and c != target_col]
    else:
        preds = [str(c) for c in predictor_cols if str(c) in clean_df.columns and str(c) != target_col]
    if not preds:
        raise ValueError(f"No valid predictor columns available for target '{target_col}'.")

    work_cols = [target_col, *preds]
    if regime_col and regime_col in clean_df.columns and regime_col not in work_cols:
        work_cols.append(regime_col)
    work = clean_df[work_cols].dropna(subset=[target_col]).copy()
    yv = pd.to_numeric(work[target_col], errors="coerce").to_numpy(dtype=float)
    X_pred = work[preds]
    n = len(work)
    n_train = max(12, int(round(0.75 * n)))
    if n - n_train < 4:
        n_train = max(8, n - 4)

    X_tr, X_va = X_pred.iloc[:n_train], X_pred.iloc[n_train:]
    y_tr, y_va = yv[:n_train], yv[n_train:]

    specs: list[tuple[str, str, str, Callable[[], tuple[np.ndarray, int, str]]]] = [
        (
            "F1",
            F1LinearBaseline.family_name,
            "F1_HAC_Linear",
            lambda: _fit_eval_standard(F1LinearBaseline("hac", confidence=tcp.confidence), X_tr, y_tr, X_va),
        ),
        (
            "F2",
            F2ChangePointBaseline.family_name,
            "F2_ASHRAE_5P",
            lambda: _fit_eval_standard(
                F2ChangePointBaseline("5P", n_grid=25, n_boot_knots=12, seed=seed), X_tr, y_tr, X_va
            ),
        ),
        (
            "F3",
            F3RegularizedBaseline.family_name,
            "F3_Huber_Robust",
            lambda: _fit_eval_standard(F3RegularizedBaseline("huber", alpha=0.05, seed=seed), X_tr, y_tr, X_va),
        ),
        (
            "F4",
            F4BasisBaseline.family_name,
            "F4_GAM_Spline",
            lambda: _fit_eval_standard(F4BasisBaseline("gam_spline", n_knots=5, alpha=1e-2), X_tr, y_tr, X_va),
        ),
        (
            "F5",
            F5EnsembleBaseline.family_name,
            "F5_LightGBM",
            lambda: _fit_eval_standard(
                F5EnsembleBaseline("lightgbm", n_estimators=80, max_depth=4, seed=seed), X_tr, y_tr, X_va
            ),
        ),
        (
            "F6",
            F6NeuralBaseline.family_name,
            "F6_MLP",
            lambda: _fit_eval_standard(
                F6NeuralBaseline("mlp", min_samples=40, max_epochs=80, seed=seed), X_tr, y_tr, X_va
            ),
        ),
        (
            "F6",
            F6NeuralBaseline.family_name,
            "F6_LSTM",
            lambda: _fit_eval_standard(
                F6NeuralBaseline(
                    "lstm", min_samples=50, seq_len=4, max_epochs=50, force_lite_profile=lite_profile, seed=seed
                ),
                X_tr,
                y_tr,
                X_va,
            ),
        ),
        (
            "F7",
            F7TimeSeriesBaseline.family_name,
            "F7_SARIMAX",
            lambda: _fit_eval_standard(
                F7TimeSeriesBaseline("arimax", order=(1, 0, 0), seasonal_periods=max(2, min(seasonal_period, n_train // 3))),
                X_tr,
                y_tr,
                X_va,
            ),
        ),
        (
            "F8",
            F8RegimeBaseline.family_name,
            "F8_Regime_Segmented",
            lambda: _fit_eval_f8(work, preds, regime_col, n_train, y_tr, seed),
        ),
        (
            "F9",
            F9DecompositionBaseline.family_name,
            "F9_STL_Fourier_Hybrid",
            lambda: _fit_eval_f9(X_tr, y_tr, X_va, max(2, min(seasonal_period, n_train // 3))),
        ),
        (
            "F9",
            F9DecompositionBaseline.family_name,
            "F9_Prophet_Optional",
            lambda: _fit_eval_f9_prophet(X_tr, y_tr, X_va, max(2, min(seasonal_period, n_train // 3))),
        ),
        (
            "F10",
            F10ConvexStackingBaseline.family_name,
            "F10_Convex_Stacking",
            lambda: _fit_eval_standard(F10ConvexStackingBaseline(cv_folds=3, gap=1, seed=seed), X_tr, y_tr, X_va),
        ),
    ]

    raw_rows: list[dict[str, Any]] = []
    val_losses: dict[str, np.ndarray] = {}

    for fam_id, fam_name, mod_name, runner in specs:
        t0 = time.perf_counter()
        try:
            pred_va, n_par, f_txt = runner()
            dt = time.perf_counter() - t0
            m = compute_baseline_metrics(y_va, pred_va)
            qualified = bool(np.isfinite(m["cvrmse"]) and m["cvrmse"] <= 35.0 and abs(m["nmbe"]) <= 15.0)
            comp = float(max(0.0, min(100.0, 100.0 - 1.5 * m["cvrmse"] - 1.0 * abs(m["nmbe"]) + 10.0 * max(0.0, m["r2"]))))
            val_losses[mod_name] = (y_va - pred_va) ** 2
            raw_rows.append(
                {
                    "composite_score": comp,
                    "family_id": fam_id,
                    "family_name": fam_name,
                    "fit_seconds": dt,
                    "formula_summary": f_txt,
                    "metrics": m,
                    "model_name": mod_name,
                    "n_obs": n,
                    "n_params": n_par,
                    "qualified": qualified,
                    "reason": "Meets validation thresholds." if qualified else f"Exceeded validation error band (CVRMSE={m['cvrmse']:.2f}%).",
                    "status": "qualified" if qualified else "rejected",
                }
            )
        except (SampleSizeGateSkippedError, OptionalBackendSkippedError) as exc:
            dt = time.perf_counter() - t0
            raw_rows.append(
                {
                    "composite_score": None,
                    "family_id": fam_id,
                    "family_name": fam_name,
                    "fit_seconds": dt,
                    "formula_summary": "not fitted",
                    "metrics": None,
                    "model_name": mod_name,
                    "n_obs": n,
                    "n_params": 0,
                    "qualified": False,
                    "reason": str(exc),
                    "status": "skipped",
                }
            )
        except Exception as exc:
            dt = time.perf_counter() - t0
            raw_rows.append(
                {
                    "composite_score": None,
                    "family_id": fam_id,
                    "family_name": fam_name,
                    "fit_seconds": dt,
                    "formula_summary": "not fitted",
                    "metrics": None,
                    "model_name": mod_name,
                    "n_obs": n,
                    "n_params": 0,
                    "qualified": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "status": "rejected",
                }
            )

    mcs_set: set[str] = set()
    if len(val_losses) >= 2 and y_va.size >= 6:
        names_l = list(val_losses.keys())
        L_mat = np.column_stack([val_losses[nm] for nm in names_l])
        mcs_res = compute_mcs_exact(L_mat, model_names=names_l, alpha=tcp.alpha, n_boot=120, block_length=2, seed=seed)
        mcs_set = set(mcs_res["superior_set"])
    elif len(val_losses) == 1:
        mcs_set = set(val_losses.keys())

    # Mark top-5 qualified models as 'shortlisted'
    qual_sorted = sorted(
        [r for r in raw_rows if r["status"] == "qualified"],
        key=lambda r: -(r["composite_score"] or 0.0),
    )
    shortlisted_names = {r["model_name"] for r in qual_sorted[:5]}

    tier_map = {
        "F1_HAC_Linear": "M",
        "F2_ASHRAE_5P": "M",
        "F3_Huber_Robust": "S",
        "F4_GAM_Spline": "S",
        "F5_LightGBM": "M",
        "F6_MLP": "M",
        "F6_LSTM": "M",
        "F7_SARIMAX": "S",
        "F8_Regime_Segmented": "S",
        "F9_STL_Fourier_Hybrid": "S",
        "F9_Prophet_Optional": "O",
        "F10_Convex_Stacking": "S",
    }
    badge_map = {
        "shortlisted": "SHORTLISTED",
        "qualified": "QUALIFIED",
        "rejected": "REJECTED",
        "skipped": "SKIPPED",
    }
    entries: list[ModelEvaluationEntry] = []
    for r in raw_rows:
        st = "shortlisted" if r["model_name"] in shortlisted_names else r["status"]
        m = r["metrics"]
        entries.append(
            ModelEvaluationEntry(
                family_id=r["family_id"],
                family_name=r["family_name"],
                model_name=r["model_name"],
                status=st,
                status_badge=badge_map.get(st, st.upper()),
                r2=m["r2"] if m else None,
                cvrmse=m["cvrmse"] if m else None,
                nmbe=m["nmbe"] if m else None,
                mae=m["mae"] if m else None,
                rmse=m["rmse"] if m else None,
                wape=m["wape"] if m else None,
                composite_score=r["composite_score"],
                in_mcs=r["model_name"] in mcs_set,
                n_obs=r["n_obs"],
                n_params=r["n_params"],
                fit_seconds=r["fit_seconds"],
                formula_summary=r["formula_summary"],
                reason=r["reason"],
                requirement_tier=tier_map.get(r["model_name"], "M"),
            )
        )
    return entries


def _fit_eval_standard(est: Any, X_tr: pd.DataFrame, y_tr: np.ndarray, X_va: pd.DataFrame) -> tuple[np.ndarray, int, str]:
    est.fit(X_tr, y_tr)
    pred = np.asarray(est.predict(X_va), dtype=float).reshape(-1)
    return pred, int(est.n_params), str(est.formula_text())


def _fit_eval_f8(
    work: pd.DataFrame,
    preds: list[str],
    regime_col: str | None,
    n_train: int,
    y_tr: np.ndarray,
    seed: int,
) -> tuple[np.ndarray, int, str]:
    cols = [*preds, regime_col] if (regime_col and regime_col in work.columns) else preds
    X_f8 = work[cols].copy()
    if regime_col is None and not isinstance(X_f8.index, pd.DatetimeIndex):
        X_f8["__cal_even_odd"] = np.where(np.arange(len(X_f8)) % 7 >= 5, "weekend_proxy", "weekday_proxy")
        est = F8RegimeBaseline(regime_col="__cal_even_odd", seed=seed)
    else:
        est = F8RegimeBaseline(regime_col=regime_col, calendar_regime="weekday_weekend", seed=seed)
    est.fit(X_f8.iloc[:n_train], y_tr)
    pred = est.predict(X_f8.iloc[n_train:])
    return pred, int(est.n_params), est.formula_text()


def _fit_eval_f9(X_tr: pd.DataFrame, y_tr: np.ndarray, X_va: pd.DataFrame, period: int) -> tuple[np.ndarray, int, str]:
    est = F9DecompositionBaseline("stl_fourier", period=period, fourier_order=2)
    est.fit(X_tr, y_tr)
    pred = est.predict(X_va, start_step=len(X_tr))
    return pred, int(est.n_params), est.formula_text()


def _fit_eval_f9_prophet(
    X_tr: pd.DataFrame, y_tr: np.ndarray, X_va: pd.DataFrame, period: int
) -> tuple[np.ndarray, int, str]:
    est = F9DecompositionBaseline("prophet", period=period, fourier_order=2)
    est.fit(X_tr, y_tr)
    pred = est.predict(X_va, start_step=len(X_tr))
    return pred, int(est.n_params), est.formula_text()


__all__ = [
    "BasePortfolioWrapper",
    "DegreeDayBaselineWrapper",
    "DriverAttributionReport",
    "ElasticNetBaselineWrapper",
    "ExtraTreesWrapper",
    "F1LinearBaseline",
    "F2ChangePointBaseline",
    "F3RegularizedBaseline",
    "F4BasisBaseline",
    "F5EnsembleBaseline",
    "F6NeuralBaseline",
    "F7TimeSeriesBaseline",
    "F8RegimeBaseline",
    "F9DecompositionBaseline",
    "F10ConvexStackingBaseline",
    "GradientBoostingWrapper",
    "InnerFoldNNLSEnsemble",
    "KendallsWResult",
    "LassoBaselineWrapper",
    "LightGBMWrapper",
    "MANDATORY_PORTFOLIO_CLASSES",
    "MLRBaselineWrapper",
    "MLPWrapper",
    "MeanBaselineWrapper",
    "ModelEvaluationEntry",
    "ModelRepresentation",
    "NNGateDecision",
    "NNLSEnsembleResult",
    "OptionalBackendSkippedError",
    "Piecewise1PTo5PWrapper",
    "PortfolioModelSpec",
    "RandomForestWrapper",
    "RidgeBaselineWrapper",
    "SUPPORTED_F2_TYPES",
    "SUPPORTED_F3_VARIANTS",
    "SUPPORTED_F4_VARIANTS",
    "SUPPORTED_F5_VARIANTS",
    "SUPPORTED_F6_VARIANTS",
    "SUPPORTED_F7_VARIANTS",
    "SUPPORTED_F9_VARIANTS",
    "SURROGATE_BANNER",
    "SURROGATE_DISCLAIMER",
    "SVRRBFWrapper",
    "SampleSizeGateSkippedError",
    "SequenceLSTMWrapper",
    "SurrogateApproximation",
    "TOWTBaselineWrapper",
    "Top5CandidateCard",
    "Top5ShortlistView",
    "XGBoostWrapper",
    "align_oof_predictions",
    "build_model_representation",
    "compute_baseline_metrics",
    "compute_driver_attributions",
    "compute_feature_consensus_f8",
    "compute_kendalls_w",
    "compute_regularization_path",
    "compute_variable_base_degree_days",
    "evaluate_f1_f10_catalog",
    "evaluate_inner_oof_nnls_ensemble",
    "evaluate_nn_gate",
    "fit_labelled_diagnostic_surrogate",
    "fit_nnls_weights_on_inner_oof",
    "get_portfolio_registry",
    "prepare_design_frame",
    "recommend_autocorr_embargo_gap",
    "solve_simplex_stacking_weights",
]
