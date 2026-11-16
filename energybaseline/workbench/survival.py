"""Predictive Cox/Breslow and Weibull AFT with right censoring and delayed entry.

Optimizes likelihoods only: no significance tests, covariance estimates or intervals.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from statsmodels.duration.hazard_regression import PHReg

from .common import binary, chronological, digest, fields, integer, numeric, real_array


def weibull_objective(parameters, x, duration, event, entry):
    """Conditional negative log likelihood and analytic gradient, including left truncation."""
    beta, log_shape = parameters[:-1], parameters[-1]
    shape = np.exp(log_shape)
    log_scale = x @ beta
    delta = np.log(duration) - log_scale
    if np.max(shape * delta) > 650:
        return float("inf"), np.zeros_like(parameters)
    power = np.exp(shape * delta)
    entry_delta = np.zeros_like(entry)
    positive = entry > 0
    entry_delta[positive] = np.log(entry[positive]) - log_scale[positive]
    entry_power = np.zeros_like(entry)
    entry_power[positive] = np.exp(shape * entry_delta[positive])
    value = np.sum(power - entry_power - event * (log_shape - log_scale + (shape - 1) * delta))
    beta_gradient = x.T @ (shape * (event - power + entry_power))
    shape_gradient = np.sum(shape * delta * power - shape * entry_delta * entry_power - event * (1 + shape * delta))
    return float(value), np.r_[beta_gradient, shape_gradient]


def concordance(duration, event, risk, entry):
    """Harrell comparable pairs; neither IPCW adjustment nor a calibrated probability."""
    pairs, credit = 0, 0.0
    for i in np.flatnonzero(event):
        # Strict event times: tied event/censoring times are not ordered pairs.
        eligible = (duration > duration[i]) & (entry <= duration[i])
        differences = risk[i] - risk[eligible]
        pairs += len(differences)
        credit += np.sum(differences > 0) + 0.5 * np.sum(differences == 0)
    return (float(credit / pairs) if pairs else None), int(pairs)


def survival_regression(frame, spec):
    fields(spec, ("duration", "event", "features", "time", "train_rows", "method", "horizons"), ("entry", "gap"))
    chronological(frame, spec["time"])
    roles = [spec["duration"], spec["event"], spec["time"]] + ([spec["entry"]] if "entry" in spec else [])
    if len(set(roles)) != len(roles) or not isinstance(spec["features"], list) or set(roles) & set(spec["features"]):
        raise ValueError("Durations, censoring, entry and time must be distinct from predictors and each other.")
    if spec["method"] not in ("cox_breslow", "weibull_aft"):
        raise ValueError("Unknown predictive survival method.")
    if len(frame) > 2000:
        raise ValueError("Survival likelihood and concordance are bounded to 2000 rows.")
    x = numeric(frame, spec["features"])
    duration = numeric(frame, [spec["duration"]]).ravel()
    event = binary(frame, spec["event"])
    entry = numeric(frame, [spec["entry"]]).ravel() if "entry" in spec else np.zeros(len(frame))
    if (duration <= 0).any() or (duration > 1e9).any() or (entry < 0).any() or (entry >= duration).any():
        raise ValueError("Require 0 < duration <= 1e9 and 0 <= entry < duration.")
    horizons = real_array(spec["horizons"], "horizons")
    if horizons.ndim != 1 or not 1 <= len(horizons) <= 30 or (horizons <= 0).any() or (np.diff(horizons) <= 0).any():
        raise ValueError("Horizons must be positive strictly increasing times, at most 30.")
    split = integer(spec["train_rows"], "train_rows", 40, len(frame) - 1)
    gap = integer(spec.get("gap", 1), "gap", 1, split - 30)
    end = split - gap
    if x.shape[1] > min(10, end // 20) or event[:end].sum() < max(10, 5 * x.shape[1]):
        raise ValueError("Survival regression needs sufficient training observations/events per feature.")
    if np.any(horizons > duration[:end].max()):
        raise ValueError("Predictions beyond maximum training follow-up are not supported.")
    if np.any(horizons[0] < entry[split:]):
        raise ValueError("Every prediction horizon must be at or after each test subject's entry time.")
    mean, scale = x[:end].mean(axis=0), x[:end].std(axis=0)
    if (scale <= 1e-12).any():
        raise ValueError("Constant predictors are not identifiable in survival regression.")
    x = (x - mean) / scale
    if np.linalg.matrix_rank(np.c_[np.ones(end), x[:end]]) < x.shape[1] + 1 or np.abs(x).max() > 100:
        raise ValueError("Collinear or extreme standardized predictors are unsupported.")
    t, e, a = duration[:end], event[:end], entry[:end]
    if spec["method"] == "cox_breslow":
        model = PHReg(t, x[:end], status=e, entry=a, ties="breslow")

        # PHReg.fit would compute covariance/inference; use only its likelihood and analytic score.
        def objective(beta):
            return -model.loglike(beta), -model.score(beta)

        initial = np.zeros(x.shape[1])
        bounds = [(-10, 10)] * len(initial)
    else:
        design = np.c_[np.ones(end), x[:end]]

        def objective(beta):
            return weibull_objective(beta, design, t, e, a)

        initial = np.r_[np.log(np.median(t)), np.zeros(x.shape[1]), 0.0]
        bounds = [(-20, 25)] + [(-5, 5)] * x.shape[1] + [(-2, 2)]
    fit = minimize(
        objective,
        initial,
        jac=True,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-6},
    )
    if (
        not fit.success
        or not np.isfinite(fit.fun)
        or not np.isfinite(fit.x).all()
        or any(abs(v - lo) < 1e-5 or abs(v - hi) < 1e-5 for v, (lo, hi) in zip(fit.x, bounds))
    ):
        raise ValueError("Survival likelihood did not converge to an interior finite solution.")
    if spec["method"] == "cox_breslow":
        risk = x[split:] @ fit.x
        if np.abs(risk).max() > 600:
            raise ValueError("Test risk exceeds finite survival prediction capacity.")
        event_times = np.unique(t[e == 1])
        increments = np.array(
            [
                np.sum((t == time) & (e == 1)) / np.exp(x[:end][(a <= time) & (t >= time)] @ fit.x).sum()
                for time in event_times
            ]
        )
        cumulative = np.r_[0.0, increments.cumsum()]
        h = cumulative[np.searchsorted(event_times, horizons, side="right")]
        h_entry = cumulative[np.searchsorted(event_times, entry[split:], side="right")]
        survival = np.exp(-np.exp(risk[:, None]) * np.maximum(0, h[None, :] - h_entry[:, None]))
        baseline_hash = digest({"event_times": event_times.tolist(), "increments": increments.tolist()})
        shape = None
    else:
        log_scale = np.c_[np.ones(len(frame) - split), x[split:]] @ fit.x[:-1]
        shape = float(np.exp(fit.x[-1]))
        risk = -log_scale
        log_hazard = shape * (np.log(horizons)[None, :] - log_scale[:, None])
        if log_hazard.max() > 650:
            raise ValueError("Test hazard exceeds finite survival prediction capacity.")
        hazard = np.exp(log_hazard)
        entry_hazard = np.zeros(len(log_scale))
        positive = entry[split:] > 0
        entry_hazard[positive] = np.exp(shape * (np.log(entry[split:][positive]) - log_scale[positive]))
        survival = np.exp(-np.maximum(0, hazard - entry_hazard[:, None]))
        baseline_hash = None
    score, pairs = concordance(duration[split:], event[split:], risk, entry[split:])
    from ..core.ingest import sha256_dataframe

    return {
        "method": spec["method"],
        "train_rows": end,
        "purged_rows": gap,
        "test_rows": len(frame) - split,
        "training_events": int(e.sum()),
        "test_events": int(event[split:].sum()),
        "delayed_entry": "entry" in spec,
        "training_sha256": sha256_dataframe(frame.iloc[:end]),
        "parameters_sha256": digest(fit.x.tolist()),
        "baseline_hazard_sha256": baseline_hash,
        "weibull_shape": shape,
        "optimizer_converged": True,
        "optimizer_iterations": int(fit.nit),
        "training_negative_log_likelihood": float(fit.fun),
        "holdout_harrell_concordance": score,
        "holdout_comparable_pairs": pairs,
        "horizons": horizons.tolist(),
        "mean_test_conditional_survival": survival.mean(axis=0).tolist(),
        "predictions_sha256": digest(survival.tolist()),
        "prediction_semantics": "Survival conditional on survival to entry; not IPCW-calibrated or causal.",
        "assumptions": "Independent noninformative right censoring/left truncation; Cox proportional hazards or Weibull AFT specification. No competing risks.",
        "inference": "Likelihood optimization only; no covariance estimates, hypothesis tests or confidence intervals.",
        "p_values_or_confidence_intervals_computed": False,
        "baseline_changed": False,
        "model_persisted": False,
    }
