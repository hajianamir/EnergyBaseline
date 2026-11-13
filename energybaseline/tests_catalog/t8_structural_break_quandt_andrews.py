"""T8 — Quandt-Andrews unknown-date sup-F structural break test with bootstrap p-value (R reference)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..core.tcp import DecisionRule
from .ci_method import bounded_effect_bootstrap_ci, moving_block_bootstrap_ci, validate_ci_level
from .t7_structural_break_chow import _design_with_intercept
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

TEST_ID = "T8"
TEST_NAME = "Quandt-Andrews Unknown Breakpoint sup-F Test"


def compute_quandt_andrews_profile(
    y: np.ndarray,
    X: np.ndarray,
    *,
    trim: float = 0.15,
) -> dict[str, Any]:
    """Compute exact sequence of Chow F(tb) and Wald k*F(tb) across trimmed interior break dates.

    Matches ``strucchange::Fstats(y ~ x, from = trim, to = 1 - trim)`` in R, where ``Fstats$stats``
    equals the Wald sequence ``k * F_Chow(tb)`` over 1-based split points ``tb``.
    """
    n, k = X.shape
    t_low = max(k + 1, int(math.ceil(trim * float(n))))
    t_high = min(n - k - 1, int(math.floor((1.0 - trim) * float(n))))
    if t_high < t_low:
        raise ValueError(f"Insufficient sample n={n} for trim={trim} and k={k}.")

    beta_p, *_ = np.linalg.lstsq(X, y, rcond=None)
    u_p = y - X @ beta_p
    rss_p = float(np.dot(u_p, u_p))

    candidates = list(range(t_low, t_high + 1))
    f_seq = np.empty(len(candidates), dtype=float)
    rss_u_seq = np.empty(len(candidates), dtype=float)
    df2 = float(n - 2 * k)

    for i, tb in enumerate(candidates):
        b1, *_ = np.linalg.lstsq(X[:tb], y[:tb], rcond=None)
        b2, *_ = np.linalg.lstsq(X[tb:], y[tb:], rcond=None)
        u1 = y[:tb] - X[:tb] @ b1
        u2 = y[tb:] - X[tb:] @ b2
        rss_u = float(np.dot(u1, u1) + np.dot(u2, u2))
        rss_u_seq[i] = rss_u
        num = max(0.0, (rss_p - rss_u) / float(k))
        den = max(rss_u / df2, 1e-15)
        f_seq[i] = num / den

    best_idx = int(np.argmax(f_seq))
    sup_f = float(f_seq[best_idx])
    sup_wald = float(k) * sup_f
    best_tb = int(candidates[best_idx])
    best_rss_u = float(rss_u_seq[best_idx])
    cohen_f = math.sqrt(max(0.0, (rss_p - best_rss_u) / max(best_rss_u, 1e-15)))
    ave_f = float(np.mean(f_seq))
    exp_f = float(np.log(np.mean(np.exp(0.5 * np.minimum(f_seq * k, 100.0)))))

    return {
        "ave_f": ave_f,
        "beta_null": beta_p,
        "break_point": best_tb,
        "candidates": candidates,
        "cohen_f": cohen_f,
        "exp_wald": exp_f,
        "f_sequence": f_seq,
        "k": k,
        "rss_pooled": rss_p,
        "sup_f": sup_f,
        "sup_wald": sup_wald,
        "u_null": u_p,
    }


def hansen_1997_p_approx(sup_wald: float, k: int, trim: float = 0.15) -> float:
    """Hansen (1997) / Andrews (1993) large-sample extreme-value p-value approximation for sup-Wald."""
    if sup_wald <= 0.0:
        return 1.0
    lam = ((1.0 - trim) * (1.0 - trim)) / (trim * trim)
    # Siegmund / Andrews boundary-crossing approximation for sup_{t in [pi0, 1-pi0]} chi^2_k Brownian bridge
    from scipy import stats as sp_stats

    half_w = 0.5 * sup_wald
    k_half = 0.5 * float(k)
    tail_chi2 = float(sp_stats.chi2.sf(sup_wald, df=k))
    density_term = (
        math.exp(min(300.0, (k_half) * math.log(max(half_w, 1e-12)) - half_w - math.lgamma(k_half + 1.0)))
        * 0.5
        * math.log(max(1.0001, lam))
        * max(0.0, sup_wald - float(k))
    )
    return float(np.clip(tail_chi2 + density_term, 0.0, 1.0))


def _bootstrap_sup_f_pvalue(
    y: np.ndarray,
    X: np.ndarray,
    observed_sup_f: float,
    beta_null: np.ndarray,
    u_null: np.ndarray,
    *,
    trim: float,
    n_boot: int,
    use_moving_block: bool,
    use_wild: bool,
    seed: int,
) -> float:
    """Compute bootstrap p-value under H0 (no break) via i.i.d., wild Rademacher, or moving-block bootstrap."""
    n = int(y.size)
    rng = np.random.default_rng(int(seed))
    y_hat_0 = X @ beta_null
    u_centered = u_null - float(np.mean(u_null))
    exceed = 0

    b_len = max(2, int(round(n ** (1.0 / 3.0))))
    n_blocks = int(math.ceil(float(n) / float(b_len)))
    u_ext = np.concatenate([u_centered, u_centered[: b_len - 1]]) if b_len > 1 else u_centered
    offsets = np.arange(b_len)

    for _ in range(int(n_boot)):
        if use_moving_block:
            starts = rng.integers(0, n, size=n_blocks)
            idx = (starts[:, None] + offsets[None, :]).reshape(-1)[:n]
            u_star = u_ext[idx]
        elif use_wild:
            signs = rng.choice(np.array([-1.0, 1.0]), size=n)
            u_star = u_centered * signs
        else:
            idx = rng.integers(0, n, size=n)
            u_star = u_centered[idx]
        y_star = y_hat_0 + u_star
        prof_b = compute_quandt_andrews_profile(y_star, X, trim=trim)
        if float(prof_b["sup_f"]) >= observed_sup_f - 1e-12:
            exceed += 1

    return float((exceed + 1) / (int(n_boot) + 1))


def run_t8_quandt_andrews(
    y: Any,
    exog: Any | None = None,
    *,
    trim: float = 0.15,
    n_boot: int | None = None,
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
    """Execute Quandt-Andrews unknown-breakpoint sup-F test with bootstrap p-value (TC-3, R reference)."""
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

    y_arr = np.asarray(y, dtype=float).reshape(-1)
    X_raw = _design_with_intercept(y_arr, exog) if y_arr.size > 0 else np.empty((0, 1))
    valid = np.isfinite(y_arr) & np.all(np.isfinite(X_raw), axis=1)
    yc = y_arr[valid]
    Xc = X_raw[valid]
    k = int(Xc.shape[1]) if Xc.ndim == 2 else 1

    min_req = max(20, int(math.ceil((2.0 * float(k + 2)) / max(0.05, trim))))
    if yc.size < min_req or float(np.std(yc, ddof=1)) <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(yc.size),
            min_required=min_req,
            reason=f"requires >={min_req} non-constant observations for trimmed Quandt-Andrews grid (trim={trim})",
            stage="pre_model",
            family="breaks_regimes",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    idx_sub, sub_info, sub_chk = deterministic_subsample(
        np.arange(yc.size),
        mode=norm_mode,
        max_fast_n=1500,
        preserve_order=True,
        seed=seed,
    )
    ys = yc[idx_sub]
    Xs = Xc[idx_sub]
    n = int(ys.size)

    prof = compute_quandt_andrews_profile(ys, Xs, trim=trim)
    u_null = prof["u_null"]

    # Check homoskedasticity + serial independence of null residuals to pick bootstrap scheme
    half = n // 2
    vr = max(float(np.var(u_null[:half])), float(np.var(u_null[half:]))) / max(
        min(float(np.var(u_null[:half])), float(np.var(u_null[half:]))), 1e-15
    )
    hetero = vr > 2.25
    het_chk = AssumptionCheck(
        name="homoskedastic_innovations",
        passed=not hetero,
        metric_name="half_sample_variance_ratio",
        metric_value=vr,
        threshold=2.25,
        action="iid_residual_bootstrap" if not hetero else "wild_rademacher_bootstrap_fallback",
    )

    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        u_null,
        allow_hac=allow_hac,
        prefer_block_bootstrap=allow_hac,
        alpha=alpha,
    )
    use_mbb = bool(ac_info["autocorrelated"] and allow_hac)
    use_wild = bool(hetero and not use_mbb)

    boot_reps = int(n_boot) if n_boot is not None else (249 if norm_mode == "full" else 99)
    p_boot = _bootstrap_sup_f_pvalue(
        ys,
        Xs,
        float(prof["sup_f"]),
        prof["beta_null"],
        u_null,
        trim=trim,
        n_boot=boot_reps,
        use_moving_block=use_mbb,
        use_wild=use_wild,
        seed=seed,
    )
    p_hansen = hansen_1997_p_approx(float(prof["sup_wald"]), k=k, trim=trim)

    eff = float(prof["cohen_f"])
    stacked = np.column_stack([ys, Xs])
    if use_mbb and n <= 400:
        ci_obj = moving_block_bootstrap_ci(
            stacked,
            lambda m: float(compute_quandt_andrews_profile(m[:, 0], m[:, 1:], trim=trim)["cohen_f"]),
            confidence_level=lv,
            n_boot=80,
            seed=seed,
        )
    else:
        ci_obj = bounded_effect_bootstrap_ci(
            stacked,
            lambda m: float(compute_quandt_andrews_profile(m[:, 0], m[:, 1:], trim=trim)["cohen_f"]),
            confidence_level=lv,
            bounds=(0.0, 50.0),
            n_boot=80,
            seed=seed,
            method_tag=(
                "naive_iid_bootstrap_qa_cohen_f"
                if inf_label == "naive"
                else "percentile_bootstrap_qa_cohen_f"
            ),
        )
        if inf_label == "naive":
            ci_obj = type(ci_obj)(
                low=ci_obj.low,
                high=ci_obj.high,
                confidence_level=ci_obj.confidence_level,
                method="naive_iid_bootstrap_qa_cohen_f (autocorrelation_uncorrected)",
                estimate=ci_obj.estimate,
                std_error=ci_obj.std_error,
                inference_label="naive",
            )

    ci_dict = build_ci_dict(ci_obj)
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=p_boot,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=eff,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=0.0,
        reject_means_fail=True,
    )
    pass_act = "retain_time_invariant_parameters"
    fail_act = "segment_at_estimated_break_or_fit_regime_model"
    action = fail_act if dec == "fail" else pass_act
    fallback_used = "moving_block_bootstrap" if use_mbb else ("wild_rademacher_bootstrap" if use_wild else ac_fb)
    interp = (
        f"Quandt-Andrews sup-F={prof['sup_f']:.4f} (sup-Wald={prof['sup_wald']:.4f}) at t_hat={prof['break_point']} "
        f"(bootstrap p_raw={p_boot:.4g}, Hansen_p={p_hansen:.4g}, Cohen_f={eff:.3f}): "
        f"{'unknown-date structural break detected' if rejected else 'no structural break across trimmed grid'}."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest="quandt_andrews_sup_f_bootstrap",
        stage="pre_model",
        family="breaks_regimes",
        target=target,
        variable=variable,
        statistic=float(prof["sup_f"]),
        p_raw=p_boot,
        p_adj=p_boot,
        df=(float(k), float(n - 2 * k)),
        effect_size=eff,
        effect_size_name="sup_break_cohen_f",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), het_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool((not hetero) and ac_chk.passed),
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
            "ave_f": round(float(prof["ave_f"]), 8),
            "bootstrap_replications": boot_reps,
            "bootstrap_scheme": "moving_block" if use_mbb else ("wild_rademacher" if use_wild else "iid_residual"),
            "estimated_break_point": int(prof["break_point"]),
            "exp_wald": round(float(prof["exp_wald"]), 8),
            "hansen_p_approx": round(p_hansen, 8),
            "policy_evaluation_note": rule_note,
            "subsampling": sub_info,
            "sup_f": round(float(prof["sup_f"]), 8),
            "sup_wald": round(float(prof["sup_wald"]), 8),
            "trim": trim,
        },
    )
