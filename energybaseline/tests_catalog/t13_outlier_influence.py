"""T13 — Flag-only outlier & influence diagnostics: Cook's distance, Bonferroni Studentized residuals & ESD (G-6)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats as sp_stats

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

TEST_ID = "T13"
TEST_NAME = "Flag-Only Outlier & Cook's Distance Influence Diagnostics"


def compute_influence_diagnostics(y: np.ndarray, X: np.ndarray) -> dict[str, Any]:
    """Compute leverage h_ii, externally Studentized residuals t_i, Cook's distance D_i, and robust MAD z-scores."""
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    y_hat = X @ beta
    e = y - y_hat
    df_resid = max(1, n - k)
    mse = max(float(np.dot(e, e)) / float(df_resid), 1e-18)

    # Hat matrix diagonal h_ii = diag(X (X'X)^+ X') computed in O(n * k^2) without forming n x n matrix!
    xtx_inv = np.linalg.pinv(X.T @ X, rcond=1e-12)
    h_diag = np.einsum("ij,jk,ik->i", X, xtx_inv, X)
    h_diag = np.clip(h_diag, 0.0, 0.9999)

    one_minus_h = np.maximum(1.0 - h_diag, 1e-6)
    intern_stud = e / np.sqrt(mse * one_minus_h)
    cooks_d = (intern_stud * intern_stud / float(k)) * (h_diag / one_minus_h)

    # Externally studentized (leave-one-out deleted) residuals t_i ~ t_{n - k - 1}
    df_del = max(1, n - k - 1)
    denom_del = np.maximum(float(df_resid) - intern_stud * intern_stud, 1e-9)
    ext_stud = intern_stud * np.sqrt(float(df_del) / denom_del)
    abs_ext = np.abs(ext_stud)
    max_idx = int(np.argmax(abs_ext))
    max_abs_t = float(abs_ext[max_idx])
    p_bonf = min(1.0, float(n) * 2.0 * float(sp_stats.t.sf(max_abs_t, df=df_del)))

    # Robust modified Z-score (Iglewicz & Hoaglin MAD outlier fallback)
    med_e = float(np.median(e))
    mad_e = float(np.median(np.abs(e - med_e))) * 1.4826
    scale_rob = mad_e if mad_e > 1e-12 else math.sqrt(mse)
    rob_z = np.abs(e - med_e) / max(scale_rob, 1e-12)
    max_rob_z = float(np.max(rob_z))

    cooks_cutoff = 4.0 / float(n)
    t_crit_bonf = float(sp_stats.t.ppf(1.0 - 0.05 / (2.0 * float(n)), df=df_del))
    flagged_mask = (cooks_d > cooks_cutoff) & (abs_ext > min(3.0, t_crit_bonf))
    flagged_indices = np.flatnonzero(flagged_mask).astype(int).tolist()

    return {
        "bonferroni_outlier_p": p_bonf,
        "cooks_cutoff_4_over_n": cooks_cutoff,
        "cooks_d": cooks_d,
        "df_deleted": df_del,
        "flagged_indices": flagged_indices,
        "leverage_max": float(np.max(h_diag)),
        "max_abs_ext_studentized": max_abs_t,
        "max_cooks_d": float(np.max(cooks_d)),
        "max_cooks_index": int(np.argmax(cooks_d)),
        "max_robust_z": max_rob_z,
        "residuals": e,
    }


