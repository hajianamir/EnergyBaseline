"""T6 — Multicollinearity diagnostic suite: Variance Inflation Factor (VIF) & Belsley-Kuh-Welsch condition index."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from ..core.tcp import DecisionRule
from .ci_method import fisher_z_ci, moving_block_bootstrap_ci, validate_ci_level
from .tc_assumptions import (
    AssumptionCheck,
    TestContractResult,
    build_ci_dict,
    deterministic_subsample,
    evaluate_contract_decision,
    make_insufficient_data_result,
    make_not_run_user_result,
    resolve_autocorrelation_inference,
)

TEST_ID = "T6"
TEST_NAME = "VIF & Condition Index Multicollinearity"


def _compute_vif_and_condition(X: np.ndarray, names: list[str]) -> tuple[dict[str, float], float, float]:
    """Compute per-column VIFs, max auxiliary R2, and Belsley-Kuh-Welsch condition index."""
    n, p = X.shape
    stds = np.std(X, axis=0, ddof=1)
    safe_stds = np.where(stds > 1e-12, stds, 1.0)
    Z = (X - np.mean(X, axis=0)) / safe_stds

    # Scaled design matrix with intercept for condition index (Belsley, Kuh & Welsch 1980)
    Z_aug = np.column_stack([np.ones(n, dtype=float), Z])
    col_norms = np.linalg.norm(Z_aug, axis=0)
    Z_unit = Z_aug / np.where(col_norms > 1e-15, col_norms, 1.0)
    svals = np.linalg.svd(Z_unit, compute_uv=False)
    cond_index = float(svals[0] / max(float(svals[-1]), 1e-12))

    if p == 1:
        return {names[0]: 1.0}, 0.0, cond_index

    corr = np.corrcoef(Z, rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, 1.0)
    try:
        inv_corr = np.linalg.pinv(corr, rcond=1e-12)
        diag_v = np.diag(inv_corr)
        vifs = {names[j]: float(min(1e6, max(1.0, diag_v[j]))) for j in range(p)}
    except np.linalg.LinAlgError:
        vifs = {names[j]: 1e6 for j in range(p)}

    max_vif = max(vifs.values())
    max_r2 = max(0.0, min(0.999999, 1.0 - 1.0 / max_vif))
    return vifs, max_r2, cond_index


def run_t6_multicollinearity(
    predictors: Any,
    *,
    column_names: Sequence[str] | None = None,
    vif_threshold: float = 10.0,
    condition_threshold: float = 30.0,
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    mode: str = "full",
    enabled: bool = True,
    target: str = "",
    variable: str = "",
    allow_hac: bool = True,
    decision_rule: str = DecisionRule.P_ONLY.value,
    min_effect_size: float = 0.0,
    seed: int = 42,
) -> TestContractResult:
    """Assess multicollinearity via VIF + Belsley-Kuh-Welsch condition index (with Spearman rank VIF fallback)."""
    lv = validate_ci_level(confidence_level)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    if not enabled:
        return make_not_run_user_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            stage="pre_model",
            family="dependence_collinearity",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    if isinstance(predictors, pd.DataFrame):
        names = list(column_names) if column_names is not None else [str(c) for c in predictors.columns]
        mat = predictors.to_numpy(dtype=float)
    else:
        mat = np.asarray(predictors, dtype=float)
        if mat.ndim == 1:
            mat = mat.reshape(-1, 1)
        names = (
            list(column_names)
            if column_names is not None
            else [f"x{j + 1}" for j in range(mat.shape[1])]
        )

    valid_rows = np.all(np.isfinite(mat), axis=1)
    clean = mat[valid_rows]
    n_clean, p = clean.shape if clean.ndim == 2 else (0, 0)

    if p < 2 or n_clean < max(8, p + 3):
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(n_clean),
            min_required=max(8, p + 3),
            reason="requires >=2 predictors and n >= max(8, p+3) complete observations for VIF",
            stage="pre_model",
            family="dependence_collinearity",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    sub, sub_info, sub_chk = deterministic_subsample(
        clean,
        mode=norm_mode,
        max_fast_n=5000,
        preserve_order=True,
        seed=seed,
    )
    n = int(sub.shape[0])

    # Check extreme leverage/kurtosis in predictors; also compute rank-based Spearman VIF fallback if heavy-tailed
    max_kurt = float(np.max([abs(sp_stats.kurtosis(sub[:, j], fisher=True, bias=False)) for j in range(p)]))
    heavy_tails = max_kurt > 10.0
    kurt_chk = AssumptionCheck(
        name="predictor_elliptical_tails_for_pearson_vif",
        passed=not heavy_tails,
        metric_name="max_abs_kurtosis",
        metric_value=max_kurt,
        threshold=10.0,
        action="pearson_vif_primary" if not heavy_tails else "augmented_with_rank_spearman_vif_fallback",
    )

    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        sub[:, 0],
        allow_hac=allow_hac,
        prefer_block_bootstrap=(norm_mode == "full" and allow_hac and n <= 500),
        alpha=alpha,
    )

    work_mat = np.column_stack([sp_stats.rankdata(sub[:, j]) for j in range(p)]) if heavy_tails else sub
    vifs, max_r2, cond_index = _compute_vif_and_condition(work_mat, names)
    max_vif = max(vifs.values())
    worst_var = max(vifs, key=lambda k: vifs[k])

    # Calibrated one-sided Fisher-z exceedance test against H0: max_VIF <= vif_threshold (i.e. R_max <= sqrt(1 - 1/vif_threshold))
    r0 = math.sqrt(max(0.0, 1.0 - 1.0 / max(1.05, float(vif_threshold))))
    r_obs = math.sqrt(max(0.0, min(0.999999, max_r2)))
    n_eff = float(n) * 0.65 if (ac_info["autocorrelated"] and allow_hac) else float(n)
    dof = max(2.0, n_eff - float(p) - 1.0)
    z_vif = math.sqrt(dof) * (math.atanh(min(0.999999, r_obs)) - math.atanh(min(0.999999, r0)))
    # Incorporate severe condition-index exceedance if condition_index > condition_threshold
    if cond_index > condition_threshold and max_vif <= vif_threshold:
        z_cond = math.log(cond_index / float(condition_threshold)) * math.sqrt(dof / 2.0)
        z_stat = max(z_vif, z_cond)
    else:
        z_stat = z_vif
    p_raw = float(np.clip(sp_stats.norm.sf(z_stat), 0.0, 1.0))

    if inf_label == "block_bootstrap":
        ci_obj = moving_block_bootstrap_ci(
            work_mat,
            lambda m: math.sqrt(_compute_vif_and_condition(m, names)[1]),
            confidence_level=lv,
            n_boot=150,
            seed=seed,
        )
    else:
        ci_obj = fisher_z_ci(
            r_obs,
            n,
            confidence_level=lv,
            autocorrelated=bool(ac_info["autocorrelated"]),
            allow_hac=allow_hac,
            effective_n=(n_eff if (ac_info["autocorrelated"] and allow_hac) else None),
        )

    ci_dict = build_ci_dict(ci_obj)
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=p_raw,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=r_obs,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=r0 if decision_rule == DecisionRule.CI_EXCLUDES_NULL.value else 0.0,
        reject_means_fail=True,
    )
    pass_act = "retain_predictor_design_matrix"
    fail_act = "prune_collinear_predictors_or_apply_ridge"
    action = fail_act if dec == "fail" else pass_act
    fallback_used = "spearman_rank_vif" if heavy_tails else ac_fb
    interp = (
        f"Max VIF={max_vif:.2f} on '{worst_var}' (threshold={vif_threshold:.1f}), "
        f"condition index={cond_index:.2f} (threshold={condition_threshold:.1f}), R_max={r_obs:.3f}: "
        f"{'severe multicollinearity detected' if rejected else 'acceptable predictor conditioning'}."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest="spearman_rank_vif_cond" if heavy_tails else "pearson_vif_bkw_condition",
        stage="pre_model",
        family="dependence_collinearity",
        target=target,
        variable=variable or worst_var,
        statistic=max_vif,
        p_raw=p_raw,
        p_adj=p_raw,
        df=float(max(1, n - p)),
        effect_size=r_obs,
        effect_size_name="max_multiple_correlation_R",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), kurt_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool((not heavy_tails) and ac_chk.passed),
        fallback_used=fallback_used,
        inference_label=inf_label,
        alpha=alpha,
        decision_rule=decision_rule,
        min_effect_size=min_effect_size,
        null_value=r0 if decision_rule == DecisionRule.CI_EXCLUDES_NULL.value else 0.0,
        decision=dec,
        rejected_null=rejected,
        interpretation=interp,
        downstream_action=action,
        pass_action=pass_act,
        fail_action=fail_act,
        mode=norm_mode,
        details={
            "autocorrelation_check": ac_info,
            "condition_index": round(cond_index, 6),
            "condition_threshold": condition_threshold,
            "exceedance_z": round(z_stat, 6),
            "max_vif": round(max_vif, 6),
            "policy_evaluation_note": rule_note,
            "subsampling": sub_info,
            "vif_by_predictor": {k: round(v, 6) for k, v in vifs.items()},
            "vif_threshold": vif_threshold,
            "worst_predictor": worst_var,
        },
    )
