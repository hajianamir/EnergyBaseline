"""T7 — Known-date Chow structural break test (validated against R strucchange::sctest Chow reference)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
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

TEST_ID = "T7"
TEST_NAME = "Chow Known-Date Structural Break Test"


def _design_with_intercept(y: np.ndarray, exog: Any | None) -> np.ndarray:
    n = int(y.size)
    if exog is None:
        return np.ones((n, 1), dtype=float)
    X = np.asarray(exog, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    if X.shape[0] != n:
        raise ValueError(f"exog rows ({X.shape[0]}) != y length ({n}).")
    col_stds = np.std(X, axis=0)
    if not np.any(col_stds <= 1e-12):
        X = np.column_stack([np.ones(n, dtype=float), X])
    return X


def compute_chow_classical(
    y: np.ndarray,
    X: np.ndarray,
    break_point: int,
) -> dict[str, Any]:
    """Exact classical Chow F-test matching ``strucchange::sctest(y ~ x, type='Chow', point=break_point)``.

    ``break_point`` is the number of observations in regime 1 (i.e. 1-based end of first regime,
    identical to ``point`` in ``strucchange::sctest``).
    """
    n, k = X.shape
    n1 = int(break_point)
    n2 = n - n1
    if n1 <= k or n2 <= k:
        raise ValueError(f"Chow test requires break_point in ({k + 1}, {n - k - 1}), got {n1} for n={n}, k={k}.")

    beta_p, *_ = np.linalg.lstsq(X, y, rcond=None)
    u_p = y - X @ beta_p
    rss_p = float(np.dot(u_p, u_p))

    X1, y1 = X[:n1], y[:n1]
    X2, y2 = X[n1:], y[n1:]
    beta_1, *_ = np.linalg.lstsq(X1, y1, rcond=None)
    beta_2, *_ = np.linalg.lstsq(X2, y2, rcond=None)
    u_1 = y1 - X1 @ beta_1
    u_2 = y2 - X2 @ beta_2
    rss_1 = float(np.dot(u_1, u_1))
    rss_2 = float(np.dot(u_2, u_2))
    rss_u = rss_1 + rss_2

    df1 = k
    df2 = n - 2 * k
    num = max(0.0, (rss_p - rss_u) / float(df1))
    den = max(rss_u / float(df2), 1e-15)
    f_stat = num / den
    p_val = float(np.clip(sp_stats.f.sf(f_stat, df1, df2), 0.0, 1.0))

    delta_beta = beta_2 - beta_1
    pooled_sd = math.sqrt(den)
    # Standardized parameter shift magnitude (Cohen's f = sqrt((RSS_P - RSS_U) / RSS_U))
    cohen_f = math.sqrt(max(0.0, (rss_p - rss_u) / max(rss_u, 1e-15)))
    return {
        "beta_1": beta_1,
        "beta_2": beta_2,
        "cohen_f": cohen_f,
        "delta_beta": delta_beta,
        "df1": df1,
        "df2": df2,
        "f_stat": f_stat,
        "p_raw": p_val,
        "pooled_sd": pooled_sd,
        "rss_1": rss_1,
        "rss_2": rss_2,
        "rss_pooled": rss_p,
        "u_unrestricted": np.concatenate([u_1, u_2]),
    }


def _hac_wald_break_test(
    y: np.ndarray,
    X: np.ndarray,
    break_point: int,
    *,
    use_hac: bool,
) -> tuple[float, float]:
    """HC3 / Newey-West HAC Wald structural break test on interacted regime design matrix."""
    n, k = X.shape
    n1 = int(break_point)
    ind = np.zeros(n, dtype=float)
    ind[n1:] = 1.0
    X_int = X * ind[:, None]
    Z = np.column_stack([X, X_int])
    theta, *_ = np.linalg.lstsq(Z, y, rcond=None)
    u = y - Z @ theta
    ztz_inv = np.linalg.pinv(Z.T @ Z)

    # HC1/HC3 meat + optional Bartlett Newey-West lags
    meat = (Z * (u * u)[:, None]).T @ Z * (float(n) / float(max(1, n - 2 * k)))
    if use_hac:
        bw = max(1, int(math.floor(4.0 * ((n / 100.0) ** (2.0 / 9.0)))))
        bw = min(bw, n - 2)
        zu = Z * u[:, None]
        for lag in range(1, bw + 1):
            w = 1.0 - float(lag) / float(bw + 1)
            gamma_l = zu[lag:].T @ zu[:-lag]
            meat = meat + w * (gamma_l + gamma_l.T)

    cov_theta = ztz_inv @ meat @ ztz_inv
    delta = theta[k:]
    cov_delta = cov_theta[k:, k:]
    wald = float(delta.T @ np.linalg.pinv(cov_delta) @ delta)
    f_robust = max(0.0, wald / float(k))
    p_robust = float(np.clip(sp_stats.f.sf(f_robust, k, max(1, n - 2 * k)), 0.0, 1.0))
    return f_robust, p_robust


def run_t7_chow(
    y: Any,
    exog: Any | None = None,
    *,
    break_point: int | None = None,
    alpha: float = 0.05,
    confidence_level: float = 0.95,
    mode: str = "full",
    enabled: bool = True,
    target: str = "",
    variable: str = "",
    allow_hac: bool = True,
    use_classical_f: bool = False,
    decision_rule: str = DecisionRule.P_ONLY.value,
    min_effect_size: float = 0.0,
    seed: int = 42,
) -> TestContractResult:
    """Execute Chow known-date structural break test (with automatic HC/HAC Wald fallback when assumptions fail)."""
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

    if yc.size < max(12, 2 * k + 4) or float(np.std(yc, ddof=1)) <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(yc.size),
            min_required=max(12, 2 * k + 4),
            reason="insufficient observations or constant target for two-regime Chow split",
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
        max_fast_n=6000,
        preserve_order=True,
        seed=seed,
    )
    if break_point is not None and sub_info["applied"]:
        eff_bp = int(round(float(break_point) * float(idx_sub.size) / float(yc.size)))
    elif break_point is not None:
        eff_bp = int(break_point)
    else:
        eff_bp = int(idx_sub.size // 2)

    ys = yc[idx_sub]
    Xs = Xc[idx_sub]
    n = int(ys.size)
    eff_bp = min(max(k + 2, eff_bp), n - k - 2)

    classical = compute_chow_classical(ys, Xs, eff_bp)
    u_unr = classical["u_unrestricted"]

    # Check homoskedasticity across regimes (variance ratio F-test) & serial correlation
    v1 = float(np.var(u_unr[:eff_bp], ddof=k))
    v2 = float(np.var(u_unr[eff_bp:], ddof=k))
    var_ratio = max(v1, v2) / max(min(v1, v2), 1e-15)
    vr_p = 2.0 * float(sp_stats.f.sf(var_ratio, eff_bp - k, n - eff_bp - k))
    homosked_ok = vr_p >= 0.01

    homo_chk = AssumptionCheck(
        name="equal_regime_error_variance",
        passed=homosked_ok,
        metric_name="variance_ratio",
        metric_value=var_ratio,
        threshold="p>=0.01",
        action="classical_chow_f" if homosked_ok else "switched_to_hc_hac_wald_break_test",
    )
    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        u_unr,
        allow_hac=allow_hac,
        prefer_block_bootstrap=False,
        alpha=alpha,
    )

    need_robust = (not use_classical_f) and ((not homosked_ok) or (ac_info["autocorrelated"] and allow_hac))
    if need_robust:
        stat_val, p_raw = _hac_wald_break_test(ys, Xs, eff_bp, use_hac=bool(ac_info["autocorrelated"] and allow_hac))
        subtest = "chow_hac_wald_fallback" if (ac_info["autocorrelated"] and allow_hac) else "chow_hc_wald_fallback"
        fallback_used = "hac_wald_chow" if (ac_info["autocorrelated"] and allow_hac) else "hc_wald_chow"
    else:
        stat_val = float(classical["f_stat"])
        p_raw = float(classical["p_raw"])
        subtest = "chow_classical_f"
        fallback_used = ac_fb

    eff_size = float(classical["cohen_f"])
    se_eff = max(1e-6, 1.0 / math.sqrt(float(max(4, n - 2 * k))))
    if norm_mode == "full" and allow_hac and ac_info["autocorrelated"] and n <= 500:
        stacked = np.column_stack([ys, Xs])
        ci_obj = moving_block_bootstrap_ci(
            stacked,
            lambda m: float(compute_chow_classical(m[:, 0], m[:, 1:], eff_bp)["cohen_f"]),
            confidence_level=lv,
            n_boot=150,
            seed=seed,
        )
    else:
        ci_obj = exact_t_ci(
            eff_size,
            se_eff,
            df=float(classical["df2"]),
            confidence_level=lv,
            method_name=(
                "naive_chow_cohen_f_t (autocorrelation_uncorrected)"
                if inf_label == "naive"
                else ("hac_chow_cohen_f_t" if need_robust else "exact_chow_cohen_f_t")
            ),
            inference_label=inf_label,
        )

    ci_dict = build_ci_dict(ci_obj)
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=p_raw,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=eff_size,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=0.0,
        reject_means_fail=True,
    )
    pass_act = "retain_single_regime_baseline"
    fail_act = "segment_baseline_or_add_nre_regime_indicator"
    action = fail_act if dec == "fail" else pass_act
    interp = (
        f"Chow break test at t={eff_bp} ({subtest}={stat_val:.4f}, p_raw={p_raw:.4g}, "
        f"classical_F={classical['f_stat']:.4f}, Cohen_f={eff_size:.3f}): "
        f"{'significant structural break detected' if rejected else 'parameter stability retained'}."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest=subtest,
        stage="pre_model",
        family="breaks_regimes",
        target=target,
        variable=variable,
        statistic=stat_val,
        p_raw=p_raw,
        p_adj=p_raw,
        df=(float(classical["df1"]), float(classical["df2"])),
        effect_size=eff_size,
        effect_size_name="cohen_f_regime_shift",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), homo_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool(homosked_ok and ac_chk.passed),
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
            "break_point": eff_bp,
            "classical_chow_f": round(float(classical["f_stat"]), 8),
            "classical_chow_p": round(float(classical["p_raw"]), 10),
            "df": [int(classical["df1"]), int(classical["df2"])],
            "policy_evaluation_note": rule_note,
            "rss_1": round(float(classical["rss_1"]), 8),
            "rss_2": round(float(classical["rss_2"]), 8),
            "rss_pooled": round(float(classical["rss_pooled"]), 8),
            "subsampling": sub_info,
            "variance_ratio": round(var_ratio, 6),
        },
    )