def compute_ramsey_reset_harvey_collier(
    y: Any,
    exog: Any,
    *,
    power: int = 3,
) -> dict[str, float]:
    """Ramsey (1969) RESET polynomial specification F-test & recursive-residual Harvey-Collier t-test."""
    yv = np.asarray(y, dtype=float).reshape(-1)
    X = _design_with_intercept(yv, exog)
    n, k = X.shape
    pw = max(2, int(power))
    beta_r, *_ = np.linalg.lstsq(X, yv, rcond=None)
    yhat = X @ beta_r
    u_r = yv - yhat
    rss_r = float(np.dot(u_r, u_r))

    yhat_std = (yhat - float(np.mean(yhat))) / max(float(np.std(yhat, ddof=1)), 1e-12)
    poly_cols = [yhat_std**m for m in range(2, pw + 1)]
    X_u = np.column_stack([X, *poly_cols])
    q = len(poly_cols)
    beta_u, *_ = np.linalg.lstsq(X_u, yv, rcond=None)
    u_u = yv - X_u @ beta_u
    rss_u = float(np.dot(u_u, u_u))
    df2 = max(1, n - k - q)
    f_reset = max(0.0, ((rss_r - rss_u) / float(q)) / max(rss_u / float(df2), 1e-18))
    p_reset = float(np.clip(sp_stats.f.sf(f_reset, q, df2), 0.0, 1.0))

    # Recursive residuals ordered along primary driver for Harvey-Collier linearity t-test
    order = np.argsort(X[:, 1] if k > 1 else np.arange(n))
    yo, Xo = yv[order], X[order]
    rec_res: list[float] = []
    for t in range(k + 1, n):
        bt, *_ = np.linalg.lstsq(Xo[:t], yo[:t], rcond=None)
        xt = Xo[t]
        xtx_inv = np.linalg.pinv(Xo[:t].T @ Xo[:t], rcond=1e-12)
        ft = math.sqrt(max(1.0 + float(xt.T @ xtx_inv @ xt), 1e-12))
        rec_res.append(float(yo[t] - float(xt @ bt)) / ft)
    rec_arr = np.asarray(rec_res, dtype=float)
    if rec_arr.size >= 4 and float(np.std(rec_arr, ddof=1)) > 1e-15:
        hc_t = float(np.mean(rec_arr)) / (float(np.std(rec_arr, ddof=1)) / math.sqrt(float(rec_arr.size)))
        hc_p = float(np.clip(2.0 * sp_stats.t.sf(abs(hc_t), df=rec_arr.size - 1), 0.0, 1.0))
    else:
        hc_t, hc_p = 0.0, 1.0

    return {
        "harvey_collier_p": hc_p,
        "harvey_collier_t": hc_t,
        "reset_df1": float(q),
        "reset_df2": float(df2),
        "reset_f": f_reset,
        "reset_p": p_reset,
    }


