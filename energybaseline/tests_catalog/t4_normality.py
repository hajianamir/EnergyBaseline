"""T4 — Residual normality suite: Shapiro-Wilk (n <= 5000), Jarque-Bera, D'Agostino-Pearson K2, and Anderson-Darling."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats as sp_stats

from ..core.tcp import DecisionRule
from .ci_method import bounded_effect_bootstrap_ci, moving_block_bootstrap_ci, validate_ci_level
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

TEST_ID = "T4"
TEST_NAME = "Shapiro-Wilk, Jarque-Bera & Anderson-Darling Normality"


def _nonnormality_index(arr: np.ndarray) -> float:
    """Standardized root-mean-square departure from Gaussian skewness=0 and excess kurtosis=0."""
    if arr.size < 8 or float(np.std(arr, ddof=1)) <= 1e-12:
        return 0.0
    sk = float(sp_stats.skew(arr, bias=False))
    ku = float(sp_stats.kurtosis(arr, fisher=True, bias=False))
    return math.sqrt(sk * sk + 0.25 * ku * ku)


def _ar1_prewhiten(arr: np.ndarray) -> np.ndarray:
    c = arr - float(np.mean(arr))
    denom = float(np.dot(c[:-1], c[:-1]))
    if denom <= 1e-15:
        return c
    phi = float(np.clip(np.dot(c[:-1], c[1:]) / denom, -0.95, 0.95))
    return c[1:] - phi * c[:-1]


def run_t4_normality(
    residuals: Any,
    *,
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
    """Evaluate residual Gaussianity via Shapiro-Wilk (n<=5000), Jarque-Bera, D'Agostino K2, and Anderson-Darling."""
    lv = validate_ci_level(confidence_level)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    if not enabled:
        return make_not_run_user_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            stage="pre_model",
            family="residual_diagnostics",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    raw = np.asarray(residuals, dtype=float).reshape(-1)
    clean = raw[np.isfinite(raw)]
    if clean.size < 8 or float(np.std(clean, ddof=1)) <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(clean.size),
            min_required=8,
            reason="requires >=8 non-constant finite observations for normality tests",
            stage="pre_model",
            family="residual_diagnostics",
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
    n = int(sub.size)
    inf_label, ac_fallback, ac_chk, ac_info = resolve_autocorrelation_inference(
        sub,
        allow_hac=allow_hac,
        prefer_block_bootstrap=(norm_mode == "full" and allow_hac and n <= 800),
        alpha=alpha,
    )

    # Shapiro-Wilk assumes i.i.d. observations; when serial correlation is present & allow_hac=True,
    # evaluate innovations after AR(1) pre-whitening (documented fallback) so serial correlation doesn't masquerade as non-normality!
    test_vec = _ar1_prewhiten(sub) if (ac_info["autocorrelated"] and allow_hac and n >= 12) else sub
    fallback_used = "ar1_prewhitened_innovations" if (ac_info["autocorrelated"] and allow_hac and n >= 12) else ac_fallback

    jb_res = sp_stats.jarque_bera(test_vec)
    jb_stat = float(jb_res.statistic)
    jb_p = float(np.clip(jb_res.pvalue, 0.0, 1.0))

    if test_vec.size >= 20:
        k2_res = sp_stats.normaltest(test_vec)
        k2_stat = float(k2_res.statistic)
        k2_p = float(np.clip(k2_res.pvalue, 0.0, 1.0))
    else:
        k2_stat, k2_p = jb_stat, jb_p

    ad_res = sp_stats.anderson(test_vec, dist="norm")
    ad_stat = float(ad_res.statistic)

    sw_limit_chk = AssumptionCheck(
        name="shapiro_wilk_sample_cap",
        passed=test_vec.size <= 5000,
        metric_name="n_used",
        metric_value=int(test_vec.size),
        threshold=5000,
        action="shapiro_wilk_primary" if test_vec.size <= 5000 else "switched_to_jarque_bera_k2_large_n",
    )

    if test_vec.size <= 5000:
        sw_res = sp_stats.shapiro(test_vec)
        primary_stat = float(sw_res.statistic)
        primary_p = float(np.clip(sw_res.pvalue, 0.0, 1.0))
        primary_df = float(test_vec.size)
        subtest = "shapiro_wilk"
    else:
        primary_stat = jb_stat
        primary_p = jb_p
        primary_df = 2.0
        subtest = "jarque_bera"
        if not fallback_used:
            fallback_used = "jarque_bera_large_n"

    eff = _nonnormality_index(test_vec)
    if inf_label == "block_bootstrap":
        ci_obj = moving_block_bootstrap_ci(
            sub,
            _nonnormality_index,
            confidence_level=lv,
            n_boot=200,
            seed=seed,
        )
    else:
        ci_obj = bounded_effect_bootstrap_ci(
            test_vec,
            _nonnormality_index,
            confidence_level=lv,
            bounds=(0.0, 100.0),
            n_boot=150,
            seed=seed,
            method_tag=(
                "naive_iid_bootstrap_nonnormality"
                if inf_label == "naive"
                else "percentile_bootstrap_nonnormality_index"
            ),
        )
        if inf_label == "naive":
            ci_obj = type(ci_obj)(
                low=ci_obj.low,
                high=ci_obj.high,
                confidence_level=ci_obj.confidence_level,
                method="naive_iid_bootstrap_nonnormality (autocorrelation_uncorrected)",
                estimate=ci_obj.estimate,
                std_error=ci_obj.std_error,
                inference_label="naive",
            )

    ci_dict = build_ci_dict(ci_obj)
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=primary_p,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=eff,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=0.0,
        reject_means_fail=True,
    )
    pass_act = "proceed_gaussian_parametric_intervals"
    fail_act = "switch_to_conformal_or_nonparametric_fallback"
    action = fail_act if dec == "fail" else pass_act
    sk = float(sp_stats.skew(test_vec, bias=False))
    ku = float(sp_stats.kurtosis(test_vec, fisher=True, bias=False))
    interp = (
        f"Normality ({subtest}={primary_stat:.4f}, p_raw={primary_p:.4g}, JB={jb_stat:.3f}, A2={ad_stat:.3f}, "
        f"skew={sk:+.2f}, excess_kurt={ku:+.2f}): "
        f"{'Gaussianity rejected -> use non-parametric / conformal intervals' if rejected else 'consistent with Gaussianity'}."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest=subtest,
        stage="pre_model",
        family="residual_diagnostics",
        target=target,
        variable=variable,
        statistic=primary_stat,
        p_raw=primary_p,
        p_adj=primary_p,
        df=primary_df,
        effect_size=eff,
        effect_size_name="rms_skew_kurtosis_departure",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), sw_limit_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool(sw_limit_chk.passed and ac_chk.passed),
        fallback_used=fallback_used,
        inference_label=inf_label,
        alpha=alpha,
        decision_rule=decision_rule,
        min_effect_size=min_effect_size,
        decision=dec,
        rejected_null=rejected,
        interpretation=interp,
        downstream_action=action,
        pass_action=pass_act,
        fail_action=fail_act,
        mode=norm_mode,
        details={
            "anderson_darling_a2": round(ad_stat, 6),
            "autocorrelation_check": ac_info,
            "dagostino_k2": round(k2_stat, 6),
            "dagostino_p": round(k2_p, 8),
            "excess_kurtosis": round(ku, 6),
            "jarque_bera_p": round(jb_p, 8),
            "jarque_bera_stat": round(jb_stat, 6),
            "policy_evaluation_note": rule_note,
            "skewness": round(sk, 6),
            "subsampling": sub_info,
        },
    )
