"""T14 — Causal M&V with declared 'group' column: covariate balance (SMD), parallel pre-trends & propensity overlap."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from ..core.tcp import DecisionRule
from .ci_method import exact_t_ci, moving_block_bootstrap_ci, newey_west_se_mean, validate_ci_level
from .tc_assumptions import (
    AssumptionCheck,
    TestContractResult,
    build_ci_dict,
    deterministic_subsample,
    evaluate_contract_decision,
    make_insufficient_data_result,
    make_not_applicable_group_result,
    make_not_run_user_result,
    resolve_autocorrelation_inference,
)

TEST_ID = "T14"
TEST_NAME = "Causal M&V Group Balance, Parallel Trends & Propensity Overlap"


def _encode_binary_group(group: Any) -> tuple[np.ndarray | None, list[str]]:
    arr = np.asarray(group, dtype=object)
    valid = ~pd.isna(arr)
    uniq = sorted({str(x) for x in arr[valid]})
    if len(uniq) != 2:
        return None, uniq
    # Prefer 'treatment'/'1'/'treated' as group 1 if present
    treat_label = uniq[1]
    for cand in uniq:
        if cand.lower() in ("1", "true", "treat", "treated", "treatment", "pilot", "retrofit"):
            treat_label = cand
            break
    g_bin = np.where(np.asarray([str(x) for x in arr]) == treat_label, 1.0, 0.0)
    g_bin[~valid] = np.nan
    return g_bin, uniq


def compute_multi_group_anova_kruskal(values: Any, groups: Any) -> dict[str, Any]:
    """Multi-group comparison: One-way / Welch heteroskedastic ANOVA with Kruskal-Wallis non-parametric fallback."""
    yv = np.asarray(values, dtype=float).reshape(-1)
    gv = np.asarray(groups, dtype=object).reshape(-1)
    valid = np.isfinite(yv) & (~pd.isna(gv))
    yv, gv = yv[valid], gv[valid]
    labels = sorted({str(g) for g in gv})
    buckets = [yv[np.asarray([str(g) for g in gv]) == lb] for lb in labels]
    buckets = [b for b in buckets if b.size >= 3]
    if len(buckets) < 2:
        raise ValueError("Multi-group comparison requires >=2 groups with >=3 observations each.")

    norm_ok = all(
        (float(sp_stats.shapiro(b[:5000]).pvalue) >= 0.01 if b.size >= 8 and float(np.std(b, ddof=1)) > 1e-12 else True)
        for b in buckets
    )
    lev_p = float(sp_stats.levene(*buckets).pvalue)
    homosked_ok = lev_p >= 0.05

    f_res = sp_stats.f_oneway(*buckets)
    kw_res = sp_stats.kruskal(*buckets)
    if not norm_ok:
        method = "kruskal_wallis_nonparametric"
        stat_val, p_val = float(kw_res.statistic), float(kw_res.pvalue)
    elif not homosked_ok:
        # Welch (1951) heteroskedastic one-way F-test
        k_g = len(buckets)
        ns = np.array([b.size for b in buckets], dtype=float)
        means = np.array([np.mean(b) for b in buckets], dtype=float)
        vars_g = np.maximum(np.array([np.var(b, ddof=1) for b in buckets], dtype=float), 1e-15)
        ws = ns / vars_g
        w_sum = float(np.sum(ws))
        m_bar = float(np.sum(ws * means)) / w_sum
        lam = float(np.sum(((1.0 - ws / w_sum) ** 2) / np.maximum(ns - 1.0, 1.0)))
        f_welch = (float(np.sum(ws * ((means - m_bar) ** 2))) / float(k_g - 1)) / (
            1.0 + (2.0 * float(k_g - 2) / float(k_g * k_g - 1)) * lam
        )
        df2_w = float(k_g * k_g - 1) / max(3.0 * lam, 1e-12)
        method = "welch_heteroskedastic_anova"
        stat_val, p_val = float(max(0.0, f_welch)), float(sp_stats.f.sf(max(0.0, f_welch), k_g - 1, df2_w))
    else:
        method = "one_way_anova"
        stat_val, p_val = float(f_res.statistic), float(f_res.pvalue)

    return {
        "anova_f": float(f_res.statistic),
        "anova_p": float(f_res.pvalue),
        "kruskal_h": float(kw_res.statistic),
        "kruskal_p": float(kw_res.pvalue),
        "levene_p": lev_p,
        "method_selected": method,
        "p_value": float(np.clip(p_val, 0.0, 1.0)),
        "statistic": stat_val,
    }


def run_t14_causal_group(
    y: Any,
    covariates: Any | None = None,
    *,
    group: Any | None = None,
    pre_period_mask: Any | None = None,
    smd_threshold: float = 0.25,
    overlap_bounds: tuple[float, float] = (0.10, 0.90),
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    mode: str = "full",
    enabled: bool = True,
    target: str = "",
    variable: str = "group",
    allow_hac: bool = True,
    decision_rule: str = DecisionRule.P_ONLY.value,
    min_effect_size: float = 0.0,
    seed: int = 42,
) -> TestContractResult:
    """Execute T14 causal M&V diagnostics ONLY when a ``group`` column is supplied."""
    lv = validate_ci_level(confidence_level)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    if not enabled:
        return make_not_run_user_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            stage="causal_group",
            family="causal_group",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    if group is None:
        return make_not_applicable_group_result(target=target, mode=norm_mode, confidence_level=lv)

    y_arr = np.asarray(y, dtype=float).reshape(-1)
    g_bin, group_labels = _encode_binary_group(group)
    if g_bin is None or g_bin.size != y_arr.size:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(y_arr.size),
            min_required=12,
            reason=f"T14 requires a binary treatment/control group column aligned with target (found labels={group_labels})",
            stage="causal_group",
            family="causal_group",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    if covariates is not None:
        X_raw = np.asarray(covariates, dtype=float)
        if X_raw.ndim == 1:
            X_raw = X_raw.reshape(-1, 1)
    else:
        X_raw = np.linspace(0.0, 1.0, num=y_arr.size, dtype=float).reshape(-1, 1)

    pre_mask = (
        np.asarray(pre_period_mask, dtype=bool).reshape(-1)
        if pre_period_mask is not None
        else np.ones(y_arr.size, dtype=bool)
    )
    valid = np.isfinite(y_arr) & np.isfinite(g_bin) & np.all(np.isfinite(X_raw), axis=1) & pre_mask
    yc = y_arr[valid]
    gc = g_bin[valid]
    Xc = X_raw[valid]

    n1_raw = int(np.sum(gc == 1.0))
    n0_raw = int(np.sum(gc == 0.0))
    if min(n1_raw, n0_raw) < 6:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(yc.size),
            min_required=12,
            reason=f"requires >=6 observations in both treatment ({n1_raw}) and control ({n0_raw}) pre-period groups",
            stage="causal_group",
            family="causal_group",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    idx_sub, sub_info, sub_chk = deterministic_subsample(
        np.arange(yc.size),
        mode=norm_mode,
        max_fast_n=5000,
        preserve_order=True,
        seed=seed,
    )
    ys, gs, Xs = yc[idx_sub], gc[idx_sub], Xc[idx_sub]
    n = int(ys.size)
    mask1 = gs == 1.0
    mask0 = gs == 0.0

    # 1. Covariate Balance (SMD + Welch t-test with Mann-Whitney U & Brunner-Munzel fallback when non-normal)
    p_cov = int(Xs.shape[1])
    smds: list[float] = []
    bal_pvals: list[float] = []
    used_nonparam_balance = False

    for j in range(p_cov):
        x1 = Xs[mask1, j]
        x0 = Xs[mask0, j]
        v1 = float(np.var(x1, ddof=1))
        v0 = float(np.var(x0, ddof=1))
        pooled_sd = math.sqrt(max(0.5 * (v1 + v0), 1e-15))
        smds.append(abs(float(np.mean(x1)) - float(np.mean(x0))) / pooled_sd)

        sw1 = float(sp_stats.shapiro(x1[:5000]).pvalue) if x1.size >= 8 and v1 > 1e-12 else 1.0
        sw0 = float(sp_stats.shapiro(x0[:5000]).pvalue) if x0.size >= 8 and v0 > 1e-12 else 1.0
        if min(sw1, sw0) < 0.01:
            used_nonparam_balance = True
            bm = sp_stats.brunnermunzel(x1, x0)
            mw = sp_stats.mannwhitneyu(x1, x0, alternative="two-sided")
            p_j = float(bm.pvalue) if np.isfinite(bm.pvalue) else float(mw.pvalue)
        else:
            wt = sp_stats.ttest_ind(x1, x0, equal_var=False)
            p_j = float(wt.pvalue) if np.isfinite(wt.pvalue) else 1.0
        bal_pvals.append(float(np.clip(p_j, 0.0, 1.0)))

    max_smd = max(smds)
    min_bal_p = min(bal_pvals)
    bal_chk = AssumptionCheck(
        name="gaussian_group_covariates_for_welch_t",
        passed=not used_nonparam_balance,
        metric_name="used_brunner_munzel_mann_whitney",
        metric_value=str(used_nonparam_balance),
        threshold="False",
        action="welch_t_balance" if not used_nonparam_balance else "switched_to_brunner_munzel_mann_whitney_fallback",
    )

    # 2. Pre-Period Parallel Trends: y_i = b0 + b1*G_i + b2*t_i + b3*(G_i * t_i) + u_i (test H0: b3 = 0)
    # Build group-local normalized time index in [0, 1]
    t_norm = np.zeros(n, dtype=float)
    t_norm[mask1] = np.linspace(0.0, 1.0, num=int(np.sum(mask1)))
    t_norm[mask0] = np.linspace(0.0, 1.0, num=int(np.sum(mask0)))
    Z_pt = np.column_stack([np.ones(n, dtype=float), gs, t_norm, gs * t_norm])
    beta_pt, *_ = np.linalg.lstsq(Z_pt, ys, rcond=None)
    u_pt = ys - Z_pt @ beta_pt
    slope_diff = float(beta_pt[3])

    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        u_pt,
        allow_hac=allow_hac,
        prefer_block_bootstrap=(norm_mode == "full" and allow_hac and n <= 400),
        alpha=alpha,
    )

    ztz_inv = np.linalg.pinv(Z_pt.T @ Z_pt, rcond=1e-12)
    df_pt = max(1, n - 4)
    if ac_info["autocorrelated"] and allow_hac:
        se_nw, bw = newey_west_se_mean(u_pt * (gs * t_norm - float(np.mean(gs * t_norm))))
        var_gt = max(float(np.var(gs * t_norm, ddof=1)), 1e-12)
        se_b3 = max(1e-8, se_nw / var_gt)
    else:
        mse_pt = max(float(np.dot(u_pt, u_pt)) / float(df_pt), 1e-18)
        se_b3 = math.sqrt(max(1e-18, mse_pt * float(ztz_inv[3, 3])))

    t_pt = slope_diff / se_b3
    p_parallel = float(np.clip(2.0 * sp_stats.t.sf(abs(t_pt), df=df_pt), 0.0, 1.0))

    # 3. Propensity Score Overlap (Positivity in [overlap_low, overlap_high])
    Xs_scaled = StandardScaler().fit_transform(Xs)
    clf = LogisticRegression(C=1.0, max_iter=250, random_state=int(seed))
    clf.fit(Xs_scaled, gs.astype(int))
    ps = clf.predict_proba(Xs_scaled)[:, 1]
    lo_b, hi_b = overlap_bounds
    in_overlap = (ps >= lo_b) & (ps <= hi_b)
    overlap_share = float(np.mean(in_overlap))
    overlap_ok = overlap_share >= 0.75

    ov_chk = AssumptionCheck(
        name="propensity_positivity_overlap",
        passed=overlap_ok,
        metric_name="overlap_share_in_bounds",
        metric_value=overlap_share,
        threshold=0.75,
        action="positivity_satisfied" if overlap_ok else "insufficient_common_support",
    )

    # Synthesize causal validity p_raw: fail if parallel trends violated OR severe balance/overlap violation
    # (Bonferroni-corrected across the two inferential checks: parallel trends & worst covariate balance)
    p_inferential = min(1.0, 2.0 * min(p_parallel, min_bal_p))
    if not overlap_ok or max_smd > smd_threshold:
        # Express severe positivity / SMD violation on p scale when inferential p_parallel alone was noisy
        z_smd = max(0.0, (max_smd - smd_threshold) * math.sqrt(float(min(n1_raw, n0_raw))))
        p_smd = float(sp_stats.norm.sf(z_smd)) if max_smd > smd_threshold else 1.0
        p_ov = 0.001 if not overlap_ok else 1.0
        p_raw = min(p_inferential, p_smd, p_ov)
    else:
        p_raw = p_parallel
    p_raw = float(np.clip(p_raw, 0.0, 1.0))

    sd_y = max(float(np.std(ys, ddof=1)), 1e-12)
    std_slope_diff = abs(slope_diff) / sd_y
    eff = max(max_smd, std_slope_diff)

    if inf_label == "block_bootstrap" and n <= 400:
        stacked = np.column_stack([ys, gs, t_norm])

        def _pt_eff(m: np.ndarray) -> float:
            Z_m = np.column_stack([np.ones(m.shape[0]), m[:, 1], m[:, 2], m[:, 1] * m[:, 2]])
            b_m, *_ = np.linalg.lstsq(Z_m, m[:, 0], rcond=None)
            return abs(float(b_m[3])) / max(float(np.std(m[:, 0], ddof=1)), 1e-12)

        ci_obj = moving_block_bootstrap_ci(stacked, _pt_eff, confidence_level=lv, n_boot=120, seed=seed)
    else:
        se_eff = max(1e-6, se_b3 / sd_y)
        ci_obj = exact_t_ci(
            eff,
            se_eff,
            df=float(df_pt),
            confidence_level=lv,
            method_name=(
                "naive_did_parallel_trends_t (autocorrelation_uncorrected)"
                if inf_label == "naive"
                else ("hac_did_parallel_trends_t" if (ac_info["autocorrelated"] and allow_hac) else "did_parallel_trends_t")
            ),
            inference_label=inf_label,
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
    pass_act = "proceed_causal_did_mv"
    fail_act = "reweight_match_or_reject_control_group"
    action = fail_act if dec == "fail" else pass_act
    fallback_used = "brunner_munzel_mann_whitney" if used_nonparam_balance else ac_fb
    interp = (
        f"Causal M&V check: max |SMD|={max_smd:.3f} (limit={smd_threshold:.2f}), "
        f"parallel pre-trends t={t_pt:+.3f} (p={p_parallel:.4g}), "
        f"propensity overlap in [{lo_b:.2f},{hi_b:.2f}]={100.0 * overlap_share:.1f}%: "
        f"{'causal identification assumptions violated' if rejected else 'control group balanced with parallel pre-trends'}."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest="causal_balance_parallel_trends_overlap",
        stage="causal_group",
        family="causal_group",
        target=target,
        variable=variable,
        statistic=t_pt,
        p_raw=p_raw,
        p_adj=p_raw,
        df=float(df_pt),
        effect_size=eff,
        effect_size_name="max_smd_or_std_pretrend_divergence",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), bal_chk.to_dict(), ov_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool((not used_nonparam_balance) and overlap_ok and ac_chk.passed),
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
            "autocorrelation_check": ac_info,
            "balance_min_p": round(min_bal_p, 8),
            "group_labels": group_labels,
            "max_smd": round(max_smd, 6),
            "n_control": int(np.sum(mask0)),
            "n_treatment": int(np.sum(mask1)),
            "overlap_bounds": [lo_b, hi_b],
            "overlap_share": round(overlap_share, 6),
            "parallel_trends_p": round(p_parallel, 8),
            "parallel_trends_slope_diff": round(slope_diff, 8),
            "parallel_trends_t": round(t_pt, 8),
            "policy_evaluation_note": rule_note,
            "smds": [round(s, 6) for s in smds],
            "subsampling": sub_info,
        },
    )