def compute_lagged_ccf_and_granger(
    target_y: Any,
    driver_x: Any,
    *,
    max_lag: int = 4,
) -> dict[str, Any]:
    """Lagged cross-correlation peak & bivariate Granger causality F-test (H0: driver_x does not Granger-cause y)."""
    yv = np.asarray(target_y, dtype=float).reshape(-1)
    xv = np.asarray(driver_x, dtype=float).reshape(-1)
    n = min(yv.size, xv.size)
    yv, xv = yv[:n], xv[:n]
    L = min(max(1, int(max_lag)), max(1, (n - 6) // 3))

    yc = (yv - float(np.mean(yv))) / max(float(np.std(yv, ddof=1)), 1e-12)
    xc = (xv - float(np.mean(xv))) / max(float(np.std(xv, ddof=1)), 1e-12)
    ccf_by_lag: dict[int, float] = {}
    for lag in range(0, L + 1):
        if lag == 0:
            ccf_by_lag[0] = float(np.dot(yc, xc)) / float(n)
        else:
            ccf_by_lag[lag] = float(np.dot(yc[lag:], xc[:-lag])) / float(n - lag)
    best_lag = max(ccf_by_lag, key=lambda k_lag: abs(ccf_by_lag[k_lag]))

    # Restricted AR(L) vs Unrestricted ARX(L, L)
    y_dep = yv[L:]
    n_eff = int(y_dep.size)
    y_lags = np.column_stack([yv[L - j : n - j] for j in range(1, L + 1)])
    x_lags = np.column_stack([xv[L - j : n - j] for j in range(1, L + 1)])
    Z_r = np.column_stack([np.ones(n_eff, dtype=float), y_lags])
    Z_u = np.column_stack([Z_r, x_lags])

    br, *_ = np.linalg.lstsq(Z_r, y_dep, rcond=None)
    bu, *_ = np.linalg.lstsq(Z_u, y_dep, rcond=None)
    rss_r = float(np.dot(y_dep - Z_r @ br, y_dep - Z_r @ br))
    rss_u = float(np.dot(y_dep - Z_u @ bu, y_dep - Z_u @ bu))
    df2 = max(1, n_eff - int(Z_u.shape[1]))
    f_granger = max(0.0, ((rss_r - rss_u) / float(L)) / max(rss_u / float(df2), 1e-18))
    p_granger = float(np.clip(sp_stats.f.sf(f_granger, L, df2), 0.0, 1.0))

    return {
        "best_ccf": ccf_by_lag[best_lag],
        "best_lag": best_lag,
        "ccf_by_lag": ccf_by_lag,
        "granger_df1": L,
        "granger_df2": df2,
        "granger_f": f_granger,
        "granger_p": p_granger,
    }


def run_t13_outlier_influence(
    y: Any,
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
    """Strictly flag-only Cook's distance & Bonferroni studentized outlier test (never mutates/deletes rows)."""
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

    y_arr = np.asarray(y, dtype=float).reshape(-1)
    X_raw = _design_with_intercept(y_arr, exog) if y_arr.size > 0 else np.empty((0, 1))
    valid = np.isfinite(y_arr) & np.all(np.isfinite(X_raw), axis=1)
    yc = y_arr[valid]
    Xc = X_raw[valid]
    k = int(Xc.shape[1]) if Xc.ndim == 2 else 1

    if yc.size < max(10, k + 4) or float(np.std(yc, ddof=1)) <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(yc.size),
            min_required=max(10, k + 4),
            reason="requires >= max(10, k+4) finite non-constant observations for Cook's distance",
            stage="pre_model",
            family="residual_diagnostics",
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
    ys = yc[idx_sub]
    Xs = Xc[idx_sub]
    n = int(ys.size)

    diag = compute_influence_diagnostics(ys, Xs)
    e = diag["residuals"]

    # Check bulk Gaussianity (trimmed middle 90% so a single outlier doesn't invalidate its own assumption check!)
    q05, q95 = np.quantile(e, [0.05, 0.95])
    core_e = e[(e >= q05) & (e <= q95)]
    core_sw_p = float(sp_stats.shapiro(core_e[:5000]).pvalue) if core_e.size >= 8 else 1.0
    core_gauss = core_sw_p >= 0.01

    gauss_chk = AssumptionCheck(
        name="core_bulk_gaussianity_for_studentized_bonferroni",
        passed=core_gauss,
        metric_name="trimmed_90pct_shapiro_p",
        metric_value=core_sw_p,
        threshold=0.01,
        action="bonferroni_studentized_cooks_d" if core_gauss else "augmented_with_mad_robust_z_fallback",
    )
    flag_only_chk = AssumptionCheck(
        name="flag_only_zero_mutation_policy",
        passed=True,
        metric_name="rows_deleted",
        metric_value=0,
        threshold=0,
        action="flag_only_preserve_all_rows",
    )
    inf_label, ac_fb, ac_chk, ac_info = resolve_autocorrelation_inference(
        e,
        allow_hac=allow_hac,
        prefer_block_bootstrap=(norm_mode == "full" and allow_hac and n <= 500),
        alpha=alpha,
    )

    max_cooks = float(diag["max_cooks_d"])
    p_bonf = float(diag["bonferroni_outlier_p"])
    if not core_gauss:
        # Non-parametric / heavy-core MAD tail bound via Chebyshev-Gauss-Vysochanskij-Petunin inequality
        z_rob = float(diag["max_robust_z"])
        p_rob = min(1.0, float(n) * (4.0 / (9.0 * max(z_rob * z_rob, 1.0)))) if z_rob > 3.5 else 1.0
        p_raw = min(p_bonf, p_rob) if max_cooks > float(diag["cooks_cutoff_4_over_n"]) else p_bonf
        fallback_used = "mad_vysochanskij_petunin_bound"
        subtest = "cooks_distance_mad_flag_only"
    else:
        p_raw = p_bonf
        fallback_used = ac_fb
        subtest = "cooks_distance_bonferroni_flag_only"

    stacked = np.column_stack([ys, Xs])
    if inf_label == "block_bootstrap" and n <= 400:
        ci_obj = moving_block_bootstrap_ci(
            stacked,
            lambda m: float(compute_influence_diagnostics(m[:, 0], m[:, 1:])["max_cooks_d"]),
            confidence_level=lv,
            n_boot=100,
            seed=seed,
        )
    else:
        ci_obj = bounded_effect_bootstrap_ci(
            stacked,
            lambda m: float(compute_influence_diagnostics(m[:, 0], m[:, 1:])["max_cooks_d"]),
            confidence_level=lv,
            bounds=(0.0, 100.0),
            n_boot=100,
            seed=seed,
            method_tag=(
                "naive_iid_bootstrap_max_cooks_d"
                if inf_label == "naive"
                else "percentile_bootstrap_max_cooks_d"
            ),
        )
        if inf_label == "naive":
            ci_obj = type(ci_obj)(
                low=ci_obj.low,
                high=ci_obj.high,
                confidence_level=ci_obj.confidence_level,
                method="naive_iid_bootstrap_max_cooks_d (autocorrelation_uncorrected)",
                estimate=ci_obj.estimate,
                std_error=ci_obj.std_error,
                inference_label="naive",
            )

    ci_dict = build_ci_dict(ci_obj)
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=p_raw,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=max_cooks,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=0.0,
        reject_means_fail=True,
    )
    pass_act = "no_influential_outliers_flagged"
    fail_act = "flag_only_preserve_all_rows"
    action = fail_act if dec == "fail" else pass_act
    interp = (
        f"Flag-only influence check: max Cook's D={max_cooks:.4f} (4/n={diag['cooks_cutoff_4_over_n']:.4f}), "
        f"max |studentized t|={diag['max_abs_ext_studentized']:.3f} (Bonferroni p_raw={p_raw:.4g}), "
        f"flagged_rows={len(diag['flagged_indices'])} (0 rows deleted)."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest=subtest,
        stage="pre_model",
        family="residual_diagnostics",
        target=target,
        variable=variable,
        statistic=float(diag["max_abs_ext_studentized"]),
        p_raw=p_raw,
        p_adj=p_raw,
        df=float(diag["df_deleted"]),
        effect_size=max_cooks,
        effect_size_name="max_cooks_distance",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), gauss_chk.to_dict(), flag_only_chk.to_dict(), ac_chk.to_dict()),
        assumptions_met=bool(core_gauss and ac_chk.passed),
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
            "bonferroni_outlier_p": round(p_bonf, 10),
            "cooks_cutoff_4_over_n": round(float(diag["cooks_cutoff_4_over_n"]), 8),
            "flag_only": True,
            "flagged_count": len(diag["flagged_indices"]),
            "flagged_indices": diag["flagged_indices"][:100],
            "leverage_max": round(float(diag["leverage_max"]), 8),
            "max_abs_ext_studentized": round(float(diag["max_abs_ext_studentized"]), 8),
            "max_cooks_d": round(max_cooks, 8),
            "max_cooks_index": int(diag["max_cooks_index"]),
            "max_robust_z": round(float(diag["max_robust_z"]), 8),
            "policy_evaluation_note": rule_note,
            "rows_deleted": 0,
            "subsampling": sub_info,
        },
    )
