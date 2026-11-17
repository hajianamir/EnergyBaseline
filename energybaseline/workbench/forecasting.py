"""Holdout-only benchmark forecasts: no target imputation, intervals, tests or auto-refits."""

from __future__ import annotations

import numpy as np

from .common import chronological, fields, integer, number, numeric


BASELINES = ("median", "last", "seasonal_naive", "random_walk_drift", "croston_sba")


def baseline_forecast(train, horizon, *, method, period=1, alpha=0.1):
    values = np.asarray(train, dtype=float)
    integer(horizon, "horizon", 1, 10_000)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError("At least two finite observed training targets are required.")
    if method == "median":
        return np.repeat(np.median(values), horizon)
    if method == "last":
        return np.repeat(values[-1], horizon)
    if method == "seasonal_naive":
        integer(period, "period", 1, len(values))
        return np.resize(values[-period:], horizon)
    if method == "random_walk_drift":
        return values[-1] + np.arange(1, horizon + 1) * (values[-1] - values[0]) / (len(values) - 1)
    if method == "croston_sba":
        number(alpha, "alpha", 0.000001, 1)
        if (values < 0).any():
            raise ValueError("Croston requires nonnegative intermittent demand.")
        nonzero = np.flatnonzero(values > 0)
        if not len(nonzero):
            return np.zeros(horizon)
        first = int(nonzero[0])
        size, interval, previous = values[first], float(first + 1), first
        for pos in nonzero[1:]:
            size = alpha * values[pos] + (1 - alpha) * size
            interval = alpha * (int(pos) - previous) + (1 - alpha) * interval
            previous = int(pos)
        return np.repeat((1 - alpha / 2) * size / interval, horizon)
    raise ValueError("Unknown benchmark forecast method.")


def forecast_holdout(frame, spec):
    fields(spec, ("target", "time", "train_rows", "method"), ("period", "alpha", "lags", "rank"))
    times = chronological(frame, spec["time"])
    if len(times) < 4 or times.diff().dropna().nunique() != 1:
        raise ValueError("Forecasting requires at least four regularly spaced observations.")
    targets = spec["target"] if isinstance(spec["target"], list) else [spec["target"]]
    values = numeric(frame, targets)
    split = integer(spec["train_rows"], "train_rows", 2, len(frame) - 1)
    train, test = values[:split], values[split:]
    if len(test) > 10_000 or len(targets) > 20:
        raise ValueError("Forecast resource bounds: at most 10000 holdout steps and 20 targets.")
    method = spec["method"]
    if method in BASELINES:
        if len(targets) != 1 or "lags" in spec or "rank" in spec:
            raise ValueError("Univariate benchmark methods cannot accept multivariate/rank/lag options.")
        if "period" in spec and method != "seasonal_naive" or "alpha" in spec and method != "croston_sba":
            raise ValueError("Forecast option does not apply to the selected method.")
        prediction = baseline_forecast(
            train.ravel(), len(test), method=method, period=spec.get("period", 1), alpha=spec.get("alpha", 0.1)
        ).reshape(-1, 1)
    elif method in ("var", "vecm"):
        if len(targets) < 2 or "period" in spec or "alpha" in spec:
            raise ValueError("VAR/VECM require multiple targets and explicit lag/rank semantics.")
        if split > 20_000:
            raise ValueError("VAR/VECM training is bounded to 20000 rows.")
        lags = integer(spec.get("lags", 1), "lags", 1, 24)
        if split < 5 * (values.shape[1] * lags + 1):
            raise ValueError("Insufficient training rows for the fixed multivariate model size.")
        if method == "var":
            if "rank" in spec:
                raise ValueError("Rank applies only to VECM.")
            from statsmodels.tsa.api import VAR

            model = VAR(train).fit(maxlags=lags, ic=None, trend="c")
            prediction = model.forecast(train[-lags:], len(test))
        else:
            from statsmodels.tsa.vector_ar.vecm import VECM

            rank = integer(spec.get("rank"), "rank", 1, values.shape[1] - 1)
            model = VECM(train, k_ar_diff=lags, coint_rank=rank, deterministic="co").fit()
            prediction = model.predict(steps=len(test))
    else:
        raise ValueError("Unknown forecast method.")
    if not np.isfinite(prediction).all():
        raise ValueError("Forecast produced nonfinite predictions.")
    error = prediction - test
    return {
        "method": method,
        "train_rows": split,
        "test_rows": len(test),
        "target_count": len(targets),
        "rmse": np.sqrt(np.mean(error**2, axis=0)).tolist(),
        "mae": np.mean(np.abs(error), axis=0).tolist(),
        "forecast_origin": "Last training row; all holdout predictions are multi-step with no holdout updates.",
        "baseline_changed": False,
        "p_values_or_intervals_computed": False,
    }
