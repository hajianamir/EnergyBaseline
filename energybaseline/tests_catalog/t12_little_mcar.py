"""T12 — Native Little's (1988) multivariate MCAR missingness test via Gaussian EM + Spearman pattern fallback."""

from __future__ import annotations

import math
from typing import Any

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

TEST_ID = "T12"
TEST_NAME = "Little's Multivariate MCAR Missingness Test"


def compute_little_mcar_em(
    data: np.ndarray,
    *,
    max_iter: int = 500,
    tol: float = 1e-9,
    ridge: float = 1e-8,
) -> dict[str, Any]:
    """Native Expectation-Maximization (EM) implementation of Little's (1988, JASA) MCAR chi-square test.

    Matches ``naniar::mcar_test`` / ``norm::em.norm`` (ML covariance denominator ``N_valid_rows``).
    """
    Y_in = np.asarray(data, dtype=float)
    if Y_in.ndim != 2 or Y_in.shape[1] < 2:
        raise ValueError("Little's MCAR test requires a 2-D numeric matrix with P >= 2 variables.")

    # Drop completely empty rows (rows with zero observed variables carry no information)
    obs_mask_full = np.isfinite(Y_in)
    keep_rows = np.any(obs_mask_full, axis=1)
    Y = Y_in[keep_rows]
    obs_mask = obs_mask_full[keep_rows]
    N, P = Y.shape
    if N < P + 4:
        raise ValueError(f"Little's MCAR requires N >= P + 4 non-empty rows, got N={N}, P={P}.")

    # Group rows by missingness pattern bitmask
    patterns: dict[tuple[bool, ...], list[int]] = {}
    for i in range(N):
        key = tuple(bool(b) for b in obs_mask[i])
        patterns.setdefault(key, []).append(i)

    if len(patterns) <= 1:
        return {
            "converged": True,
            "d2_statistic": 0.0,
            "df": 0,
            "em_iterations": 0,
            "n_patterns": len(patterns),
            "n_rows": N,
            "p_raw": 1.0,
            "phi_effect": 0.0,
        }

    # Initialize mu_hat from observed column means and Sigma_hat from column-mean-imputed ML covariance
    mu = np.zeros(P, dtype=float)
    Y_imp = Y.copy()
    for j in range(P):
        col_obs = Y[obs_mask[:, j], j]
        m_j = float(np.mean(col_obs)) if col_obs.size > 0 else 0.0
        mu[j] = m_j
        Y_imp[~obs_mask[:, j], j] = m_j

    centered_init = Y_imp - mu[None, :]
    Sigma = (centered_init.T @ centered_init) / float(N) + ridge * np.eye(P)

    converged = False
    iters = 0
    pattern_items = [(np.array(k, dtype=bool), np.array(idxs, dtype=int)) for k, idxs in patterns.items()]

    for it in range(1, max_iter + 1):
        iters = it
        sum_y = np.zeros(P, dtype=float)
        sum_yy = np.zeros((P, P), dtype=float)

        for mask_bool, row_idxs in pattern_items:
            m_g = int(row_idxs.size)
            obs_idx = np.flatnonzero(mask_bool)
            mis_idx = np.flatnonzero(~mask_bool)
            Y_sub = Y[row_idxs]

            if mis_idx.size == 0:
                Y_obs = Y_sub[:, obs_idx]
                sum_y += np.sum(Y_obs, axis=0)
                sum_yy += Y_obs.T @ Y_obs
            else:
                Y_obs = Y_sub[:, obs_idx]  # (m_g, p_obs)
                mu_o = mu[obs_idx]
                mu_m = mu[mis_idx]
                S_oo = Sigma[np.ix_(obs_idx, obs_idx)] + ridge * np.eye(obs_idx.size)
                S_mo = Sigma[np.ix_(mis_idx, obs_idx)]
                S_mm = Sigma[np.ix_(mis_idx, mis_idx)]

                S_oo_inv = np.linalg.pinv(S_oo, rcond=1e-12)
                B = S_mo @ S_oo_inv  # (p_mis, p_obs)
                cond_cov_mm = S_mm - B @ S_mo.T  # Var(Y_mis | Y_obs)
                cond_cov_mm = 0.5 * (cond_cov_mm + cond_cov_mm.T)

                # Conditional expectations for every row in pattern g
                Y_hat = np.empty((m_g, P), dtype=float)
                Y_hat[:, obs_idx] = Y_obs
                Y_hat[:, mis_idx] = mu_m[None, :] + (Y_obs - mu_o[None, :]) @ B.T

                sum_y += np.sum(Y_hat, axis=0)
                sum_yy += Y_hat.T @ Y_hat
                # Add conditional covariance contribution m_g * Var(Y_mis | Y_obs) to missing block
                sum_yy[np.ix_(mis_idx, mis_idx)] += float(m_g) * cond_cov_mm

        mu_new = sum_y / float(N)
        Sigma_new = (sum_yy / float(N)) - np.outer(mu_new, mu_new)
        Sigma_new = 0.5 * (Sigma_new + Sigma_new.T) + ridge * np.eye(P)

        diff_mu = float(np.max(np.abs(mu_new - mu)))
        diff_S = float(np.max(np.abs(Sigma_new - Sigma)))
        mu = mu_new
        Sigma = Sigma_new
        if max(diff_mu, diff_S) < tol:
            converged = True
            break

    # Compute Little's d^2 across all patterns with >=1 observed variable
    d2 = 0.0
    sum_pj = 0
    for mask_bool, row_idxs in pattern_items:
        obs_idx = np.flatnonzero(mask_bool)
        p_j = int(obs_idx.size)
        if p_j == 0:
            continue
        sum_pj += p_j
        m_j = int(row_idxs.size)
        y_bar_j = np.mean(Y[np.ix_(row_idxs, obs_idx)], axis=0)
        delta_j = y_bar_j - mu[obs_idx]
        S_oo = Sigma[np.ix_(obs_idx, obs_idx)]
        S_oo_inv = np.linalg.pinv(S_oo, rcond=1e-12)
        d2 += float(m_j) * float(delta_j.T @ S_oo_inv @ delta_j)

    df = max(1, sum_pj - P)
    d2 = max(0.0, d2)
    p_raw = float(np.clip(sp_stats.chi2.sf(d2, df=df), 0.0, 1.0))
    phi_eff = math.sqrt(d2 / float(max(1, N * df)))

    return {
        "converged": converged,
        "d2_statistic": d2,
        "df": df,
        "em_iterations": iters,
        "mu_mle": mu.tolist(),
        "n_patterns": len(pattern_items),
        "n_rows": N,
        "p_raw": p_raw,
        "phi_effect": phi_eff,
    }


