"""T3 — Residual / series serial autocorrelation: Ljung-Box multi-lag Q, Durbin-Watson, and rank fallback."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats as sp_stats
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.stats.stattools import durbin_watson

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
)

TEST_ID = "T3"
TEST_NAME = "Ljung-Box & Durbin-Watson Autocorrelation"


def _lag1_corr(arr: np.ndarray) -> float:
    if arr.size < 3:
        return 0.0
    c = arr - float(np.mean(arr))
    d = float(np.dot(c, c))
    if d <= 1e-15:
        return 0.0
    return float(np.clip(np.dot(c[1:], c[:-1]) / d, -0.999999, 0.999999))


def run_t3_autocorrelation(
    residuals: Any,
    *,
    max_lags: int | None = None,
    lags: int | None = None,
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
    """Test zero serial autocorrelation via Ljung-Box Q + Durbin-Watson (with rank Ljung-Box fallback on heavy tails)."""
    if max_lags is None and lags is not None:
        max_lags = lags
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

    raw = np.asarray(residuals, dtype=float).reshape(-1)
    clean = raw[np.isfinite(raw)]
    if clean.size < 10 or float(np.std(clean, ddof=1)) <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(clean.size),
            min_required=10,
            reason="requires >=10 non-constant observations for Ljung-Box / Durbin-Watson",
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
    n = int(sub.size)
    n_lags = int(max_lags) if max_lags is not None else min(10, max(1, n // 5))
    n_lags = min(max(1, n_lags), n - 2)

    # Check finite-fourth-moment / outlier assumption; use rank-based Ljung-Box fallback under extreme kurtosis
    kurt = float(sp_stats.kurtosis(sub, fisher=True, bias=False))
    heavy_tails = abs(kurt) > 8.0
    tail_chk = AssumptionCheck(
        name="finite_fourth_moment_homoscedastic_innovations",
        passed=not heavy_tails,
        metric_name="excess_kurtosis",
        metric_value=kurt,
        threshold=8.0,
        action="standard_ljung_box" if not heavy_tails else "switched_to_rank_ljung_box_fallback",
    )

    work_for_lb = sp_stats.rankdata(sub, method="average") if heavy_tails else sub
    lb_df = acorr_ljungbox(work_for_lb, lags=[n_lags], return_df=True)
    q_stat = float(lb_df["lb_stat"].iloc[0])
    p_raw = float(np.clip(lb_df["lb_pvalue"].iloc[0], 0.0, 1.0))

    dw_stat = float(durbin_watson(sub - float(np.mean(sub))))
    rho1 = _lag1_corr(sub)
    is_ac = abs(rho1) >= 0.20 or p_raw <= alpha

    if is_ac and not allow_hac:
        inf_label = "naive"
        ci_obj = fisher_z_ci(rho1, n, confidence_level=lv, autocorrelated=True, allow_hac=False)
    elif is_ac and norm_mode == "full" and n <= 800:
        inf_label = "block_bootstrap"
        ci_obj = moving_block_bootstrap_ci(
            sub,
            _lag1_corr,
            confidence_level=lv,
            n_boot=250,
            seed=seed,
        )
    elif is_ac:
        inf_label = "hac_robust"
        n_eff = max(4.0, float(n) * (1.0 - rho1 * rho1) / max(0.05, 1.0 + rho1 * rho1))
        ci_obj = fisher_z_ci(rho1, n, confidence_level=lv, autocorrelated=True, allow_hac=True, effective_n=n_eff)
    else:
        inf_label = "standard"
        ci_obj = fisher_z_ci(rho1, n, confidence_level=lv, autocorrelated=False, allow_hac=True)

    ac_ci_chk = AssumptionCheck(
        name="autocorrelation_ci_calibration",
        passed=not (is_ac and not allow_hac),
        metric_name="lag1_r",
        metric_value=rho1,
        threshold=0.20,
        action="labelled_naive_uncorrected_autocorrelation" if inf_label == "naive" else f"ci_via_{inf_label}",
    )

    ci_dict = build_ci_dict(ci_obj)
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=p_raw,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=rho1,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=0.0,
        reject_means_fail=True,
    )
    pass_act = "proceed_iid_errors"
    fail_act = "apply_hac_or_add_autoregressive_lags"
    action = fail_act if dec == "fail" else pass_act
    subtest = "rank_ljung_box_dw" if heavy_tails else "ljung_box_dw"
    fallback = "rank_ljung_box" if heavy_tails else ("moving_block_bootstrap" if inf_label == "block_bootstrap" else "")
    interp = (
        f"Ljung-Box Q({n_lags})={q_stat:.3f} (p_raw={p_raw:.4g}), Durbin-Watson={dw_stat:.3f}, "
        f"lag-1 r={rho1:+.3f}: {'serial autocorrelation present' if rejected else 'no significant autocorrelation'}."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest=subtest,
        stage="pre_model",
        family="dependence_collinearity",
        target=target,
        variable=variable,
        statistic=q_stat,
        p_raw=p_raw,
        p_adj=p_raw,
        df=float(n_lags),
        effect_size=rho1,
        effect_size_name="lag1_autocorrelation_r",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), tail_chk.to_dict(), ac_ci_chk.to_dict()),
        assumptions_met=bool((not heavy_tails) and ac_ci_chk.passed),
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
            "durbin_watson": round(dw_stat, 6),
            "excess_kurtosis": round(kurt, 6),
            "lag1_r": round(rho1, 6),
            "lags_tested": n_lags,
            "ljung_box_p": round(p_raw, 8),
            "ljung_box_q": round(q_stat, 8),
            "max_bartlett_band": round(1.96 / math.sqrt(float(n)), 6),
            "policy_evaluation_note": rule_note,
            "subsampling": sub_info,
        },
    )
