"""MAPIE adapters with explicit exchangeable versus sequential calibration contracts.

For autocorrelated series, coverage is empirical, not a distribution-free guarantee.
Sequential updates change calibration scores only, never the locked estimator.
"""

from __future__ import annotations

import numpy as np
from sklearn.base import clone
from sklearn.linear_model import LinearRegression
from mapie.regression import (
    SplitConformalRegressor,
    CrossConformalRegressor,
    JackknifeAfterBootstrapRegressor,
    ConformalizedQuantileRegressor,
    TimeSeriesRegressor,
)
from mapie.subsample import BlockBootstrap

from .calibration import confidence, vector


class ConformalIntervals:
    """Owns a cloned estimator; training/calibration/holdout are separate inputs.

    Cross and bootstrap methods are explicitly exchangeable-data diagnostics.
    Time series use block-bootstrap EnbPI or ACI. The caller must supply a
    chronological training prefix; holdout outcomes are used only after prediction.
    """

    def __init__(self, estimator=None, *, method="enbpi", levels=(0.8, 0.95), seed=42):
        self.estimator = LinearRegression() if estimator is None else estimator
        self.method = method
        self.levels = tuple(confidence(v) for v in levels)
        if not self.levels or len(set(self.levels)) != len(self.levels):
            raise ValueError("Confidence levels must be nonempty and unique")
        self.seed = seed

    def fit(self, X_train, y_train, X_cal=None, y_cal=None):
        X = np.asarray(X_train, dtype=float)
        y = vector(y_train, minimum=30)
        if X.ndim != 2 or len(X) != len(y) or not np.isfinite(X).all():
            raise ValueError("Invalid training matrix")
        estimator = clone(self.estimator)
        if self.method in ("enbpi", "aci"):
            cv = BlockBootstrap(
                n_resamplings=40, length=max(2, int(len(y) ** (1 / 3))), overlapping=True, random_state=self.seed
            )
            self.backend_ = TimeSeriesRegressor(
                estimator=estimator, method=self.method, cv=cv, agg_function="mean", random_state=self.seed
            ).fit(X, y)
        elif self.method == "cross":
            self.backend_ = CrossConformalRegressor(
                estimator, confidence_level=self.levels, cv=5, random_state=self.seed
            ).fit_conformalize(X, y)
        elif self.method == "jackknife":
            self.backend_ = JackknifeAfterBootstrapRegressor(
                estimator, confidence_level=self.levels, resampling=40, random_state=self.seed
            ).fit_conformalize(X, y)
        elif self.method in ("split", "quantile"):
            if X_cal is None or y_cal is None:
                raise ValueError("Separate calibration data required")
            if self.method == "split":
                self.backend_ = (
                    SplitConformalRegressor(estimator, confidence_level=self.levels, prefit=False)
                    .fit(X, y)
                    .conformalize(X_cal, y_cal)
                )
            else:
                if len(self.levels) != 1:
                    raise ValueError("MAPIE quantile backend requires one level per adapter")
                self.backend_ = (
                    ConformalizedQuantileRegressor(confidence_level=self.levels[0]).fit(X, y).conformalize(X_cal, y_cal)
                )
        else:
            raise ValueError("Unknown conformal method")
        return self

    def predict(self, X):
        if self.method in ("enbpi", "aci"):
            pred, bounds = self.backend_.predict(X, confidence_level=self.levels, ensemble=True)
        else:
            pred, bounds = self.backend_.predict_interval(X)
        if not np.isfinite(bounds).all():
            raise ValueError("Insufficient calibration data for finite intervals")
        return np.asarray(pred), np.asarray(bounds)

    def observe(self, X, y, *, gamma=0.01):
        """Observe already-predicted outcomes; explicit opt-in ACI adaptation rate."""
        if self.method not in ("enbpi", "aci"):
            raise ValueError("Sequential updates require EnbPI or ACI")
        if not 0 <= gamma <= 1:
            raise ValueError("Invalid adaptation rate")
        observed = vector(y)
        if self.method == "aci":
            # MAPIE 1.6 update() changes scores only; adaptation is a separate API.
            self.backend_.adapt_conformal_inference(
                X, observed, gamma=gamma, confidence_level=self.levels, ensemble=True
            )
        self.backend_.update(X, observed, ensemble=True)
        return self