def _missingness_pattern_spearman_fallback(Y: np.ndarray) -> tuple[float, float, float]:
    """Non-parametric MAR vs MCAR check correlating missingness indicators R_j with observed columns Y_k."""
    N, P = Y.shape
    obs = np.isfinite(Y)
    p_vals: list[float] = []
    rhos: list[float] = []
    for j in range(P):
        miss_j = (~obs[:, j]).astype(float)
        if np.sum(miss_j) < 2 or np.sum(1.0 - miss_j) < 2:
            continue
        for k in range(P):
            if k == j:
                continue
            valid_k = obs[:, k]
            if np.sum(valid_k) < 8 or np.unique(miss_j[valid_k]).size < 2:
                continue
            res = sp_stats.spearmanr(miss_j[valid_k], Y[valid_k, k])
            if np.isfinite(res.pvalue):
                p_vals.append(float(np.clip(res.pvalue, 1e-15, 1.0)))
                rhos.append(abs(float(res.statistic)))

    if not p_vals:
        return 0.0, 1.0, 0.0
    # Fisher combined chi-square statistic -2 * sum(ln p_m) ~ chi^2_{2M}
    stat = -2.0 * float(np.sum(np.log(p_vals)))
    df = 2 * len(p_vals)
    # Also take Bonferroni-conservative check on strongest MAR association so mild sample correlation isn't over-inflated
    min_p_bonf = min(1.0, float(len(p_vals)) * min(p_vals))
    p_comb = max(float(sp_stats.chi2.sf(stat, df=df)), min_p_bonf * 0.5) if len(p_vals) > 4 else min_p_bonf
    return stat, float(np.clip(p_comb, 0.0, 1.0)), float(max(rhos))


