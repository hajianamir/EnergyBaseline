"""T11 — Friedman omnibus & Nemenyi post-hoc multi-model OOF rank comparison (validated against R reference)."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from ..core.tcp import DecisionRule
from .ci_method import exact_t_ci, moving_block_bootstrap_ci, validate_ci_level
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

TEST_ID = "T11"
TEST_NAME = "Friedman + Nemenyi Post-Hoc OOF Rank Comparison"


def compute_friedman_nemenyi_exact(
    loss_matrix: Any,
    model_names: Sequence[str] | None = None,
    *,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Exact tie-corrected Friedman chi-square + pairwise Nemenyi studentized-range test.

    Matches ``stats::friedman.test`` and ``PMCMRplus::frdAllPairsNemenyiTest`` (`ptukey(..., df=Inf)`).
    """
    L = np.asarray(loss_matrix, dtype=float)
    if L.ndim != 2 or L.shape[0] < 4 or L.shape[1] < 3:
        raise ValueError("Nemenyi multi-model comparison requires shape (N >= 4, K >= 3).")
    N, K = L.shape
    names = list(model_names) if model_names is not None else [f"model_{j + 1}" for j in range(K)]

    ranks = np.empty_like(L, dtype=float)
    tie_sum = 0.0
    for i in range(N):
        r_row = sp_stats.rankdata(L[i], method="average")
        ranks[i] = r_row
        _, counts = np.unique(L[i], return_counts=True)
        for c in counts:
            if c > 1:
                tie_sum += float(c**3 - c)

    mean_ranks = np.mean(ranks, axis=0)
    ss_ranks = float(np.sum((mean_ranks - 0.5 * float(K + 1)) ** 2))
    chi2_raw = (12.0 * float(N) / float(K * (K + 1))) * ss_ranks
    tie_corr = 1.0 - tie_sum / float(N * K * (K * K - 1))
    friedman_chi2 = chi2_raw / max(tie_corr, 1e-12) if tie_corr > 0 else 0.0
    friedman_p = float(np.clip(sp_stats.chi2.sf(friedman_chi2, df=K - 1), 0.0, 1.0))

    # Iman-Davenport (1980) F-approximation
    denom_id = max(1e-12, float(N * (K - 1)) - friedman_chi2)
    iman_davenport_f = (float(N - 1) * friedman_chi2) / denom_id

    # Kendall's coefficient of concordance W in [0, 1]
    kendall_w = float(np.clip(friedman_chi2 / float(N * (K - 1)), 0.0, 1.0))

    # Nemenyi studentized range statistic: q_ij = |R_i - R_j| / sqrt(K*(K+1) / (12*N))
    se_nemenyi = math.sqrt(float(K * (K + 1)) / float(12 * N))
    se_diff = math.sqrt(float(K * (K + 1)) / float(6 * N))
    q_crit = float(sp_stats.studentized_range.ppf(1.0 - float(alpha), K, np.inf))
    critical_difference = (q_crit / math.sqrt(2.0)) * se_diff

    pairwise: list[dict[str, Any]] = []
    max_q = 0.0
    min_pair_p = 1.0
    best_idx = int(np.argmin(mean_ranks))
    worst_idx = int(np.argmax(mean_ranks))

    for i in range(K):
        for j in range(i + 1, K):
            diff = abs(float(mean_ranks[i] - mean_ranks[j]))
            q_ij = diff / se_nemenyi
            p_ij = float(np.clip(sp_stats.studentized_range.sf(q_ij, K, np.inf), 0.0, 1.0))
            if q_ij >= max_q:
                max_q = q_ij
                min_pair_p = p_ij
            pairwise.append(
                {
                    "model_a": names[i],
                    "model_b": names[j],
                    "p_nemenyi": round(p_ij, 10),
                    "q_statistic": round(q_ij, 8),
                    "rank_diff": round(diff, 8),
                    "significant": bool(p_ij <= alpha),
                }
            )

    return {
        "best_model": names[best_idx],
        "critical_difference": critical_difference,
        "df": K - 1,
        "friedman_chi2": friedman_chi2,
        "friedman_p": friedman_p,
        "iman_davenport_f": iman_davenport_f,
        "kendall_w": kendall_w,
        "max_q_statistic": max_q,
        "mean_ranks": {names[j]: round(float(mean_ranks[j]), 8) for j in range(K)},
        "min_nemenyi_p": min_pair_p,
        "n_blocks": N,
        "n_models": K,
        "pairwise": pairwise,
        "se_rank_diff": se_diff,
        "worst_model": names[worst_idx],
    }


