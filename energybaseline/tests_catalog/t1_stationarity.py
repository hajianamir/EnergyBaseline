"""T1 — Joint Augmented Dickey-Fuller (ADF) and KPSS stationarity test with 2x2 decision matrix."""

from __future__ import annotations

import math
from typing import Any
import warnings

import numpy as np
from statsmodels.tools.sm_exceptions import InterpolationWarning
from statsmodels.tsa.stattools import adfuller, kpss

from ..core.tcp import DecisionRule
from .ci_method import exact_t_ci, moving_block_bootstrap_ci, newey_west_se_mean, validate_ci_level
from .tc_assumptions import (
    AssumptionCheck,
    TestContractResult,
    build_ci_dict,
    deterministic_subsample,
    evaluate_contract_decision,
    make_insufficient_data_result,
    make_not_run_user_result,
    resolve_autocorrelation_inference,
    stationarity_decision_matrix,
)

TEST_ID = "T1"
TEST_NAME = "ADF + KPSS Joint Stationarity"


def _ar1_phi(arr: np.ndarray) -> float:
    if arr.size < 3:
        return 0.0
    x = arr[:-1] - float(np.mean(arr[:-1]))
    y = arr[1:] - float(np.mean(arr[1:]))
    denom = float(np.dot(x, x))
    if denom <= 1e-15:
        return 0.0
    return float(np.clip(np.dot(x, y) / denom, -1.2, 1.2))


