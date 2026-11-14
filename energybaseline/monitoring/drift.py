"""Read-only drift diagnostics and ISO 50006 engineering advisories."""

from __future__ import annotations
import numpy as np
from scipy import stats
from ..uq.calibration import vector, interval_diagnostics


def covariate_drift(reference, current, *, bins=10, psi_threshold=0.2, alpha=0.01):
    r, c = vector(reference, minimum=20), vector(current, minimum=20)
    edges = np.unique(np.quantile(r, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        edges = np.array([r[0] - 0.5, r[0] + 0.5])
    edges[0], edges[-1] = -np.inf, np.inf
    a, b = np.histogram(r, edges)[0] + 0.5, np.histogram(c, edges)[0] + 0.5
    a, b = a / a.sum(), b / b.sum()
    psi = float(np.sum((b - a) * np.log(b / a)))
    ks = stats.ks_2samp(r, c)
    # A material KS statistic prevents large-sample negligible shifts firing alone.
    return {
        "psi": psi,
        "ks_statistic": float(ks.statistic),
        "ks_p": float(ks.pvalue),
        "wasserstein": float(stats.wasserstein_distance(r, c)),
        "triggered": bool(psi > psi_threshold or (ks.pvalue < alpha and ks.statistic > 0.1)),
    }


def residual_regime(reference, current, *, window=60, threshold=5.0):
    """Two-sided Page-Hinkley and decision-interval CUSUM against frozen scale.

    Pettitt tests a change point in the concatenated reference/current window;
    variance ratio catches volatility changes even when the mean is unchanged.
    """
    r, c = vector(reference, minimum=20), vector(current, minimum=20)
    r, c = r[-window:], c[-window:]
    scale = max(float(np.std(r, ddof=1)), 1e-9)
    z = (c - r.mean()) / scale
    plus = minus = 0.0
    ph_plus = ph_minus = 0.0
    cusum_alarm = ph_alarm = False
    for v in z:
        plus = max(0.0, plus + v - 0.5)
        minus = min(0.0, minus + v + 0.5)
        cusum_alarm |= plus > threshold or minus < -threshold
        ph_plus = max(0.0, ph_plus + v - 0.05)
        ph_minus = min(0.0, ph_minus + v + 0.05)
        ph_alarm |= ph_plus > 2 * threshold or ph_minus < -2 * threshold
    joined = np.r_[r, c]
    ranks = stats.rankdata(joined)
    u = 2 * np.cumsum(ranks) - np.arange(1, len(joined) + 1) * (len(joined) + 1)
    k = float(np.max(np.abs(u)))
    p = min(1.0, float(2 * np.exp(-6 * k * k / (len(joined) ** 3 + len(joined) ** 2))))
    variance_ratio = float(np.var(c, ddof=1) / scale**2)
    return {
        "mean_shift_sigma": float(z.mean()),
        "variance_ratio": variance_ratio,
        "page_hinkley": bool(ph_alarm),
        "cusum": bool(cusum_alarm),
        "pettitt_p": p,
        "triggered": bool(
            ((cusum_alarm or ph_alarm) and abs(float(z.mean())) >= 0.5)
            or p < 0.01
            or variance_ratio > 2.5
            or variance_ratio < 0.4
        ),
    }


def miscoverage_monitor(y, lower, upper, *, nominal=0.95, window=100):
    y, lo, hi = vector(y), vector(lower), vector(upper)
    if not (y.shape == lo.shape == hi.shape):
        raise ValueError("Coverage input mismatch")
    d = interval_diagnostics(y[-window:], lo[-window:], hi[-window:], nominal)
    d["degraded"] = bool(d["coverage"] < nominal - 0.05)
    return d


def run_drift_monitor(
    reference_drivers,
    current_drivers,
    reference_residuals,
    residuals,
    *,
    tcp,
    domain_flags=None,
    y=None,
    lower=None,
    upper=None,
    nominal=0.95,
):
    """Inference requires the real, accepted VSG/TCP configuration (PIPE-1)."""
    tcp.assert_accepted()
    if set(reference_drivers) != set(current_drivers):
        raise ValueError("Driver columns must match")
    cov = {k: covariate_drift(reference_drivers[k], current_drivers[k]) for k in reference_drivers}
    regime = residual_regime(reference_residuals, residuals)
    residuals = vector(residuals, minimum=20)
    flags = np.zeros(len(residuals), dtype=bool) if domain_flags is None else np.asarray(domain_flags, dtype=bool)
    if flags.shape != residuals.shape:
        raise ValueError("Domain flags must align")
    coverage = None if y is None else miscoverage_monitor(y, lower, upper, nominal=nominal)
    cov_shift = any(v["triggered"] for v in cov.values())
    shifted = regime["triggered"]
    sustained_negative = np.mean(residuals[-20:] < 0) >= 0.8 and regime["mean_shift_sigma"] < -0.5
    improvement = bool(shifted and sustained_negative and not flags.any() and not cov_shift)
    interpretation = (
        "possible efficiency improvement; engineering confirmation required"
        if improvement
        else "possible model breakdown / non-routine event"
        if shifted or flags.any()
        else "covariate shift only; inspect seasonal loading"
        if cov_shift
        else "stable"
    )
    advisory = bool(shifted or flags.any() or (coverage is not None and coverage["degraded"]))
    return {
        "covariates": cov,
        "residual_regime": regime,
        "miscoverage": coverage,
        "interpretation": interpretation,
        "possible_efficiency_improvement": improvement,
        "advisory": {
            "standard": "ISO 50006",
            "rebaseline_review": advisory,
            "action": "engineering review and re-validation; retain locked baseline"
            if advisory
            else "continue monitoring; inspect seasonal loading"
            if cov_shift
            else "continue monitoring",
        },
        "model_mutated": False,
    }
