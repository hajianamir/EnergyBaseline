"""T5 — Heteroskedasticity suite: Koenker/Breusch-Pagan LM, White omnibus, Engle ARCH-LM & Spearman rank fallback."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats as sp_stats
from statsmodels.stats.diagnostic import het_arch, het_breuschpagan, het_white

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

TEST_ID = "T5"
TEST_NAME = "Breusch-Pagan, White & ARCH-LM Heteroskedasticity"


def _prepare_exog(residuals: np.ndarray, exog: Any | None) -> np.ndarray:
    n = int(residuals.size)
    if exog is None:
        t = np.linspace(-1.0, 1.0, num=n, dtype=float)
        return np.column_stack([np.ones(n, dtype=float), t])
    X = np.asarray(exog, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    if X.shape[0] != n:
        raise ValueError(f"exog row count ({X.shape[0]}) must match residuals length ({n}).")
    # Prepend constant column if none present
    col_vars = np.std(X, axis=0)
    if not np.any(col_vars <= 1e-12):
        X = np.column_stack([np.ones(n, dtype=float), X])
    return X


def _aux_r2(u2: np.ndarray, X: np.ndarray) -> float:
    yc = u2 - float(np.mean(u2))
    sst = float(np.dot(yc, yc))
    if sst <= 1e-15:
        return 0.0
    beta, *_ = np.linalg.lstsq(X, u2, rcond=None)
    err = u2 - X @ beta
    sse = float(np.dot(err, err))
    return float(np.clip(1.0 - sse / sst, 0.0, 1.0))


def run_t5_heteroskedasticity(
    residuals: Any,
    exog: Any | None = None,
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
    """Test homoskedasticity via Koenker-studentized Breusch-Pagan, White, Engle ARCH-LM & Spearman fallback."""
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

    raw_u = np.asarray(residuals, dtype=float).reshape(-1)
    if exog is not None:
        raw_X = np.asarray(exog, dtype=float)
        if raw_X.ndim == 1:
            raw_X = raw_X.reshape(-1, 1)
        valid = np.isfinite(raw_u) & np.all(np.isfinite(raw_X), axis=1)
        clean_u = raw_u[valid]
        clean_X: np.ndarray | None = raw_X[valid]
    else:
        valid = np.isfinite(raw_u)
        clean_u = raw_u[valid]
        clean_X = None

    if clean_u.size < 14 or float(np.std(clean_u, ddof=1)) <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(clean_u.size),
            min_required=14,
            reason="requires >=14 finite non-constant residuals for heteroskedasticity tests",
            stage="pre_model",
            family="residual_diagnostics",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    idx_arr, sub_info, sub_chk = deterministic_subsample(
        np.arange(clean_u.size),
        mode=norm_mode,
        max_fast_n=5000,
        preserve_order=True,
        seed=seed,
    )
    u = clean_u[idx_arr] - float(np.mean(clean_u[idx_arr]))
    X = _prepare_exog(u, clean_X[idx_arr] if clean_X is not None else None)
    n = int(u.size)

    kurt = float(sp_stats.kurtosis(u, fisher=True, bias=False))
    norm_ok = abs(kurt) <= 3.0
    kurt_chk = AssumptionCheck(
        name="gaussian_fourth_moment_for_classical_bp",
        passed=norm_ok,
        metric_name="excess_kurtosis",
        metric_value=kurt,
        threshold=3.0,
        action="classical_bp_and_white" if norm_ok else "koenker_studentized_bp_and_spearman_rank_fallback",
    )

    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        u * u,
        allow_hac=allow_hac,
        prefer_block_bootstrap=(norm_mode == "full" and allow_hac and n <= 600),
        alpha=alpha,
    )

    # statsmodels het_breuschpagan(..., robust=True) implements Koenker's studentized kurtosis-robust LM!
    bp_lm, bp_p_raw, bp_f, bp_fp = het_breuschpagan(u, X, robust=not norm_ok)
    bp_stat = float(bp_lm)
    bp_p = float(np.clip(bp_p_raw, 0.0, 1.0))

    p_cols = int(X.shape[1])
    white_terms = p_cols * (p_cols + 1) // 2
    if white_terms < max(4, n // 3) and p_cols <= 8:
        w_lm, w_p_raw, _, _ = het_white(u, X)
        white_stat = float(w_lm)
        white_p = float(np.clip(w_p_raw, 0.0, 1.0))
    else:
        white_stat, white_p = bp_stat, bp_p

    arch_lags = min(4, max(1, n // 10))
    arch_lm, arch_p_raw, _, _ = het_arch(u, nlags=arch_lags)
    arch_stat = float(arch_lm)
    arch_p = float(np.clip(arch_p_raw, 0.0, 1.0))

    # Non-parametric Spearman rank correlation between |u| and primary predictor / scale proxy
    scale_proxy = X[:, 1] if X.shape[1] >= 2 else np.arange(n, dtype=float)
    sp_res = sp_stats.spearmanr(np.abs(u), scale_proxy)
    sp_rho = float(sp_res.statistic) if np.isfinite(sp_res.statistic) else 0.0
    sp_p = float(np.clip(sp_res.pvalue, 0.0, 1.0)) if np.isfinite(sp_res.pvalue) else 1.0

    # Primary test: when strong ARCH clustering dominates static exog, report ARCH-LM; when extreme kurtosis > 8,
    # use Spearman |u| rank fallback; otherwise Koenker/Breusch-Pagan (& White when nonlinear variance present)
    if abs(kurt) > 8.0 and sp_p < bp_p:
        primary_stat = abs(sp_rho) * float(np.sqrt(max(1, n - 2)))
        primary_p = sp_p
        primary_df = float(max(1, n - 2))
        subtest = "spearman_abs_residual_fallback"
        fallback_used = "spearman_abs_residual_rank"
    elif white_p < bp_p and white_p <= alpha:
        primary_stat = white_stat
        primary_p = white_p
        primary_df = float(max(1, white_terms - 1))
        subtest = "white_quadratic_lm"
        fallback_used = "koenker_studentized" if not norm_ok else ac_fb
    elif exog is None and arch_p < bp_p:
        primary_stat = arch_stat
        primary_p = arch_p
        primary_df = float(arch_lags)
        subtest = "engle_arch_lm"
        fallback_used = ac_fb
    else:
        primary_stat = bp_stat
        primary_p = bp_p
        primary_df = float(max(1, p_cols - 1))
        subtest = "koenker_breusch_pagan_lm" if not norm_ok else "breusch_pagan_lm"
        fallback_used = "koenker_studentized_bp" if not norm_ok else ac_fb

    r2_aux = max(_aux_r2(u * u, X), arch_stat / float(n))
    r_eff = float(np.sqrt(np.clip(r2_aux, 0.0, 0.999999)))
    if inf_label == "block_bootstrap":
        stacked = np.column_stack([u * u, X])
        ci_obj = moving_block_bootstrap_ci(
            stacked,
            lambda m: float(np.sqrt(max(0.0, _aux_r2(m[:, 0], m[:, 1:])))),
            confidence_level=lv,
            n_boot=180,
            seed=seed,
        )
    else:
        ci_obj = fisher_z_ci(
            r_eff,
            n,
            confidence_level=lv,
            autocorrelated=bool(ac_info["autocorrelated"]),
            allow_hac=allow_hac,
            effective_n=(float(n) * 0.6 if (ac_info["autocorrelated"] and allow_hac) else None),
        )

    ci_dict = build_ci_dict(ci_obj)
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=primary_p,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=r_eff,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=0.0,
        reject_means_fail=True,
    )
    pass_act = "proceed_homoskedastic_ols"
    fail_act = "apply_hc3_hac_or_variance_stabilizing_transform"
    action = fail_act if dec == "fail" else pass_act
    interp = (
        f"Heteroskedasticity ({subtest}={primary_stat:.3f}, p_raw={primary_p:.4g}, BP={bp_stat:.3f}, "
        f"White={white_stat:.3f}, ARCH({arch_lags})={arch_stat:.3f}, sqrt(R2_aux)={r_eff:.3f}): "
        f"{'non-constant error variance detected' if rejected else 'homoskedasticity retained'}."
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
        effect_size=r_eff,
        effect_size_name="sqrt_auxiliary_variance_r2",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), kurt_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool(norm_ok and ac_chk.passed),
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
            "arch_lags": arch_lags,
            "arch_lm": round(arch_stat, 6),
            "arch_p": round(arch_p, 8),
            "autocorrelation_check": ac_info,
            "breusch_pagan_f": round(float(bp_f), 6),
            "breusch_pagan_f_p": round(float(bp_fp), 8),
            "breusch_pagan_lm": round(bp_stat, 6),
            "breusch_pagan_p": round(bp_p, 8),
            "policy_evaluation_note": rule_note,
            "spearman_abs_u_p": round(sp_p, 8),
            "spearman_abs_u_rho": round(sp_rho, 6),
            "subsampling": sub_info,
            "white_lm": round(white_stat, 6),
            "white_p": round(white_p, 8),
        },
    )
