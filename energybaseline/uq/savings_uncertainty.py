"""ASHRAE/IPMVP-style aggregate savings uncertainty using residual autocovariance.

Includes estimated mean-bias uncertainty and measurement/static-factor standard
uncertainty. This is an estimated covariance propagation, not an IPMVP certification.
"""

from __future__ import annotations
import numpy as np
from scipy.stats import t
from .calibration import confidence, vector


def savings_uncertainty(
    savings, calibration_residuals, *, level=0.95, maxlags=None, measurement_sd=0.0, static_factor_sd=0.0
):
    s, r = vector(savings), vector(calibration_residuals, minimum=5)
    level = confidence(level)
    if measurement_sd < 0 or static_factor_sd < 0:
        raise ValueError("Standard uncertainties cannot be negative")
    n, m = len(s), len(r)
    lag = min(m - 1, n - 1, max(1, int(m ** (1 / 3))) if maxlags is None else int(maxlags))
    if lag < 0:
        raise ValueError("Negative lag")
    centered = r - r.mean()
    gamma0 = float(centered @ centered / m)
    long_run, aggregate = gamma0, n * gamma0
    for k in range(1, lag + 1):
        g = float(centered[k:] @ centered[:-k] / m)
        weight = 1 - k / (lag + 1)  # Bartlett taper preserves non-negative HAC estimates.
        long_run += 2 * weight * g
        aggregate += 2 * (n - k) * weight * g
    variance = max(0.0, aggregate) + n * n * max(0.0, long_run) / m
    variance += n * float(measurement_sd) ** 2 + n * n * float(static_factor_sd) ** 2
    half = float(t.ppf((1 + level) / 2, m - 1) * np.sqrt(variance))
    total = float(s.sum())
    return {
        "confidence": level,
        "savings": total,
        "standard_uncertainty": float(np.sqrt(variance)),
        "half_width": half,
        "lower": total - half,
        "upper": total + half,
        "maxlags": lag,
        "method": "Bartlett autocovariance + calibration mean + measurement + static factor",
    }