def run_t1_stationarity(
    series: Any,
    *,
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    mode: str = "full",
    enabled: bool = True,
    target: str = "",
    variable: str = "",
    regression: str = "c",
    max_lags: int | None = None,
    allow_hac: bool = True,
    decision_rule: str = DecisionRule.P_ONLY.value,
    min_effect_size: float = 0.0,
    seed: int = 42,
) -> TestContractResult:
    """Execute joint ADF + KPSS stationarity test and return ``TestContractResult``."""
    lv = validate_ci_level(confidence_level)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    if not enabled:
        return make_not_run_user_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            stage="pre_model",
            family="stationarity_trend",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    raw = np.asarray(series, dtype=float).reshape(-1)
    clean = raw[np.isfinite(raw)]
    if clean.size < 12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(clean.size),
            min_required=12,
            reason="fewer than 12 finite observations for ADF+KPSS",
            stage="pre_model",
            family="stationarity_trend",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    sub, sub_info, sub_chk = deterministic_subsample(
        clean,
        mode=norm_mode,
        max_fast_n=4000,
        preserve_order=True,
        seed=seed,
    )
    n = int(sub.size)
    std_val = float(np.std(sub, ddof=1))
    if std_val <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=n,
            min_required=12,
            reason="zero or near-zero series variance (constant signal)",
            stage="pre_model",
            family="stationarity_trend",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    inf_label, fallback, ac_chk, ac_info = resolve_autocorrelation_inference(
        sub,
        allow_hac=allow_hac,
        prefer_block_bootstrap=(norm_mode == "full" and allow_hac and n <= 500),
        alpha=alpha,
    )
    var_chk = AssumptionCheck(
        name="non_degenerate_variance",
        passed=True,
        metric_name="std",
        metric_value=std_val,
        threshold=1e-12,
        action="proceed_adf_kpss",
    )

    adf_reg = regression if regression in ("c", "ct", "n") else "c"
    kpss_reg = "ct" if adf_reg == "ct" else "c"
    autolag = None if max_lags is not None else "AIC"
    adf_res = adfuller(sub, maxlag=max_lags, regression=adf_reg, autolag=autolag)
    adf_stat = float(adf_res[0])
    adf_p = float(np.clip(adf_res[1], 0.0, 1.0))
    used_lag = int(adf_res[2])
    n_obs_adf = int(adf_res[3])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InterpolationWarning)
        kpss_nlags: str | int = "auto" if allow_hac else 0
        kpss_stat_raw, kpss_p_raw, kpss_lags, _ = kpss(sub, regression=kpss_reg, nlags=kpss_nlags)
    kpss_stat = float(kpss_stat_raw)
    kpss_p = float(np.clip(kpss_p_raw, 0.0, 1.0))

    matrix = stationarity_decision_matrix(adf_p, kpss_p, alpha=alpha)
    phi_1 = _ar1_phi(sub)
    # Effect size: persistence index |phi_1| in [0, 1+] (near 1 indicates unit-root persistence)
    persistence = abs(phi_1)

    if norm_mode == "full" and allow_hac and n <= 600:
        ci_obj = moving_block_bootstrap_ci(
            sub,
            lambda a: abs(_ar1_phi(a)),
            confidence_level=lv,
            n_boot=250,
            seed=seed,
        )
    else:
        resid_ar = sub[1:] - phi_1 * sub[:-1]
        if allow_hac:
            se_u, bw = newey_west_se_mean(resid_ar)
            se_phi = max(1e-6, se_u / max(std_val, 1e-9))
            m_name = f"hac_ar1_persistence_t(bw={bw})"
        else:
            se_phi = math.sqrt(max(1e-8, (1.0 - min(0.999, phi_1 * phi_1)) / float(max(2, n - 2))))
            m_name = (
                "naive_ols_ar1_t (autocorrelation_uncorrected)"
                if inf_label == "naive"
                else "ols_ar1_student_t"
            )
        ci_obj = exact_t_ci(
            persistence,
            se_phi,
            df=max(1, n_obs_adf - used_lag - 2),
            confidence_level=lv,
            method_name=m_name,
            inference_label=inf_label,
        )

    # Synthesize single conservative p_raw where small p_raw rejects stationarity:
    # Under stationary H0 (KPSS), small kpss_p rejects; if ADF fails to reject unit root (adf_p > alpha),
    # effective non-stationarity evidence p_nonstat = min(kpss_p, max(0.0, 1.0 - adf_p)).
    if matrix["cell"] == "stationary":
        p_raw = max(float(alpha) + 1e-6, min(1.0, kpss_p)) if kpss_p > alpha else kpss_p
    else:
        p_raw = min(kpss_p, max(1e-6, 1.0 - adf_p)) if kpss_p <= alpha else min(alpha * 0.5, max(1e-6, 1.0 - adf_p))
    p_raw = float(np.clip(p_raw, 0.0, 1.0))

    ci_dict = build_ci_dict(ci_obj)
    if decision_rule == DecisionRule.P_ONLY.value:
        dec = str(matrix["decision"])
        rejected = dec == "fail"
        rule_note = str(matrix["summary"])
    else:
        dec, rejected, rule_note = evaluate_contract_decision(
            p_for_decision=p_raw,
            alpha=alpha,
            decision_rule=decision_rule,
            effect_size=persistence,
            min_effect_size=min_effect_size,
            ci=ci_dict,
            null_value=0.0,
            reject_means_fail=True,
        )

    pass_act = "proceed_stationary"
    fail_act = str(matrix["downstream_action"])
    action = fail_act if dec == "fail" else pass_act
    interp = (
        f"{matrix['summary']} (ADF={adf_stat:.3f}, p_ADF={adf_p:.4g}; "
        f"KPSS={kpss_stat:.3f}, p_KPSS={kpss_p:.4g}; |phi_1|={persistence:.3f})."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest="adf_kpss_2x2",
        stage="pre_model",
        family="stationarity_trend",
        target=target,
        variable=variable,
        statistic=adf_stat,
        p_raw=p_raw,
        p_adj=p_raw,
        df=float(max(1, n_obs_adf - used_lag - 2)),
        effect_size=persistence,
        effect_size_name="ar1_persistence_abs_phi1",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), var_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool(ac_chk.passed),
        fallback_used=fallback,
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
            "adf_lags": used_lag,
            "adf_p": round(adf_p, 8),
            "adf_statistic": round(adf_stat, 8),
            "autocorrelation_check": ac_info,
            "kpss_lags": int(kpss_lags),
            "kpss_p": round(kpss_p, 8),
            "kpss_statistic": round(kpss_stat, 8),
            "lock_custom_matrix_decision": True,
            "matrix_cell": matrix["cell"],
            "matrix_force_fail": bool(matrix["decision"] == "fail" and decision_rule == DecisionRule.P_ONLY.value),
            "policy_evaluation_note": rule_note,
            "subsampling": sub_info,
        },
    )
