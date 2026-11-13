"""T10 — OOF Diebold-Mariano / Harvey-Leybourne-Newbold (HLN) forecast comparison & Model Confidence Set (R references)."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import stats as sp_stats

from ..core.tcp import DecisionRule
from .ci_method import exact_t_ci, mean_ci_with_autocorrelation_guard, moving_block_bootstrap_ci, validate_ci_level
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

TEST_ID = "T10"
TEST_NAME = "Diebold-Mariano (HLN) OOF Forecast Equivalence & MCS"


def compute_dm_hln_exact(
    errors_1: np.ndarray,
    errors_2: np.ndarray,
    *,
    h: int = 1,
    power: int = 2,
) -> dict[str, Any]:
    """Exact Harvey-Leybourne-Newbold (1997) DM test matching ``forecast::dm.test(..., varestimator='acf')`` in R."""
    e1 = np.asarray(errors_1, dtype=float).reshape(-1)
    e2 = np.asarray(errors_2, dtype=float).reshape(-1)
    if e1.size != e2.size:
        raise ValueError(f"OOF error vectors must have equal length ({e1.size} vs {e2.size}).")
    T = int(e1.size)
    horizon = max(1, int(h))
    if T <= horizon + 2:
        raise ValueError(f"DM test requires T > h + 2, got T={T}, h={horizon}.")

    d = (np.abs(e1) ** power) - (np.abs(e2) ** power)
    d_bar = float(np.mean(d))
    u = d - d_bar

    # Sample autocovariances gamma_k = (1/T) * sum_{t=k+1}^T u_t * u_{t-k}, matching R stats::acf(..., type='covariance', demean=TRUE)
    gamma_0 = float(np.dot(u, u)) / float(T)
    d_var_long = gamma_0
    for k in range(1, horizon):
        gamma_k = float(np.dot(u[k:], u[:-k])) / float(T)
        d_var_long += 2.0 * gamma_k

    var_d_bar = d_var_long / float(T)
    if var_d_bar <= 1e-18:
        # Fallback to Bartlett Newey-West positive variance if rectangular truncated sum is non-positive
        bw = max(1, horizon - 1)
        d_var_bart = gamma_0
        for k in range(1, bw + 1):
            w = 1.0 - float(k) / float(bw + 1)
            d_var_bart += 2.0 * w * (float(np.dot(u[k:], u[:-k])) / float(T))
        var_d_bar = max(d_var_bart, 1e-18) / float(T)

    dm_raw = d_bar / math.sqrt(var_d_bar)
    hln_factor = math.sqrt(
        max(1e-9, (float(T) + 1.0 - 2.0 * float(horizon) + float(horizon * (horizon - 1)) / float(T)) / float(T))
    )
    hln_stat = dm_raw * hln_factor
    df = T - 1
    p_raw = float(np.clip(2.0 * sp_stats.t.sf(abs(hln_stat), df=df), 0.0, 1.0))
    sd_d = float(np.std(d, ddof=1))
    cohen_d = d_bar / max(sd_d, 1e-12)

    return {
        "cohen_d": cohen_d,
        "df": df,
        "dm_raw": dm_raw,
        "h": horizon,
        "hln_factor": hln_factor,
        "hln_statistic": hln_stat,
        "loss_diff": d,
        "mean_loss_diff": d_bar,
        "p_raw": p_raw,
        "power": power,
        "se_mean_loss_diff": math.sqrt(var_d_bar),
    }


def compute_mcs_exact(
    loss_matrix: Any,
    model_names: Sequence[str] | None = None,
    *,
    alpha: float = 0.10,
    block_length: int = 3,
    n_boot: int = 500,
    boot_indices: np.ndarray | None = None,
    seed: int = 2024,
) -> dict[str, Any]:
    """Hansen, Lunde & Nason (2011) Model Confidence Set (MCS) using T_max sequential elimination.

    ``loss_matrix`` has shape ``(T, M)`` where entry ``(t, m)`` is model ``m``'s loss at step ``t``.
    Accepts optional pre-generated ``boot_indices`` ``(B, T)`` (0-based) for exact R-script parity.
    """
    L = np.asarray(loss_matrix, dtype=float)
    if L.ndim != 2 or L.shape[0] < 8 or L.shape[1] < 2:
        raise ValueError("MCS requires a 2-D loss matrix of shape (T >= 8, M >= 2).")
    T, M = L.shape
    names = list(model_names) if model_names is not None else [f"model_{j + 1}" for j in range(M)]
    if len(names) != M:
        raise ValueError("model_names length must match loss_matrix columns.")

    if boot_indices is not None:
        idx_mat = np.asarray(boot_indices, dtype=int)
        B = int(idx_mat.shape[0])
    else:
        B = int(n_boot)
        b_len = min(max(1, int(block_length)), T)
        n_blocks = int(math.ceil(float(T) / float(b_len)))
        rng = np.random.default_rng(int(seed))
        starts = rng.integers(0, T, size=(B, n_blocks))
        offsets = np.arange(b_len)
        idx_mat = ((starts[:, :, None] + offsets[None, None, :]).reshape(B, -1)[:, :T]) % T

    surviving = list(range(M))
    eliminated_order: list[str] = []
    mcs_pvalues: dict[str, float] = {}
    running_max_p = 0.0
    initial_t_max: float | None = None

    while len(surviving) > 1:
        L_sub = L[:, surviving]  # (T, m_cur)
        d_ibar_t = L_sub - np.mean(L_sub, axis=1, keepdims=True)  # relative loss of i vs average in M
        d_ibar = np.mean(d_ibar_t, axis=0)  # (m_cur,)

        # Bootstrap centered relative losses d^*_{i., b} = mean_t(d_{i., t^*_b}) - d_ibar
        boot_means = np.empty((B, len(surviving)), dtype=float)
        for b in range(B):
            boot_means[b] = np.mean(d_ibar_t[idx_mat[b]], axis=0) - d_ibar

        var_boot = np.mean(boot_means * boot_means, axis=0)
        se_boot = np.sqrt(np.maximum(var_boot, 1e-18))
        t_stats = d_ibar / se_boot
        t_max_obs = float(np.max(t_stats))
        worst_local = int(np.argmax(t_stats))
        if initial_t_max is None:
            initial_t_max = t_max_obs

        t_boot_max = np.max(boot_means / se_boot[None, :], axis=1)
        p_step = float(np.mean(t_boot_max >= t_max_obs - 1e-12))
        running_max_p = max(running_max_p, p_step)

        if running_max_p < float(alpha):
            elim_global = surviving.pop(worst_local)
            eliminated_order.append(names[elim_global])
            mcs_pvalues[names[elim_global]] = round(running_max_p, 8)
        else:
            # Remaining models enter the MCS; assign monotonic step p-values until singleton
            elim_global = surviving.pop(worst_local)
            mcs_pvalues[names[elim_global]] = round(running_max_p, 8)

    last_model = names[surviving[0]]
    mcs_pvalues[last_model] = 1.0
    superior_set = [nm for nm in names if mcs_pvalues[nm] >= float(alpha)]
    if not superior_set:
        superior_set = [last_model]

    return {
        "alpha": float(alpha),
        "eliminated_models": [nm for nm in names if nm not in superior_set],
        "elimination_sequence": eliminated_order,
        "mcs_pvalues": mcs_pvalues,
        "mean_losses": {names[j]: round(float(np.mean(L[:, j])), 8) for j in range(M)},
        "superior_set": superior_set,
        "t_max_initial": round(float(initial_t_max or 0.0), 8),
    }


def run_t10_diebold_mariano(
    errors_1: Any,
    errors_2: Any,
    *,
    h: int = 1,
    power: int = 2,
    extra_oof_losses: Mapping[str, Any] | None = None,
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    mode: str = "full",
    enabled: bool = True,
    target: str = "",
    variable: str = "model_1_vs_model_2",
    allow_hac: bool = True,
    use_hln_exact: bool = False,
    decision_rule: str = DecisionRule.P_ONLY.value,
    min_effect_size: float = 0.0,
    seed: int = 42,
) -> TestContractResult:
    """Execute HLN-adjusted Diebold-Mariano OOF forecast comparison (+ optional MCS across >=2 models)."""
    lv = validate_ci_level(confidence_level)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    if not enabled:
        return make_not_run_user_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            stage="post_model",
            family="model_comparison",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    e1_raw = np.asarray(errors_1, dtype=float).reshape(-1)
    e2_raw = np.asarray(errors_2, dtype=float).reshape(-1)
    n_min = min(e1_raw.size, e2_raw.size)
    e1_raw, e2_raw = e1_raw[:n_min], e2_raw[:n_min]
    valid = np.isfinite(e1_raw) & np.isfinite(e2_raw)
    e1c, e2c = e1_raw[valid], e2_raw[valid]

    horizon = max(1, int(h))
    if e1c.size < max(10, horizon + 4):
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(e1c.size),
            min_required=max(10, horizon + 4),
            reason="insufficient OOF predictions for Diebold-Mariano forecast comparison",
            stage="post_model",
            family="model_comparison",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    idx_sub, sub_info, sub_chk = deterministic_subsample(
        np.arange(e1c.size),
        mode=norm_mode,
        max_fast_n=5000,
        preserve_order=True,
        seed=seed,
    )
    e1 = e1c[idx_sub]
    e2 = e2c[idx_sub]
    T = int(e1.size)

    dm = compute_dm_hln_exact(e1, e2, h=horizon, power=power)
    d = dm["loss_diff"]

    if float(np.std(d, ddof=1)) <= 1e-14:
        # Identical forecast losses -> p_raw = 1.0 (equal predictive accuracy retained)
        ci_zero = exact_t_ci(0.0, 0.0, df=max(1, T - 1), confidence_level=lv, method_name="degenerate_identical_oof")
        return TestContractResult(
            test_id=TEST_ID,
            name=TEST_NAME,
            subtest="dm_hln_identical_losses",
            stage="post_model",
            family="model_comparison",
            target=target,
            variable=variable,
            statistic=0.0,
            p_raw=1.0,
            p_adj=1.0,
            df=float(T - 1),
            effect_size=0.0,
            effect_size_name="loss_diff_cohen_d",
            ci=build_ci_dict(ci_zero),
            n_used=T,
            assumptions_checked=(sub_chk.to_dict(),),
            assumptions_met=True,
            decision="pass",
            rejected_null=False,
            interpretation="OOF forecast loss series are numerically identical (DM=0, p_raw=1.0).",
            downstream_action="retain_equivalent_forecast_models",
            pass_action="retain_equivalent_forecast_models",
            fail_action="prefer_lower_loss_forecast_model",
            mode=norm_mode,
        )

    kurt_d = float(sp_stats.kurtosis(d, fisher=True, bias=False))
    heavy_loss_outliers = abs(kurt_d) > 12.0
    tail_chk = AssumptionCheck(
        name="loss_differential_finite_variance",
        passed=not heavy_loss_outliers,
        metric_name="excess_kurtosis",
        metric_value=kurt_d,
        threshold=12.0,
        action="dm_hln_parametric" if not heavy_loss_outliers else "switched_to_wilcoxon_signed_rank_loss_diff",
    )

    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        d,
        allow_hac=allow_hac,
        prefer_block_bootstrap=False,
        alpha=alpha,
    )

    if (not use_hln_exact) and heavy_loss_outliers and not ac_info["autocorrelated"]:
        w_res = sp_stats.wilcoxon(d)
        stat_val = float(w_res.statistic)
        p_raw = float(np.clip(w_res.pvalue, 0.0, 1.0))
        subtest = "wilcoxon_loss_diff_fallback"
        fallback_used = "wilcoxon_signed_rank"
    elif (not use_hln_exact) and ac_info["autocorrelated"] and allow_hac and horizon == 1:
        # Upgrade h=1 variance to Newey-West HAC when OOF loss differentials remain serially correlated!
        ci_hac = mean_ci_with_autocorrelation_guard(d, confidence_level=lv, allow_hac=True, seed=seed)
        stat_val = float(dm["mean_loss_diff"]) / max(ci_hac.std_error, 1e-12)
        p_raw = float(np.clip(2.0 * sp_stats.t.sf(abs(stat_val), df=T - 1), 0.0, 1.0))
        subtest = "dm_hac_newey_west"
        fallback_used = "hac_newey_west"
    else:
        stat_val = float(dm["hln_statistic"])
        p_raw = float(dm["p_raw"])
        subtest = "dm_hln"
        fallback_used = ac_fb

    eff = float(dm["cohen_d"])
    if inf_label == "naive":
        se_naive = float(np.std(d, ddof=1)) / math.sqrt(float(T)) / max(float(np.std(d, ddof=1)), 1e-12)
        ci_obj = exact_t_ci(
            eff,
            se_naive,
            df=float(T - 1),
            confidence_level=lv,
            method_name="naive_iid_dm_cohen_d_t (autocorrelation_uncorrected)",
            inference_label="naive",
        )
    elif ac_info["autocorrelated"] and allow_hac and norm_mode == "full" and T <= 600:
        ci_obj = moving_block_bootstrap_ci(
            d,
            lambda a: float(np.mean(a)) / max(float(np.std(a, ddof=1)), 1e-12),
            confidence_level=lv,
            n_boot=200,
            seed=seed,
        )
    else:
        se_eff = float(dm["se_mean_loss_diff"]) / max(float(np.std(d, ddof=1)), 1e-12)
        ci_obj = exact_t_ci(
            eff,
            se_eff,
            df=float(T - 1),
            confidence_level=lv,
            method_name="hln_bartlett_cohen_d_t",
            inference_label=inf_label,
        )

    mcs_summary: dict[str, Any] | None = None
    if extra_oof_losses:
        names = list(extra_oof_losses.keys())
        cols = [np.asarray(extra_oof_losses[nm], dtype=float).reshape(-1)[:T] for nm in names]
        if len(cols) >= 2 and all(c.size == T for c in cols):
            mcs_summary = compute_mcs_exact(
                np.column_stack(cols),
                names,
                alpha=alpha,
                n_boot=250 if norm_mode == "full" else 100,
                seed=seed,
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
    pass_act = "retain_equivalent_forecast_models"
    fail_act = "prefer_lower_loss_forecast_model"
    action = fail_act if dec == "fail" else pass_act
    better = "model_1" if dm["mean_loss_diff"] < 0 else "model_2"
    interp = (
        f"Diebold-Mariano ({subtest}={stat_val:+.4f}, p_raw={p_raw:.4g}, mean_loss_diff={dm['mean_loss_diff']:+.4g}, "
        f"Cohen_d={eff:+.3f}): "
        f"{f'equal predictive accuracy rejected ({better} has lower OOF loss)' if rejected else 'models are statistically indistinguishable on OOF loss'}."
    )

    details: dict[str, Any] = {
        "autocorrelation_check": ac_info,
        "dm_raw_statistic": round(float(dm["dm_raw"]), 8),
        "h": horizon,
        "hln_statistic": round(float(dm["hln_statistic"]), 8),
        "hln_p": round(float(dm["p_raw"]), 10),
        "mean_loss_diff": round(float(dm["mean_loss_diff"]), 8),
        "policy_evaluation_note": rule_note,
        "power": power,
        "subsampling": sub_info,
    }
    if mcs_summary is not None:
        details["mcs"] = mcs_summary

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest=subtest,
        stage="post_model",
        family="model_comparison",
        target=target,
        variable=variable,
        statistic=stat_val,
        p_raw=p_raw,
        p_adj=p_raw,
        df=float(T - 1),
        effect_size=eff,
        effect_size_name="loss_diff_cohen_d",
        ci=ci_dict,
        n_used=T,
        assumptions_checked=(sub_chk.to_dict(), tail_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool((not heavy_loss_outliers) and ac_chk.passed),
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
        details=details,
    )
