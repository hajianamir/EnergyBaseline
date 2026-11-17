"""Descriptive risk, group fairness and categorical divergence; no hypothesis tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import rel_entr

from .common import binary, columns, fields, integer, number, numeric, real_array


def portfolio_risk(frame, spec):
    fields(spec, ("returns", "weights", "confidence"))
    values = numeric(frame, spec["returns"])
    weights = real_array(spec["weights"], "weights")
    if weights.shape != (values.shape[1],) or not np.isfinite(weights).all() or not np.isclose(weights.sum(), 1):
        raise ValueError("Portfolio weights must be finite, match the return columns and sum to one.")
    if len(values) < 2 or (values < -1).any():
        raise ValueError("At least two rows of simple returns, each >= -1, are required.")
    confidence = number(spec["confidence"], "confidence", 0.5, 0.9999)
    returns = values @ weights
    if (returns <= -1).any():
        raise ValueError("Portfolio total loss/bankruptcy precludes this compounded-return contract.")
    losses = -returns
    # Integrate the upper (1-confidence) tail of the empirical quantile function.
    # Fractional mass at its boundary is essential, including when n*tail < 1.
    ordered = np.sort(losses)[::-1]
    mass = len(losses) * (1 - confidence)
    full = int(np.floor(mass))
    expected_shortfall = (ordered[:full].sum() + (mass - full) * ordered[full]) / mass
    wealth = np.r_[1.0, np.cumprod(1 + returns)]
    if not np.isfinite(wealth).all():
        raise ValueError("Compounded wealth overflow; rescale the scenario.")
    drawdown = wealth / np.maximum.accumulate(wealth) - 1
    covariance = np.atleast_2d(np.cov(values, rowvar=False, ddof=1))
    return {
        "rows": len(values),
        "confidence": confidence,
        "loss_definition": "negative simple portfolio return",
        "var": float(np.quantile(losses, confidence, method="inverted_cdf")),
        "expected_shortfall": float(expected_shortfall),
        "maximum_drawdown": float(-drawdown.min()),
        "mean_return": float(returns.mean()),
        "sample_volatility": float(returns.std(ddof=1)),
        "covariance": covariance.tolist(),
        "terminal_wealth_per_unit": float(wealth[-1]),
        "annualized": False,
        "guarantee": "Empirical description, not a future-loss bound.",
    }


def group_fairness(frame, spec):
    fields(spec, ("group", "outcome", "prediction"), ("minimum_group_size",))
    group = columns(frame, [spec["group"]]).iloc[:, 0]
    if group.isna().any():
        raise ValueError("Sensitive groups cannot be missing.")
    y, pred = binary(frame, spec["outcome"]), binary(frame, spec["prediction"])
    minimum = integer(spec.get("minimum_group_size", 20), "minimum_group_size", 2)
    codes, labels = pd.factorize(group, sort=False)
    rows = []
    for code in range(len(labels)):
        mask = codes == code
        n = int(mask.sum())
        row = {"group_position": code, "n": n, "status": "suppressed" if n < minimum else "assessed"}
        if n >= minimum:
            positives, negatives = int((y[mask] == 1).sum()), int((y[mask] == 0).sum())
            row.update(
                selection_rate=float(pred[mask].mean()),
                true_positive_rate=float(pred[mask & (y == 1)].mean()) if positives else None,
                false_positive_rate=float(pred[mask & (y == 0)].mean()) if negatives else None,
                accuracy=float((pred[mask] == y[mask]).mean()),
            )
        rows.append(row)
    eligible = [r for r in rows if r["status"] == "assessed"]

    def gap(key):
        rates = [r[key] for r in eligible]
        return float(max(rates) - min(rates)) if len(rates) >= 2 and None not in rates else None

    rates = [r["selection_rate"] for r in eligible]
    tpr_gap, fpr_gap = gap("true_positive_rate"), gap("false_positive_rate")
    return {
        "groups": rows,
        "group_labels_exported": False,
        "scope": "Eligible groups only; suppressed groups preclude an all-population conclusion.",
        "demographic_parity_difference": gap("selection_rate"),
        "disparate_impact_ratio": float(min(rates) / max(rates)) if len(rates) >= 2 and max(rates) > 0 else None,
        "equalized_odds_difference": max(tpr_gap, fpr_gap) if tpr_gap is not None and fpr_gap is not None else None,
        "mitigation_applied": False,
        "causal_or_legal_fairness_claim": False,
    }


def categorical_divergence(frame, reference, spec):
    fields(spec, ("column",), ("pseudocount",))
    current = columns(frame, [spec["column"]]).iloc[:, 0]
    prior = columns(reference, [spec["column"]]).iloc[:, 0]
    if current.isna().any() or prior.isna().any() or not len(current) or not len(prior):
        raise ValueError("Divergence requires two nonempty categorical columns with no missing values.")
    # Factorize together rather than stringify: integer 1 and text '1' are distinct.
    codes, categories = pd.factorize(pd.concat([prior, current], ignore_index=True), sort=False)
    if len(categories) > 100_000:
        raise ValueError("Category resource limit exceeded.")
    smoothing = number(spec.get("pseudocount", 0.0), "pseudocount", 0)
    p = np.bincount(codes[: len(prior)], minlength=len(categories)).astype(float) + smoothing
    q = np.bincount(codes[len(prior) :], minlength=len(categories)).astype(float) + smoothing
    p, q = p / p.sum(), q / q.sum()
    middle = (p + q) / 2
    kl = float(rel_entr(q, p).sum())
    return {
        "reference_rows": len(prior),
        "current_rows": len(current),
        "category_count": len(categories),
        "pseudocount": smoothing,
        "log_base": "e",
        "kl_direction": "current || reference",
        "kl_divergence": kl if np.isfinite(kl) else None,
        "kl_is_infinite": not np.isfinite(kl),
        "jensen_shannon_divergence": float((rel_entr(p, middle).sum() + rel_entr(q, middle).sum()) / 2),
        "action": "Review and revalidate if appropriate; never mutate or refit a baseline.",
    }
