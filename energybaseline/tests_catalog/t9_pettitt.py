"""T9 — Non-parametric Pettitt rank change-point test (validated against R trend::pettitt.test reference)."""

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

TEST_ID = "T9"
TEST_NAME = "Pettitt Non-Parametric Change-Point Test"


def compute_pettitt_exact(arr: np.ndarray) -> dict[str, Any]:
    """O(T log T) exact Pettitt (1979) statistic matching ``trend::pettitt.test`` in R.

    Identity: ``U_{t, T} = sum_{i=1}^t sum_{j=t+1}^T sgn(y_i - y_j) = 2 * sum_{i=1}^t R_i - t * (T + 1)``,
    where ``R_i`` is the average mid-rank of ``y_i`` in ``1..T``.
    """
    T = int(arr.size)
    if T < 4:
        raise ValueError("Pettitt test requires T >= 4.")
    ranks = sp_stats.rankdata(arr, method="average")
    cum_ranks = np.cumsum(ranks[:-1])
    t_idx = np.arange(1, T, dtype=float)
    u_seq = 2.0 * cum_ranks - t_idx * float(T + 1)
    abs_u = np.abs(u_seq)
    best_pos = int(np.argmax(abs_u))
    k_stat = float(abs_u[best_pos])
    change_point_1based = best_pos + 1  # Matches R trend::pettitt.test$estimate (1-based index of end of first segment)
    p_raw = min(1.0, 2.0 * math.exp((-6.0 * k_stat * k_stat) / (float(T) ** 3 + float(T) ** 2)))

    med_1 = float(np.median(arr[:change_point_1based]))
    med_2 = float(np.median(arr[change_point_1based:]))
    mad_scale = float(np.median(np.abs(arr - np.median(arr)))) * 1.4826
    scale = mad_scale if mad_scale > 1e-12 else float(np.std(arr, ddof=1))
    norm_shift = (med_2 - med_1) / max(scale, 1e-12)
    rank_biserial = float(-u_seq[best_pos]) / float(change_point_1based * (T - change_point_1based))

    return {
        "change_point_1based": change_point_1based,
        "k_statistic": k_stat,
        "median_after": med_2,
        "median_before": med_1,
        "normalized_median_shift": norm_shift,
        "p_raw": p_raw,
        "rank_biserial": rank_biserial,
        "u_at_break": float(u_seq[best_pos]),
    }


