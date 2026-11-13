"""T2 — Seasonality detection: ACF/PACF peaks, Canova-Hansen harmonic F-test, Kruskal-Wallis fallback, QS & STL strength."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats as sp_stats
from statsmodels.tsa.stattools import pacf

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

TEST_ID = "T2"
TEST_NAME = "Seasonality Detection (ACF/PACF, Canova-Hansen & STL Strength)"


def _seasonal_strength_fs(arr: np.ndarray, period: int) -> float:
    """Compute STL-style seasonal variance strength Fs = max(0, 1 - Var(R)/Var(S+R))."""
    n = int(arr.size)
    if period < 2 or n < 2 * period:
        return 0.0
    # Detrend via linear trend or centered moving average of length period
    t_idx = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(t_idx, arr, 1)
    detrended = arr - (intercept + slope * t_idx)
    phase = np.arange(n) % period
    seasonal = np.zeros(n, dtype=float)
    for g in range(period):
        mask = phase == g
        if np.any(mask):
            seasonal[mask] = float(np.mean(detrended[mask]))
    seasonal -= float(np.mean(seasonal))
    resid = detrended - seasonal
    denom = float(np.var(seasonal + resid, ddof=1))
    if denom <= 1e-15:
        return 0.0
    return float(np.clip(1.0 - float(np.var(resid, ddof=1)) / denom, 0.0, 1.0))


def _canova_hansen_harmonic_f(arr: np.ndarray, period: int, allow_hac: bool = True) -> tuple[float, float, tuple[int, int]]:
    """Trigonometric Canova-Hansen harmonic F/Wald test at fundamental & second seasonal frequency."""
    n = int(arr.size)
    t = np.arange(n, dtype=float)
    cols = [np.ones(n, dtype=float), t / float(max(1, n))]
    harm_cols: list[np.ndarray] = []
    max_h = 2 if period >= 6 else 1
    for h in range(1, max_h + 1):
        omega = 2.0 * math.pi * float(h) / float(period)
        harm_cols.append(np.cos(omega * t))
        if not (2 * h == period):
            harm_cols.append(np.sin(omega * t))
    X_r = np.column_stack(cols)
    X_u = np.column_stack(cols + harm_cols)
    beta_r, *_ = np.linalg.lstsq(X_r, arr, rcond=None)
    beta_u, *_ = np.linalg.lstsq(X_u, arr, rcond=None)
    u_r = arr - X_r @ beta_r
    u_u = arr - X_u @ beta_u
    rss_r = float(np.dot(u_r, u_r))
    rss_u = float(np.dot(u_u, u_u))
    q = int(X_u.shape[1] - X_r.shape[1])
    df_resid = max(1, n - int(X_u.shape[1]))
    f_iid = max(0.0, ((rss_r - rss_u) / float(max(1, q))) / max(rss_u / float(df_resid), 1e-15))
    if allow_hac:
        # Scale by Bartlett residual autocorrelation factor so serial noise doesn't inflate seasonal F
        r1 = float(np.dot(u_u[1:], u_u[:-1]) / max(float(np.dot(u_u, u_u)), 1e-15)) if n > 4 else 0.0
        vif_ac = max(1.0, (1.0 + max(0.0, r1)) / max(0.15, 1.0 - max(0.0, r1)))
        f_eff = f_iid / vif_ac
    else:
        f_eff = f_iid
    p_val = float(sp_stats.f.sf(f_eff, q, df_resid))
    return f_eff, p_val, (q, df_resid)


def run_t2_seasonality(
    series: Any,
    *,
    period: int | None = None,
    max_lags: int | None = None,
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
    """Detect unmodelled seasonality via ACF/PACF peaks, Canova-Hansen harmonic test, Kruskal-Wallis & QS."""
    _ = max_lags
    lv = validate_ci_level(confidence_level)
    norm_mode = mode.strip().lower() if mode in ("fast", "full") else "full"
    if not enabled:
        return make_not_run_user_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            stage="pre_model",
            family="stationarity_trend",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    raw = np.asarray(series, dtype=float).reshape(-1)
    clean = raw[np.isfinite(raw)]
    eff_period = int(period) if (period is not None and int(period) >= 2) else (12 if clean.size >= 24 else 7)
    min_req = max(14, 2 * eff_period)
    if clean.size < min_req or float(np.std(clean, ddof=1)) <= 1e-12:
        return make_insufficient_data_result(
            test_id=TEST_ID,
            name=TEST_NAME,
            n_used=int(clean.size),
            min_required=min_req,
            reason=f"requires at least 2 full seasonal cycles (2*{eff_period}={min_req}) with non-zero variance",
            stage="pre_model",
            family="stationarity_trend",
            target=target,
            variable=variable,
            mode=norm_mode,
            confidence_level=lv,
        )

    sub, sub_info, sub_chk = deterministic_subsample(
        clean,
        mode=norm_mode,
        max_fast_n=4800,
        preserve_order=True,
        seed=seed,
    )
    n = int(sub.size)
    centered = sub - float(np.mean(sub))
    denom = float(np.dot(centered, centered))

    # ACF & PACF at seasonal lag
    acf_s = float(np.dot(centered[eff_period:], centered[:-eff_period]) / max(denom, 1e-15))
    acf_2s = (
        float(np.dot(centered[2 * eff_period :], centered[: -2 * eff_period]) / max(denom, 1e-15))
        if n > 2 * eff_period + 2
        else 0.0
    )
    max_nlags = min(eff_period, max(1, (n // 2) - 1))
    pacf_vals = pacf(sub, nlags=max_nlags, method="ywm")
    pacf_s = float(pacf_vals[eff_period]) if max_nlags >= eff_period else acf_s

    # Seasonal Ljung-Box QS statistic at {s, 2s} (positive seasonal autocorrelation only)
    pos_s = max(0.0, acf_s)
    pos_2s = max(0.0, acf_2s)
    qs_stat = float(n * (n + 2)) * (
        (pos_s * pos_s) / float(n - eff_period)
        + ((pos_2s * pos_2s) / float(n - 2 * eff_period) if n > 2 * eff_period + 2 else 0.0)
    )
    qs_p = float(sp_stats.chi2.sf(qs_stat, df=2 if n > 2 * eff_period + 2 else 1))

    # Check Gaussianity of deseasonalized residuals to decide parametric CH vs non-parametric Kruskal-Wallis fallback
    phase_groups = [sub[np.arange(n) % eff_period == g] for g in range(eff_period)]
    non_empty_groups = [g for g in phase_groups if g.size >= 2]
    shapiro_in = sub[:5000] - np.mean(sub[:5000])
    norm_p = float(sp_stats.shapiro(shapiro_in).pvalue) if shapiro_in.size >= 8 else 1.0
    norm_ok = norm_p >= 0.01

    inf_label, ac_fallback, ac_chk, ac_info = resolve_autocorrelation_inference(
        sub,
        allow_hac=allow_hac,
        prefer_block_bootstrap=(norm_mode == "full" and allow_hac),
        alpha=alpha,
    )
    norm_chk = AssumptionCheck(
        name="gaussian_innovations_for_harmonic_f",
        passed=norm_ok,
        metric_name="shapiro_p",
        metric_value=norm_p,
        threshold=0.01,
        action="canova_hansen_harmonic_f" if norm_ok else "switched_to_kruskal_wallis_seasonal",
    )

    ch_stat, ch_p, ch_df = _canova_hansen_harmonic_f(sub, eff_period, allow_hac=allow_hac)
    if len(non_empty_groups) >= 2:
        kw_res = sp_stats.kruskal(*non_empty_groups)
        kw_stat = float(kw_res.statistic)
        kw_p = float(kw_res.pvalue)
    else:
        kw_stat, kw_p = 0.0, 1.0

    if norm_ok:
        primary_stat = ch_stat
        primary_p = ch_p
        primary_df: float | tuple[float, ...] = (float(ch_df[0]), float(ch_df[1]))
        subtest = "canova_hansen_harmonic_f"
        fallback_used = ac_fallback
    else:
        primary_stat = kw_stat
        primary_p = kw_p
        primary_df = float(max(1, len(non_empty_groups) - 1))
        subtest = "kruskal_wallis_seasonal_fallback"
        fallback_used = "kruskal_wallis_seasonal"

    fs_strength = _seasonal_strength_fs(sub, eff_period)
    if norm_mode == "full" and allow_hac and n <= 800:
        ci_obj = moving_block_bootstrap_ci(
            sub,
            lambda a: _seasonal_strength_fs(a, eff_period),
            confidence_level=lv,
            block_length=max(2, eff_period),
            n_boot=200,
            seed=seed,
        )
    else:
        ci_obj = bounded_effect_bootstrap_ci(
            sub,
            lambda a: _seasonal_strength_fs(a, eff_period),
            confidence_level=lv,
            bounds=(0.0, 1.0),
            n_boot=120,
            seed=seed,
            method_tag=(
                "naive_iid_bootstrap_seasonal_strength"
                if inf_label == "naive"
                else "percentile_bootstrap_seasonal_strength"
            ),
        )
        if inf_label == "naive":
            ci_obj = type(ci_obj)(
                low=ci_obj.low,
                high=ci_obj.high,
                confidence_level=ci_obj.confidence_level,
                method="naive_iid_bootstrap_seasonal_strength (autocorrelation_uncorrected)",
                estimate=ci_obj.estimate,
                std_error=ci_obj.std_error,
                inference_label="naive",
            )

    ci_dict = build_ci_dict(ci_obj)
    # Rejecting H0 (no seasonality) indicates significant unmodelled seasonal structure requiring harmonics/dummies
    dec, rejected, rule_note = evaluate_contract_decision(
        p_for_decision=primary_p,
        alpha=alpha,
        decision_rule=decision_rule,
        effect_size=fs_strength,
        min_effect_size=min_effect_size,
        ci=ci_dict,
        null_value=0.0,
        reject_means_fail=True,
    )
    pass_act = "no_unmodelled_seasonality"
    fail_act = "add_seasonal_harmonics_or_dummies"
    action = fail_act if dec == "fail" else pass_act
    interp = (
        f"Period s={eff_period}: {'significant unmodelled seasonality detected' if rejected else 'no significant seasonality'} "
        f"({subtest}={primary_stat:.3f}, p_raw={primary_p:.4g}, STL Fs={fs_strength:.3f}, ACF(s)={acf_s:.3f}, QS_p={qs_p:.4g})."
    )

    return TestContractResult(
        test_id=TEST_ID,
        name=TEST_NAME,
        subtest=subtest,
        stage="pre_model",
        family="stationarity_trend",
        target=target,
        variable=variable,
        statistic=primary_stat,
        p_raw=primary_p,
        p_adj=primary_p,
        df=primary_df,
        effect_size=fs_strength,
        effect_size_name="stl_seasonal_strength_fs",
        ci=ci_dict,
        n_used=n,
        assumptions_checked=(sub_chk.to_dict(), norm_chk.to_dict(), ac_chk.to_dict()),
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
            "acf_2s": round(acf_2s, 6),
            "acf_s": round(acf_s, 6),
            "autocorrelation_check": ac_info,
            "canova_hansen_f": round(ch_stat, 8),
            "canova_hansen_p": round(ch_p, 8),
            "kruskal_wallis_h": round(kw_stat, 8),
            "kruskal_wallis_p": round(kw_p, 8),
            "pacf_s": round(pacf_s, 6),
            "period": eff_period,
            "policy_evaluation_note": rule_note,
            "qs_p": round(qs_p, 8),
            "qs_statistic": round(qs_stat, 8),
            "seasonality_detected": bool(rejected),
            "subsampling": sub_info,
        },
    )
