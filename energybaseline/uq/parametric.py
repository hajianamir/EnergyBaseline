"""OLS/HAC prediction intervals and independent quantile regression diagnostics."""

from __future__ import annotations

import numpy as np
import statsmodels.api as sm
from scipy.stats import t
from sklearn.linear_model import QuantileRegressor
from .calibration import confidence, vector


def ols_hac_intervals(X_train, y_train, X_reporting, *, level=0.95, maxlags=None):
    level = confidence(level)
    X, Xr = np.asarray(X_train, dtype=float), np.asarray(X_reporting, dtype=float)
    y = vector(y_train)
    z, zr = sm.add_constant(X, has_constant="add"), sm.add_constant(Xr, has_constant="add")
    if len(y) <= z.shape[1] or np.linalg.matrix_rank(z) != z.shape[1]:
        raise ValueError("OLS requires full rank and positive residual degrees of freedom")
    lag = int(4 * (len(y) / 100) ** (2 / 9)) if maxlags is None else int(maxlags)
    fit = sm.OLS(y, z).fit(cov_type="HAC", cov_kwds={"maxlags": lag})
    pred = np.asarray(fit.predict(zr))
    variance = np.einsum("ij,jk,ik->i", zr, fit.cov_params(), zr) + float(fit.scale)
    half = t.ppf((1 + level) / 2, fit.df_resid) * np.sqrt(np.maximum(variance, 0))
    return {"prediction": pred, "lower": pred - half, "upper": pred + half, "covariance": "HAC", "maxlags": lag}


def quantile_intervals(X_train, y_train, X_reporting, *, level=0.95):
    level = confidence(level)
    a = (1 - level) / 2
    lo = QuantileRegressor(quantile=a, alpha=0, solver="highs").fit(X_train, y_train).predict(X_reporting)
    hi = QuantileRegressor(quantile=1 - a, alpha=0, solver="highs").fit(X_train, y_train).predict(X_reporting)
    return np.minimum(lo, hi), np.maximum(lo, hi)


def crosscheck_intervals(lower, upper, conformal_lower, conformal_upper):
    arrays = [vector(v) for v in (lower, upper, conformal_lower, conformal_upper)]
    if len({v.shape for v in arrays}) != 1 or np.any(arrays[0] > arrays[1]) or np.any(arrays[2] > arrays[3]):
        raise ValueError("Aligned ordered intervals required")
    lo, hi, cl, ch = arrays
    return {
        "mean_width_ratio": float(np.mean(hi - lo) / max(float(np.mean(ch - cl)), 1e-12)),
        "disjoint_fraction": float(np.mean((hi < cl) | (lo > ch))),
        "diagnostic_only": True,
    }