def run_t9_pettitt(
    series: Any,
    *,
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    mode: str = "full",
    enabled: bool = True,
    target: str = "",
    variable: str = "",
    allow_hac: bool = True,
    use_asymptotic_formula: bool = False,
    decision_rule: str = DecisionRule.P_ONLY.value,
    min_effect_size: float = 0.0,
    seed: int = 42,
) -> TestContractResult:
    """Execute Pettitt non-parametric change-point test (with pre-whitened / block-bootstrap fallback under AR)."""
    lv = validate_ci_level(confidence_level)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    if not enabled:
        return make_not_run_user_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            stage="pre_model",
            family="breaks_regimes",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    raw = np.asarray(series, dtype=float).reshape(-1)
    clean = raw[np.isfinite(raw)]
    if clean.size < 10 or float(np.std(clean, ddof=1)) <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(clean.size),
            min_required=10,
            reason="requires >=10 non-constant finite observations for Pettitt rank change-point test",
            stage="pre_model",
            family="breaks_regimes",
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
    T = int(sub.size)
    exact = compute_pettitt_exact(sub)
    cp = int(exact["change_point_1based"])

    # Assess serial correlation on within-regime demeaned series so a genuine step shift isn't misread as AR(1)
    demeaned_regimes = np.concatenate([sub[:cp] - np.median(sub[:cp]), sub[cp:] - np.median(sub[cp:])])
    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        demeaned_regimes,
        allow_hac=allow_hac,
        prefer_block_bootstrap=allow_hac,
        alpha=alpha,
    )
    tie_share = 1.0 - float(np.unique(sub).size) / float(T)
    tie_chk = AssumptionCheck(
        name="continuous_distribution_low_ties",
        passed=tie_share <= 0.35,
        metric_name="tie_fraction",
        metric_value=tie_share,
        threshold=0.35,
        action="pettitt_asymptotic_rank" if tie_share <= 0.35 else "permutation_bootstrap_p_fallback",
    )

    k_stat = float(exact["k_statistic"])
    p_asym = float(exact["p_raw"])

    # When within-regime autocorrelation or heavy ties are present (and not forcing asymptotic R reference mode),
    # calibrate p-value via moving-block / permutation bootstrap!
    need_boot_p = (not use_asymptotic_formula) and (
        (ac_info["autocorrelated"] and allow_hac) or tie_share > 0.35
    )
    if need_boot_p:
        rng = np.random.default_rng(int(seed))
        n_boot = 199 if norm_mode == "full" else 99
        b_len = max(2, int(round(T ** (1.0 / 3.0))))
        n_blocks = int(math.ceil(float(T) / float(b_len)))
        ext = np.concatenate([demeaned_regimes, demeaned_regimes[: b_len - 1]])
        offsets = np.arange(b_len)
        exceed = 0
        for _ in range(n_boot):
            if ac_info["autocorrelated"] and allow_hac:
                starts = rng.integers(0, T, size=n_blocks)
                idx = (starts[:, None] + offsets[None, :]).reshape(-1)[:T]
                y_star = ext[idx]
            else:
                y_star = rng.permutation(sub)
            if compute_pettitt_exact(y_star)["k_statistic"] >= k_stat - 1e-12:
                exceed += 1
        p_raw = float((exceed + 1) / (n_boot + 1))
        fallback_used = "moving_block_bootstrap_pettitt" if (ac_info["autocorrelated"] and allow_hac) else "permutation_pettitt"
        subtest = "pettitt_bootstrap_fallback"
    else:
        p_raw = p_asym
        fallback_used = ac_fb
        subtest = "pettitt_rank_exact"

    eff = float(exact["rank_biserial"])
    if inf_label == "block_bootstrap" and T <= 600:
        ci_obj = moving_block_bootstrap_ci(
            sub,
            lambda a: float(compute_pettitt_exact(a)["rank_biserial"]),
            confidence_level=lv,
            n_boot=150,
            seed=seed,
        )
    else:
        ci_obj = bounded_effect_bootstrap_ci(
            sub,
            lambda a: float(compute_pettitt_exact(a)["rank_biserial"]),
            confidence_level=lv,
            bounds=(-1.0, 1.0),
            n_boot=120,
            seed=seed,
            method_tag=(
                "naive_iid_bootstrap_rank_biserial"
                if inf_label == "naive"
                else "percentile_bootstrap_rank_biserial"
            ),
        )
        if inf_label == "naive":
            ci_obj = type(ci_obj)(
                low=ci_obj.low,
                high=ci_obj.high,
                confidence_level=ci_obj.confidence_level,
                method="naive_iid_bootstrap_rank_biserial (autocorrelation_uncorrected)",
                estimate=ci_obj.estimate,
                std_error=ci_obj.std_error,
                inference_label="naive",
            )

    ci_dict = build_ci_dict(ci_obj)
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=p_raw,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=eff,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=0.0,
        reject_means_fail=True,
    )
    pass_act = "retain_homogeneous_series"
    fail_act = "flag_nonparametric_level_shift_at_tau"
    action = fail_act if dec == "fail" else pass_act
    interp = (
        f"Pettitt K_T={k_stat:.2f} at tau={cp} (p_raw={p_raw:.4g}, asymptotic_p={p_asym:.4g}, "
        f"median {exact['median_before']:.3g}->{exact['median_after']:.3g}, rank_biserial={eff:+.3f}): "
        f"{'significant non-parametric change-point detected' if rejected else 'no significant level shift'}."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest=subtest,
        stage="pre_model",
        family="breaks_regimes",
        target=target,
        variable=variable,
        statistic=k_stat,
        p_raw=p_raw,
        p_adj=p_raw,
        df=float(T - 1),
        effect_size=eff,
        effect_size_name="pettitt_rank_biserial_r",
        ci=ci_dict,
        n_used=T,
        assumptions_checked=(sub_chk.to_dict(), tie_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool(tie_chk.passed and ac_chk.passed),
        fallback_used=fallback_used,
        inference_label=inf_label if inf_label != "standard" else "nonparametric_exact",
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
            "asymptotic_p": round(p_asym, 10),
            "autocorrelation_check": ac_info,
            "change_point_1based": cp,
            "median_after": round(float(exact["median_after"]), 6),
            "median_before": round(float(exact["median_before"]), 6),
            "normalized_median_shift": round(float(exact["normalized_median_shift"]), 6),
            "policy_evaluation_note": rule_note,
            "subsampling": sub_info,
            "tie_fraction": round(tie_share, 6),
        },
    )