def run_t11_nemenyi(
    oof_losses: Any,
    *,
    model_names: Sequence[str] | None = None,
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    mode: str = "full",
    enabled: bool = True,
    target: str = "",
    variable: str = "multi_model_oof",
    allow_hac: bool = True,
    use_nemenyi_exact: bool = False,
    decision_rule: str = DecisionRule.P_ONLY.value,
    min_effect_size: float = 0.0,
    seed: int = 42,
) -> TestContractResult:
    """Execute Friedman + Nemenyi post-hoc OOF multi-model comparison (with block-bootstrap guard under AR)."""
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

    if isinstance(oof_losses, pd.DataFrame):
        names = list(model_names) if model_names is not None else [str(c) for c in oof_losses.columns]
        mat = oof_losses.to_numpy(dtype=float)
    elif isinstance(oof_losses, dict):
        names = list(model_names) if model_names is not None else list(oof_losses.keys())
        cols = [np.asarray(oof_losses[k], dtype=float).reshape(-1) for k in names]
        min_len = min((c.size for c in cols), default=0)
        mat = np.column_stack([c[:min_len] for c in cols]) if cols and min_len > 0 else np.empty((0, 0))
    else:
        mat = np.asarray(oof_losses, dtype=float)
        if mat.ndim == 1:
            mat = mat.reshape(-1, 1)
        names = (
            list(model_names)
            if model_names is not None
            else [f"model_{j + 1}" for j in range(mat.shape[1])]
        )

    if mat.ndim != 2 or mat.shape[1] < 3:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(mat.shape[0]) if mat.ndim == 2 else 0,
            min_required=3,
            reason="Nemenyi post-hoc comparison requires >=3 competing OOF model loss columns",
            stage="post_model",
            family="model_comparison",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    valid_rows = np.all(np.isfinite(mat), axis=1)
    clean = mat[valid_rows]
    if clean.shape[0] < 8:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(clean.shape[0]),
            min_required=8,
            reason="requires >=8 complete OOF timestamps across models",
            stage="post_model",
            family="model_comparison",
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
    N, K = sub.shape
    exact = compute_friedman_nemenyi_exact(sub, names, alpha=alpha)

    # Check block independence on best-vs-worst rank differences
    ranks_all = np.vstack([sp_stats.rankdata(sub[i], method="average") for i in range(N)])
    best_i = names.index(exact["best_model"])
    worst_i = names.index(exact["worst_model"])
    rank_gap_series = ranks_all[:, worst_i] - ranks_all[:, best_i]

    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        rank_gap_series,
        allow_hac=allow_hac,
        prefer_block_bootstrap=allow_hac,
        alpha=alpha,
    )
    blk_chk = AssumptionCheck(
        name="complete_balanced_oof_blocks",
        passed=True,
        metric_name="n_models",
        metric_value=K,
        threshold=3,
        action="friedman_nemenyi_rank_comparison",
    )

    max_q = float(exact["max_q_statistic"])
    if (not use_nemenyi_exact) and ac_info["autocorrelated"] and allow_hac:
        # Calibrate max-q against effective sample size under serial correlation of rank gaps
        r1 = max(0.0, min(0.90, abs(float(ac_info["lag1_r"]))))
        inflation = math.sqrt((1.0 + r1) / max(0.10, 1.0 - r1))
        q_eff = max_q / inflation
        p_raw = float(np.clip(sp_stats.studentized_range.sf(q_eff, K, np.inf), 0.0, 1.0))
        stat_val = q_eff
        subtest = "nemenyi_hac_effective_n_fallback"
        fallback_used = "bartlett_effective_n_nemenyi"
    else:
        stat_val = max_q
        p_raw = float(exact["min_nemenyi_p"])
        subtest = "friedman_nemenyi_exact"
        fallback_used = ac_fb

    eff = float(exact["kendall_w"])
    if inf_label == "block_bootstrap" and N <= 600:
        ci_obj = moving_block_bootstrap_ci(
            sub,
            lambda m: float(compute_friedman_nemenyi_exact(m, names, alpha=alpha)["kendall_w"]),
            confidence_level=lv,
            n_boot=150,
            seed=seed,
        )
    else:
        se_w = max(1e-6, math.sqrt(2.0 / float(max(4, N * (K - 1)))))
        ci_obj = exact_t_ci(
            eff,
            se_w,
            df=float(N - 1),
            confidence_level=lv,
            method_name=(
                "naive_iid_kendall_w_t (autocorrelation_uncorrected)"
                if inf_label == "naive"
                else "asymptotic_kendall_w_t"
            ),
            inference_label=inf_label if inf_label != "standard" else "nonparametric_exact",
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
    pass_act = "retain_statistically_tied_model_ranks"
    fail_act = "prune_nemenyi_rank_dominated_models"
    action = fail_act if dec == "fail" else pass_act
    interp = (
        f"Friedman chi2={exact['friedman_chi2']:.3f} (p={exact['friedman_p']:.4g}), "
        f"Nemenyi max_q={stat_val:.3f} (p_raw={p_raw:.4g}, CD={exact['critical_difference']:.3f}, "
        f"Kendall_W={eff:.3f}, best='{exact['best_model']}', worst='{exact['worst_model']}'): "
        f"{'significant rank divergence across OOF models' if rejected else 'OOF model ranks are not significantly different'}."
    )

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
        df=float(K - 1),
        effect_size=eff,
        effect_size_name="kendall_concordance_w",
        ci=ci_dict,
        n_used=N,
        assumptions_checked=(sub_chk.to_dict(), blk_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool(ac_chk.passed),
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
            "autocorrelation_check": ac_info,
            "best_model": exact["best_model"],
            "critical_difference": round(float(exact["critical_difference"]), 8),
            "friedman_chi2": round(float(exact["friedman_chi2"]), 8),
            "friedman_p": round(float(exact["friedman_p"]), 10),
            "iman_davenport_f": round(float(exact["iman_davenport_f"]), 8),
            "max_q_unadjusted_ac": round(max_q, 8),
            "mean_ranks": exact["mean_ranks"],
            "min_nemenyi_p": round(float(exact["min_nemenyi_p"]), 10),
            "pairwise": exact["pairwise"],
            "policy_evaluation_note": rule_note,
            "subsampling": sub_info,
            "worst_model": exact["worst_model"],
        },
    )
