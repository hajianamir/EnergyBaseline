"""Holdout interval diagnostics; calibration never consumes future observations."""

from __future__ import annotations

import numpy as np
from scipy.special import xlogy
from scipy.stats import chi2


def vector(values, *, minimum=1):
    a = np.asarray(values, dtype=float)
    if a.ndim != 1 or len(a) < minimum or not np.isfinite(a).all():
        raise ValueError("Expected a finite nonempty one-dimensional array")
    return a


def confidence(level):
    level = float(level)
    if not 0 < level < 1:
        raise ValueError("Confidence must be strictly between zero and one")
    return level


def interval_diagnostics(y, lower, upper, nominal=0.95, tolerance=0.05):
    """Kupiec unconditional coverage and Christoffersen independence LR tests.

    Degenerate transition rows contribute zero log likelihood. Tests are diagnostics,
    not a certification of exchangeability or validity for dependent time series.
    """
    nominal = confidence(nominal)
    y, lo, hi = vector(y, minimum=3), vector(lower), vector(upper)
    if not (y.shape == lo.shape == hi.shape) or np.any(lo > hi):
        raise ValueError("Intervals must be aligned and ordered")
    miss = ((y < lo) | (y > hi)).astype(int)
    n, k = len(y), int(miss.sum())
    p = k / n

    def ll(a, b, prob):
        return float(xlogy(a, prob) + xlogy(b, 1 - prob))

    lr_uc = max(0.0, 2 * (ll(k, n - k, p) - ll(k, n - k, 1 - nominal)))
    counts = np.zeros((2, 2), dtype=int)
    np.add.at(counts, (miss[:-1], miss[1:]), 1)
    n00, n01, n10, n11 = counts.ravel()
    p01 = n01 / max(n00 + n01, 1)
    p11 = n11 / max(n10 + n11, 1)
    pooled = (n01 + n11) / (n - 1)
    lr_ind = max(0.0, 2 * (ll(n01, n00, p01) + ll(n11, n10, p11) - ll(n01 + n11, n00 + n10, pooled)))
    coverage = 1 - p
    return {
        "n": n,
        "nominal": nominal,
        "coverage": coverage,
        "coverage_error_pp": 100 * (coverage - nominal),
        "within_tolerance": bool(abs(coverage - nominal) <= tolerance + 1e-12),
        "mean_width": float(np.mean(hi - lo)),
        "kupiec_p": float(chi2.sf(lr_uc, 1)),
        "christoffersen_independence_p": float(chi2.sf(lr_ind, 1)),
        "christoffersen_conditional_p": float(chi2.sf(lr_uc + lr_ind, 2)),
        "transition_counts": counts.tolist(),
    }