def run_t12_little_mcar(
    data: Any,
    *,
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    mode: str = "full",
    enabled: bool = True,
    target: str = "",
    variable: str = "",
    allow_hac: bool = True,
    use_em_exact: bool = False,
    decision_rule: str = DecisionRule.P_ONLY.value,
    min_effect_size: float = 0.0,
    seed: int = 42,
) -> TestContractResult:
    """Execute native Little's (1988) MCAR test with non-parametric missingness-pattern fallback."""
    lv = validate_ci_level(confidence_level)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    if not enabled:
        return make_not_run_user_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            stage="post_model",
            family="missingness_balance",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    if isinstance(data, pd.DataFrame):
        num_df = data.select_dtypes(include=[np.number])
        mat = num_df.to_numpy(dtype=float)
    else:
        mat = np.asarray(data, dtype=float)
        if mat.ndim == 1:
            mat = mat.reshape(-1, 1)

    if mat.ndim != 2 or mat.shape[1] < 2 or mat.shape[0] < 10:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(mat.shape[0]) if mat.ndim == 2 else 0,
            min_required=10,
            reason="Little's MCAR requires >=10 rows and >=2 numeric variables",
            stage="post_model",
            family="missingness_balance",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    sub, sub_info, sub_chk = deterministic_subsample(
        mat,
        mode=norm_mode,
        max_fast_n=4000,
        preserve_order=True,
        seed=seed,
    )
    N, P = sub.shape
    n_missing_cells = int(np.sum(~np.isfinite(sub)))

    if n_missing_cells == 0:
        ci_zero = exact_t_ci(0.0, 0.0, df=float(N - 1), confidence_level=lv, method_name="complete_data_exact")
        return TestContractResult(
            test_id=TEST_ID,
            name=TEST_NAME,
            subtest="complete_data_no_missingness",
            stage="post_model",
            family="missingness_balance",
            target=target,
            variable=variable,
            statistic=0.0,
            p_raw=1.0,
            p_adj=1.0,
            df=0.0,
            effect_size=0.0,
            effect_size_name="little_mcar_cramers_phi",
            ci=build_ci_dict(ci_zero),
            n_used=N,
            assumptions_checked=(sub_chk.to_dict(),),
            assumptions_met=True,
            decision="pass",
            rejected_null=False,
            interpretation="Dataset has 0 missing numeric cells; MCAR holds trivially (d^2=0, p_raw=1.0).",
            downstream_action="proceed_complete_case",
            pass_action="proceed_complete_case",
            fail_action="restrict_to_mar_safe_imputation_and_flag",
            mode=norm_mode,
            details={"missing_cells": 0, "subsampling": sub_info},
        )

    # Check multivariate marginal kurtosis on observed values
    kurts = [
        abs(float(sp_stats.kurtosis(sub[np.isfinite(sub[:, j]), j], fisher=True, bias=False)))
        for j in range(P)
        if np.sum(np.isfinite(sub[:, j])) >= 8
    ]
    max_kurt = max(kurts) if kurts else 0.0
    mvn_ok = max_kurt <= 6.0
    mvn_chk = AssumptionCheck(
        name="multivariate_gaussian_margins_for_em_mcar",
        passed=mvn_ok,
        metric_name="max_abs_kurtosis",
        metric_value=max_kurt,
        threshold=6.0,
        action="little_em_mcar_primary" if mvn_ok else "augmented_with_spearman_missingness_pattern_fallback",
    )

    miss_row_indicator = np.any(~np.isfinite(sub), axis=1).astype(float)
    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        miss_row_indicator,
        allow_hac=allow_hac,
        prefer_block_bootstrap=(norm_mode == "full" and allow_hac and N <= 500),
        alpha=alpha,
    )

    em_res = compute_little_mcar_em(sub)
    sp_stat, sp_p, sp_rho = _missingness_pattern_spearman_fallback(sub)

    if (not use_em_exact) and (not mvn_ok):
        stat_val = sp_stat
        p_raw = sp_p
        df_val = float(max(1, P - 1))
        eff = sp_rho
        subtest = "missingness_pattern_spearman_fallback"
        fallback_used = "missingness_pattern_spearman"
    else:
        stat_val = float(em_res["d2_statistic"])
        p_raw = float(em_res["p_raw"])
        df_val = float(em_res["df"])
        eff = float(em_res["phi_effect"])
        subtest = "little_mcar_em"
        fallback_used = ac_fb

    if inf_label == "block_bootstrap" and N <= 400:
        ci_obj = moving_block_bootstrap_ci(
            sub,
            lambda m: float(compute_little_mcar_em(m)["phi_effect"]),
            confidence_level=lv,
            n_boot=80,
            seed=seed,
        )
    else:
        se_eff = max(1e-6, 1.0 / math.sqrt(float(max(4, N))))
        ci_obj = exact_t_ci(
            eff,
            se_eff,
            df=max(1.0, df_val),
            confidence_level=lv,
            method_name=(
                "naive_mcar_phi_t (autocorrelation_uncorrected)"
                if inf_label == "naive"
                else "asymptotic_mcar_phi_t"
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
    pass_act = "proceed_mcar_compatible_handling"
    fail_act = "restrict_to_mar_safe_imputation_and_flag"
    action = fail_act if dec == "fail" else pass_act
    interp = (
        f"Little's MCAR ({subtest}={stat_val:.4f}, df={df_val:.0f}, p_raw={p_raw:.4g}, "
        f"patterns={em_res['n_patterns']}, phi={eff:.3f}): "
        f"{'MCAR rejected -> missingness depends on observed/unobserved state (MAR/MNAR)' if rejected else 'consistent with Missing Completely At Random (MCAR)'}."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest=subtest,
        stage="post_model",
        family="missingness_balance",
        target=target,
        variable=variable,
        statistic=stat_val,
        p_raw=p_raw,
        p_adj=p_raw,
        df=df_val,
        effect_size=eff,
        effect_size_name="little_mcar_cramers_phi",
        ci=ci_dict,
        n_used=int(em_res["n_rows"]),
        assumptions_checked=(sub_chk.to_dict(), mvn_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool(mvn_ok and ac_chk.passed),
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
            "em_converged": bool(em_res["converged"]),
            "em_d2": round(float(em_res["d2_statistic"]), 8),
            "em_df": int(em_res["df"]),
            "em_iterations": int(em_res["em_iterations"]),
            "em_p": round(float(em_res["p_raw"]), 10),
            "missing_cells": n_missing_cells,
            "n_patterns": int(em_res["n_patterns"]),
            "policy_evaluation_note": rule_note,
            "spearman_fallback_p": round(sp_p, 8),
            "spearman_max_rho": round(sp_rho, 6),
            "subsampling": sub_info,
        },
    )
