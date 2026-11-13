"""Confidence-interval estimators with explicit method disclosure and autocorrelation guards (CI-1..4, TC-5)."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from scipy import stats as sp_stats


class CIMethodError(ValueError):
    """Raised when a confidence-interval level or input vector is invalid."""


def validate_ci_level(confidence_level: float) -> float:
    """Validate confidence level in ``[0.50, 0.9999]`` (supports percent > 1.0)."""
    lv = float(confidence_level)
    if lv > 1.0:
        lv = lv / 100.0
    if not math.isfinite(lv) or lv < 0.50 or lv >= 1.0:
        raise CIMethodError(f"confidence_level must lie in [0.50, 1.0), got {confidence_level!r}.")
    return round(lv, 6)


@dataclass(frozen=True)
class ConfidenceInterval:
    """Typed confidence interval carrying explicit ``method`` and autocorrelation status."""

    low: float
    high: float
    confidence_level: float
    method: str
    estimate: float
    std_error: float
    inference_label: str = "standard"

    def contains(self, value: float) -> bool:
        return bool(self.low <= value <= self.high)

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence_level": self.confidence_level,
            "estimate": round(float(self.estimate), 8),
            "high": round(float(self.high), 8),
            "inference_label": self.inference_label,
            "low": round(float(self.low), 8),
            "method": self.method,
            "std_error": round(float(self.std_error), 8),
        }


def detect_serial_autocorrelation(
    series: np.ndarray,
    *,
    alpha: float = 0.05,
    rho1_threshold: float = 0.20,
) -> dict[str, Any]:
    """Detect material first-order / multi-lag serial correlation in a 1-D series."""
    arr = np.asarray(series, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n < 8:
        return {"autocorrelated": False, "lag1_r": 0.0, "ljung_box_p": 1.0, "n": n}
    centered = arr - float(np.mean(arr))
    denom = float(np.dot(centered, centered))
    if denom <= 1e-15:
        return {"autocorrelated": False, "lag1_r": 0.0, "ljung_box_p": 1.0, "n": n}

    max_lag = min(10, max(1, n // 5))
    q_stat = 0.0
    lag1_r = 0.0
    for k in range(1, max_lag + 1):
        rk = float(np.dot(centered[k:], centered[:-k])) / denom
        if k == 1:
            lag1_r = rk
        q_stat += (rk * rk) / float(n - k)
    q_stat *= float(n * (n + 2))
    lb_p = float(sp_stats.chi2.sf(max(0.0, q_stat), df=max_lag))
    is_ac = bool((abs(lag1_r) >= rho1_threshold and lb_p <= max(alpha, 0.05)) or lb_p <= min(alpha, 0.01))
    return {
        "autocorrelated": is_ac,
        "lag1_r": round(lag1_r, 6),
        "ljung_box_p": round(lb_p, 8),
        "n": n,
    }


def newey_west_se_mean(series: np.ndarray, bandwidth: int | None = None) -> tuple[float, int]:
    """Compute Bartlett-kernel Newey-West HAC standard error of the sample mean."""
    arr = np.asarray(series, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n < 2:
        return 0.0, 0
    bw = int(bandwidth) if bandwidth is not None else max(1, int(math.floor(4.0 * ((n / 100.0) ** (2.0 / 9.0)))))
    bw = min(bw, n - 1)
    u = arr - float(np.mean(arr))
    gamma_0 = float(np.dot(u, u)) / float(n)
    long_run_var = gamma_0
    for lag in range(1, bw + 1):
        weight = 1.0 - float(lag) / float(bw + 1)
        gamma_k = float(np.dot(u[lag:], u[:-lag])) / float(n)
        long_run_var += 2.0 * weight * gamma_k
    # Finite-sample floor at 10% of white-noise variance prevents negative Bartlett numerical edge cases
    long_run_var = max(long_run_var, 0.1 * gamma_0)
    se = math.sqrt(long_run_var / float(n))
    return se, bw


def exact_t_ci(
    estimate: float,
    std_error: float,
    df: float,
    confidence_level: float = 0.95,
    *,
    method_name: str = "exact_student_t",
    inference_label: str = "standard",
) -> ConfidenceInterval:
    """Student-t (or normal limit for df >= 5000) confidence interval."""
    lv = validate_ci_level(confidence_level)
    se = max(0.0, float(std_error))
    dof = max(1.0, float(df))
    tail = 0.5 * (1.0 + lv)
    crit = float(sp_stats.t.ppf(tail, df=dof)) if dof < 5000.0 else float(sp_stats.norm.ppf(tail))
    est = float(estimate)
    return ConfidenceInterval(
        low=est - crit * se,
        high=est + crit * se,
        confidence_level=lv,
        method=method_name,
        estimate=est,
        std_error=se,
        inference_label=inference_label,
    )


def mean_ci_with_autocorrelation_guard(
    series: np.ndarray,
    confidence_level: float = 0.95,
    *,
    allow_hac: bool = True,
    use_block_bootstrap: bool = False,
    prefer_block_bootstrap: bool = False,
    seed: int = 42,
    n_boot: int = 400,
) -> ConfidenceInterval:
    """Estimate CI for the mean of ``series``, applying HAC/block-bootstrap or labelling ``naive``."""
    lv = validate_ci_level(confidence_level)
    arr = np.asarray(series, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n == 0:
        return ConfidenceInterval(0.0, 0.0, lv, "empty_sample", 0.0, 0.0, "standard")
    est = float(np.mean(arr))
    if n == 1:
        return ConfidenceInterval(est, est, lv, "singleton_degenerate", est, 0.0, "standard")

    ac_info = detect_serial_autocorrelation(arr)
    is_ac = bool(ac_info["autocorrelated"])

    if (use_block_bootstrap or prefer_block_bootstrap) and n >= 8:
        return moving_block_bootstrap_ci(
            arr,
            np.mean,
            confidence_level=lv,
            seed=seed,
            n_boot=n_boot,
        )

    if is_ac and allow_hac:
        se_hac, bw = newey_west_se_mean(arr)
        return exact_t_ci(
            est,
            se_hac,
            df=max(1, n - 1),
            confidence_level=lv,
            method_name=f"hac_newey_west_t(bw={bw})",
            inference_label="hac_robust",
        )

    se_iid = float(np.std(arr, ddof=1)) / math.sqrt(float(n))
    if is_ac and not allow_hac:
        # Gate G4 Criterion #4: autocorrelated series without HAC/block-bootstrap MUST be labelled 'naive'
        return exact_t_ci(
            est,
            se_iid,
            df=max(1, n - 1),
            confidence_level=lv,
            method_name="naive_iid_student_t (autocorrelation_uncorrected)",
            inference_label="naive",
        )
    return exact_t_ci(
        est,
        se_iid,
        df=max(1, n - 1),
        confidence_level=lv,
        method_name="exact_student_t",
        inference_label="standard",
    )


def moving_block_bootstrap_ci(
    data: np.ndarray,
    stat_fn: Any,
    confidence_level: float = 0.95,
    *,
    block_length: int | None = None,
    n_boot: int = 400,
    seed: int = 42,
) -> ConfidenceInterval:
    """Deterministic circular Moving Block Bootstrap (MBB) percentile CI for dependent series."""
    lv = validate_ci_level(confidence_level)
    arr = np.asarray(data)
    n = int(arr.shape[0])
    est = float(stat_fn(arr))
    if n < 4:
        return ConfidenceInterval(est, est, lv, "moving_block_bootstrap_degenerate", est, 0.0, "block_bootstrap")

    b_len = int(block_length) if block_length is not None else max(2, int(round(n ** (1.0 / 3.0))))
    b_len = min(max(1, b_len), n)
    n_blocks = int(math.ceil(float(n) / float(b_len)))
    rng = np.random.default_rng(int(seed))

    extended = np.concatenate([arr, arr[: b_len - 1]], axis=0) if b_len > 1 else arr
    starts = rng.integers(0, n, size=(int(n_boot), n_blocks))
    offsets = np.arange(b_len)
    reps = np.empty(int(n_boot), dtype=float)
    for b in range(int(n_boot)):
        idx = (starts[b][:, None] + offsets[None, :]).reshape(-1)[:n]
        reps[b] = float(stat_fn(extended[idx]))

    finite_reps = reps[np.isfinite(reps)]
    if finite_reps.size < 4:
        return ConfidenceInterval(est, est, lv, f"moving_block_bootstrap(b={b_len})", est, 0.0, "block_bootstrap")
    alpha_tail = 0.5 * (1.0 - lv)
    lo = float(np.quantile(finite_reps, alpha_tail))
    hi = float(np.quantile(finite_reps, 1.0 - alpha_tail))
    se = float(np.std(finite_reps, ddof=1))
    return ConfidenceInterval(
        low=min(lo, est),
        high=max(hi, est),
        confidence_level=lv,
        method=f"moving_block_bootstrap_percentile(b={b_len},B={int(n_boot)})",
        estimate=est,
        std_error=se,
        inference_label="block_bootstrap",
    )


def fisher_z_ci(
    r: float,
    n: int,
    confidence_level: float = 0.95,
    *,
    autocorrelated: bool = False,
    allow_hac: bool = True,
    effective_n: float | None = None,
) -> ConfidenceInterval:
    """Fisher z-transform confidence interval for Pearson / Spearman correlation or R."""
    lv = validate_ci_level(confidence_level)
    r_clamped = max(-0.999999, min(0.999999, float(r)))
    n_eff = float(effective_n) if effective_n is not None else float(n)
    if n_eff <= 3.0:
        return ConfidenceInterval(r_clamped, r_clamped, lv, "fisher_z_small_n", r_clamped, 0.0, "standard")

    z = math.atanh(r_clamped)
    se_z = 1.0 / math.sqrt(max(1.0, n_eff - 3.0))
    crit = float(sp_stats.norm.ppf(0.5 * (1.0 + lv)))
    lo = math.tanh(z - crit * se_z)
    hi = math.tanh(z + crit * se_z)
    se_r = max(0.0, 0.5 * (hi - lo) / max(crit, 1e-9))

    if autocorrelated and not allow_hac:
        label = "naive"
        m_name = "naive_fisher_z (autocorrelation_uncorrected)"
    elif autocorrelated and effective_n is not None:
        label = "hac_robust"
        m_name = f"bartlett_effective_n_fisher_z(n_eff={n_eff:.1f})"
    else:
        label = "standard"
        m_name = "fisher_z_transform"
    return ConfidenceInterval(
        low=lo,
        high=hi,
        confidence_level=lv,
        method=m_name,
        estimate=float(r),
        std_error=se_r,
        inference_label=label,
    )


def bounded_effect_bootstrap_ci(
    values: np.ndarray,
    stat_fn: Any,
    confidence_level: float = 0.95,
    *,
    bounds: tuple[float, float] | None = None,
    n_boot: int = 300,
    seed: int = 42,
    method_tag: str = "nonparametric_percentile_bootstrap",
) -> ConfidenceInterval:
    """Seeded i.i.d. percentile bootstrap CI for non-parametric or bounded effect sizes."""
    lv = validate_ci_level(confidence_level)
    arr = np.asarray(values)
    n = int(arr.shape[0])
    est = float(stat_fn(arr))
    if n < 4:
        return ConfidenceInterval(est, est, lv, f"{method_tag}_small_n", est, 0.0, "nonparametric_exact")
    rng = np.random.default_rng(int(seed))
    reps = np.empty(int(n_boot), dtype=float)
    for b in range(int(n_boot)):
        idx = rng.integers(0, n, size=n)
        reps[b] = float(stat_fn(arr[idx]))
    finite = reps[np.isfinite(reps)]
    if finite.size < 4:
        return ConfidenceInterval(est, est, lv, method_tag, est, 0.0, "nonparametric_exact")
    alpha_tail = 0.5 * (1.0 - lv)
    lo = float(np.quantile(finite, alpha_tail))
    hi = float(np.quantile(finite, 1.0 - alpha_tail))
    if bounds is not None:
        lo = max(bounds[0], min(bounds[1], lo))
        hi = max(bounds[0], min(bounds[1], hi))
    se = float(np.std(finite, ddof=1))
    return ConfidenceInterval(
        low=min(lo, est),
        high=max(hi, est),
        confidence_level=lv,
        method=f"{method_tag}(B={int(n_boot)})",
        estimate=est,
        std_error=se,
        inference_label="nonparametric_exact",
    )
