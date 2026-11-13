"""Phase 4 Statistical Test Catalog T1..T14, assumption/fallback engine, CI methods, R validator & Monte Carlo."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..core.tcp import DecisionRule, FamilyDefinition, MultipleTestingCorrection, TestConfigPanel
from .ci_method import (
    ConfidenceInterval,
    bounded_effect_bootstrap_ci,
    detect_serial_autocorrelation,
    exact_t_ci,
    fisher_z_ci,
    mean_ci_with_autocorrelation_guard,
    moving_block_bootstrap_ci,
    newey_west_se_mean,
    validate_ci_level,
)
from .monte_carlo import (
    MonteCarloCalibrationSummary,
    compute_prospective_design_power,
    run_test_monte_carlo,
)
from .r_reference_validator import (
    MAX_REFERENCE_AGE_DAYS,
    REQUIRED_R_SLUGS,
    StaleRReferenceError,
    check_r_reference_freshness,
    validate_python_against_r_references,
)
from .t1_stationarity import run_t1_stationarity
from .t2_seasonality import run_t2_seasonality
from .t3_autocorrelation import run_t3_autocorrelation
from .t4_normality import run_t4_normality
from .t5_heteroskedasticity import run_t5_heteroskedasticity
from .t6_multicollinearity import run_t6_multicollinearity
from .t7_structural_break_chow import compute_chow_classical, run_t7_chow
from .t8_structural_break_quandt_andrews import compute_quandt_andrews_profile, run_t8_quandt_andrews
from .t9_pettitt import compute_pettitt_exact, run_t9_pettitt
from .t10_diebold_mariano import compute_dm_hln_exact, compute_mcs_exact, run_t10_diebold_mariano
from .t11_nemenyi import compute_friedman_nemenyi_exact, run_t11_nemenyi
from .t12_little_mcar import compute_little_mcar_em, run_t12_little_mcar
from .t13_outlier_influence import (
    compute_influence_diagnostics,
    compute_lagged_ccf_and_granger,
    compute_ramsey_reset_harvey_collier,
    run_t13_outlier_influence,
)
from .t14_causal_group import compute_multi_group_anova_kruskal, run_t14_causal_group
from .tc_assumptions import (
    AssumptionCheck,
    ObservedPowerForbiddenError,
    TestContractResult,
    apply_family_multiplicity,
    deterministic_subsample,
    evaluate_contract_decision,
    library_versions_snapshot,
    make_insufficient_data_result,
    make_not_applicable_group_result,
    make_not_run_user_result,
    resolve_autocorrelation_inference,
    stationarity_decision_matrix,
)

# Explicit immutable dispatch table (zero dynamic eval / getattr per S13)
CATALOG_RUNNERS: Mapping[str, Callable[..., TestContractResult]] = MappingProxyType(
    {
        "T1": run_t1_stationarity,
        "T2": run_t2_seasonality,
        "T3": run_t3_autocorrelation,
        "T4": run_t4_normality,
        "T5": run_t5_heteroskedasticity,
        "T6": run_t6_multicollinearity,
        "T7": run_t7_chow,
        "T8": run_t8_quandt_andrews,
        "T9": run_t9_pettitt,
        "T10": run_t10_diebold_mariano,
        "T11": run_t11_nemenyi,
        "T12": run_t12_little_mcar,
        "T13": run_t13_outlier_influence,
        "T14": run_t14_causal_group,
    }
)

PRE_MODEL_TEST_IDS: tuple[str, ...] = ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T13")
POST_MODEL_TEST_IDS: tuple[str, ...] = ("T10", "T11", "T12")
CAUSAL_GROUP_TEST_IDS: tuple[str, ...] = ("T14",)
ALL_CATALOG_TEST_IDS: tuple[str, ...] = (
    "T1",
    "T2",
    "T3",
    "T4",
    "T5",
    "T6",
    "T7",
    "T8",
    "T9",
    "T10",
    "T11",
    "T12",
    "T13",
    "T14",
)


def _derive_preliminary_ols_residuals(y: np.ndarray, X_df: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray | None]:
    """Compute pre-model baseline OLS residuals without mutating input data for T3, T4, T5 & T13."""
    if X_df is None or X_df.shape[1] == 0:
        valid = np.isfinite(y)
        yc = y[valid]
        return yc - (float(np.mean(yc)) if yc.size > 0 else 0.0), None

    X_raw = X_df.to_numpy(dtype=float)
    valid = np.isfinite(y) & np.all(np.isfinite(X_raw), axis=1)
    yc = y[valid]
    Xc = X_raw[valid]
    if yc.size < Xc.shape[1] + 2:
        return yc - (float(np.mean(yc)) if yc.size > 0 else 0.0), Xc
    X_aug = np.column_stack([np.ones(yc.size, dtype=float), Xc])
    beta, *_ = np.linalg.lstsq(X_aug, yc, rcond=None)
    return yc - X_aug @ beta, Xc


def run_full_test_catalog(
    df: pd.DataFrame,
    *,
    target_col: str,
    predictor_cols: Sequence[str] = (),
    group_col: str | None = None,
    oof_errors_by_model: Mapping[str, Any] | None = None,
    panel: TestConfigPanel | None = None,
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    correction: str = MultipleTestingCorrection.HOLM.value,
    decision_rule: str = DecisionRule.P_ONLY.value,
    disabled_tests: Sequence[str] = (),
    test_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    mode: str = "full",
    allow_hac: bool = True,
    period: int = 12,
    seed: int = 42,
) -> list[TestContractResult]:
    """Execute the complete T1..T14 suite respecting stage placement, user toggles, group guard, and family multiplicity.

    Stage placement:
      - Pre-model: ``T1..T9`` and ``T13``
      - Post-model (on OOF results): ``T10..T12``
      - Causal group (ONLY when ``group_col`` is present in ``df``): ``T14``
    """
    if panel is not None:
        panel.assert_accepted()
        eff_alpha = float(panel.alpha)
        eff_conf = float(panel.confidence_level)
        eff_corr = str(panel.correction)
        eff_rule = str(panel.decision_rule)
    else:
        eff_alpha = float(alpha)
        eff_conf = float(confidence_level)
        eff_corr = str(correction)
        eff_rule = str(decision_rule)

    lv = validate_ci_level(eff_conf)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    disabled_set = {t.strip().upper() for t in disabled_tests}
    overrides = dict(test_overrides or {})

    y_series = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
    pred_names = [c for c in predictor_cols if c in df.columns and c != target_col and c != group_col]
    X_df = df[pred_names].apply(pd.to_numeric, errors="coerce") if pred_names else None
    X_arr = X_df.to_numpy(dtype=float) if X_df is not None and len(pred_names) > 0 else None
    prelim_resid, prelim_X = _derive_preliminary_ols_residuals(y_series, X_df)

    has_group = bool(group_col and group_col in df.columns)
    group_vals = df[group_col].to_numpy() if has_group and group_col is not None else None

    def _cfg(tid: str) -> tuple[bool, float, str, float, dict[str, Any]]:
        ov = dict(overrides.get(tid, {}))
        if panel is not None and tid in panel.test_panels:
            tc = panel.test_panels[tid]
            # Note: TCP T14 defaults enabled=False when no group col; preserve enabled=True when group is None so T14 returns 'not applicable (no group column)' unless explicitly user-disabled!
            if tid == "T14" and not has_group and tid not in disabled_set and "enabled" not in ov:
                en_val = True
            else:
                en_val = bool(ov.get("enabled", tc.enabled)) and (tid not in disabled_set)
            prm_val = {**dict(tc.parameters), **dict(ov.get("parameters", {}))}
            return (
                en_val,
                float(ov.get("alpha", eff_alpha)),
                str(ov.get("decision_rule", eff_rule)),
                float(ov.get("min_effect_size", tc.min_effect_size)),
                prm_val,
            )
        return (
            bool(ov.get("enabled", True)) and (tid not in disabled_set),
            float(ov.get("alpha", eff_alpha)),
            str(ov.get("decision_rule", eff_rule)),
            float(ov.get("min_effect_size", 0.0)),
            dict(ov.get("parameters", {})),
        )

    raw_results: dict[str, TestContractResult] = {}

    # --- Stage 1: Pre-Model Tests (T1..T9, T13) ---
    en, al, dr, me, prm = _cfg("T1")
    raw_results["T1"] = run_t1_stationarity(
        y_series,
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=target_col,
        allow_hac=allow_hac,
        max_lags=prm.get("max_lags"),
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, prm = _cfg("T2")
    raw_results["T2"] = run_t2_seasonality(
        y_series,
        period=int(prm.get("period", period)),
        max_lags=int(prm.get("max_lags", max(24, 2 * period))),
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=target_col,
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, prm = _cfg("T3")
    raw_results["T3"] = run_t3_autocorrelation(
        prelim_resid,
        lags=int(prm.get("lags", 10)),
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=f"{target_col}_residuals",
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, _ = _cfg("T4")
    raw_results["T4"] = run_t4_normality(
        prelim_resid,
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=f"{target_col}_residuals",
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, _ = _cfg("T5")
    raw_results["T5"] = run_t5_heteroskedasticity(
        prelim_resid,
        prelim_X,
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=f"{target_col}_residuals",
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, prm = _cfg("T6")
    raw_results["T6"] = run_t6_multicollinearity(
        X_df if X_df is not None else np.empty((len(df), 0)),
        vif_threshold=float(prm.get("vif_threshold", 10.0)),
        condition_threshold=float(prm.get("condition_threshold", 30.0)),
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, prm = _cfg("T7")
    raw_results["T7"] = run_t7_chow(
        y_series,
        X_arr,
        break_point=prm.get("break_point"),
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=target_col,
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, prm = _cfg("T8")
    raw_results["T8"] = run_t8_quandt_andrews(
        y_series,
        X_arr,
        trim=float(prm.get("trim", 0.15)),
        n_boot=prm.get("n_boot"),
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=target_col,
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, _ = _cfg("T9")
    raw_results["T9"] = run_t9_pettitt(
        y_series,
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=target_col,
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    en, al, dr, me, _ = _cfg("T13")
    raw_results["T13"] = run_t13_outlier_influence(
        y_series,
        X_arr,
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=target_col,
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    # --- Stage 2: Post-Model Tests on OOF Results (T10..T12) ---
    oof_map = dict(oof_errors_by_model) if oof_errors_by_model is not None else {}
    model_keys = list(oof_map.keys())

    en, al, dr, me, prm = _cfg("T10")
    if not en:
        raw_results["T10"] = make_not_run_user_result(
            test_id="T10",
            name="Diebold-Mariano (HLN) OOF Forecast Equivalence & MCS",
            stage="post_model",
            family="model_comparison",
            target=target_col,
            mode=norm_mode,
            confidence_level=lv,
        )
    elif len(model_keys) >= 2:
        m1, m2 = model_keys[0], model_keys[1]
        losses_sq = {mk: np.asarray(oof_map[mk], dtype=float) ** 2 for mk in model_keys}
        raw_results["T10"] = run_t10_diebold_mariano(
            oof_map[m1],
            oof_map[m2],
            h=int(prm.get("h", 1)),
            power=int(prm.get("power", 2)),
            extra_oof_losses=losses_sq,
            alpha=al,
            confidence_level=lv,
            mode=norm_mode,
            enabled=True,
            target=target_col,
            variable=f"{m1}_vs_{m2}",
            allow_hac=allow_hac,
            decision_rule=dr,
            min_effect_size=me,
            seed=seed,
        )
    else:
        raw_results["T10"] = make_insufficient_data_result(
            test_id="T10",
            name="Diebold-Mariano (HLN) OOF Forecast Equivalence & MCS",
            n_used=len(model_keys),
            min_required=2,
            reason="requires >=2 candidate models with OOF residuals",
            stage="post_model",
            family="model_comparison",
            target=target_col,
            mode=norm_mode,
            confidence_level=lv,
        )

    en, al, dr, me, _ = _cfg("T11")
    if not en:
        raw_results["T11"] = make_not_run_user_result(
            test_id="T11",
            name="Friedman + Nemenyi Post-Hoc OOF Rank Comparison",
            stage="post_model",
            family="model_comparison",
            target=target_col,
            mode=norm_mode,
            confidence_level=lv,
        )
    elif len(model_keys) >= 3:
        losses_sq = {mk: np.asarray(oof_map[mk], dtype=float) ** 2 for mk in model_keys}
        raw_results["T11"] = run_t11_nemenyi(
            losses_sq,
            model_names=model_keys,
            alpha=al,
            confidence_level=lv,
            mode=norm_mode,
            enabled=True,
            target=target_col,
            allow_hac=allow_hac,
            decision_rule=dr,
            min_effect_size=me,
            seed=seed,
        )
    else:
        raw_results["T11"] = make_insufficient_data_result(
            test_id="T11",
            name="Friedman + Nemenyi Post-Hoc OOF Rank Comparison",
            n_used=len(model_keys),
            min_required=3,
            reason="requires >=3 candidate models with OOF residuals for Nemenyi post-hoc ranks",
            stage="post_model",
            family="model_comparison",
            target=target_col,
            mode=norm_mode,
            confidence_level=lv,
        )

    en, al, dr, me, _ = _cfg("T12")
    num_cols = [target_col] + pred_names
    raw_results["T12"] = run_t12_little_mcar(
        df[num_cols] if len(num_cols) >= 2 else df.select_dtypes(include=[np.number]),
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=",".join(num_cols[:4]),
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    # --- Stage 3: Causal M&V with Group Column ONLY (T14) ---
    en, al, dr, me, prm = _cfg("T14")
    raw_results["T14"] = run_t14_causal_group(
        y_series,
        X_arr,
        group=group_vals,
        smd_threshold=float(prm.get("smd_threshold", 0.25)),
        alpha=al,
        confidence_level=lv,
        mode=norm_mode,
        enabled=en,
        target=target_col,
        variable=group_col or "group",
        allow_hac=allow_hac,
        decision_rule=dr,
        min_effect_size=me,
        seed=seed,
    )

    # --- Apply family-level multiplicity adjustment (preserving both p_raw and p_adj) ---
    fam_mode = "family"
    if panel is not None:
        if panel.family_definition == FamilyDefinition.GLOBAL.value:
            fam_mode = "global"
        elif panel.family_definition == FamilyDefinition.PER_TARGET.value:
            fam_mode = "target"

    ordered_raw = [raw_results[tid] for tid in ALL_CATALOG_TEST_IDS]
    return apply_family_multiplicity(ordered_raw, correction=eff_corr, family_by=fam_mode)


__all__ = [
    "ALL_CATALOG_TEST_IDS",
    "AssumptionCheck",
    "CATALOG_RUNNERS",
    "CAUSAL_GROUP_TEST_IDS",
    "ConfidenceInterval",
    "MAX_REFERENCE_AGE_DAYS",
    "MonteCarloCalibrationSummary",
    "ObservedPowerForbiddenError",
    "POST_MODEL_TEST_IDS",
    "PRE_MODEL_TEST_IDS",
    "REQUIRED_R_SLUGS",
    "StaleRReferenceError",
    "TestContractResult",
    "apply_family_multiplicity",
    "bounded_effect_bootstrap_ci",
    "check_r_reference_freshness",
    "compute_chow_classical",
    "compute_dm_hln_exact",
    "compute_friedman_nemenyi_exact",
    "compute_influence_diagnostics",
    "compute_lagged_ccf_and_granger",
    "compute_little_mcar_em",
    "compute_mcs_exact",
    "compute_multi_group_anova_kruskal",
    "compute_pettitt_exact",
    "compute_prospective_design_power",
    "compute_quandt_andrews_profile",
    "compute_ramsey_reset_harvey_collier",
    "detect_serial_autocorrelation",
    "deterministic_subsample",
    "evaluate_contract_decision",
    "exact_t_ci",
    "fisher_z_ci",
    "library_versions_snapshot",
    "make_insufficient_data_result",
    "make_not_applicable_group_result",
    "make_not_run_user_result",
    "mean_ci_with_autocorrelation_guard",
    "moving_block_bootstrap_ci",
    "newey_west_se_mean",
    "resolve_autocorrelation_inference",
    "run_full_test_catalog",
    "run_t1_stationarity",
    "run_t2_seasonality",
    "run_t3_autocorrelation",
    "run_t4_normality",
    "run_t5_heteroskedasticity",
    "run_t6_multicollinearity",
    "run_t7_chow",
    "run_t8_quandt_andrews",
    "run_t9_pettitt",
    "run_t10_diebold_mariano",
    "run_t11_nemenyi",
    "run_t12_little_mcar",
    "run_t13_outlier_influence",
    "run_t14_causal_group",
    "run_test_monte_carlo",
    "stationarity_decision_matrix",
    "validate_ci_level",
    "validate_python_against_r_references",
]
