"""Shared explicit design contracts for preregistered statistics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..workbench.common import columns, fields, integer, number, numeric, real_array


ALTERNATIVES = ("two-sided", "greater", "less")


def alternative(parameters):
    value = parameters.get("alternative", "two-sided")
    if value not in ALTERNATIVES:
        raise ValueError("Unsupported directional hypothesis.")
    return value


def vector(frame, names, minimum=2):
    values = numeric(frame, names)
    if len(values) < minimum:
        raise ValueError("Insufficient complete observations for the declared method.")
    return values


def nonconstant(values):
    if np.any(np.ptp(np.asarray(values), axis=0) == 0):
        raise ValueError("The declared procedure is undefined for constant samples.")


def interval(low, high, level, *, kind="pointwise", interpretation="frequentist"):
    if np.isnan(low) or np.isnan(high) or low > high:
        raise ValueError("Invalid interval from numerical execution.")
    return {
        "lower": float(low) if np.isfinite(low) else None,
        "upper": float(high) if np.isfinite(high) else None,
        "lower_unbounded": bool(np.isneginf(low)),
        "upper_unbounded": bool(np.isposinf(high)),
        "level": level,
        "kind": kind,
        "interpretation": interpretation,
    }


def result(statistic, p_value=None, effect=None, *, suffix=None, **details):
    if (
        statistic is not None
        and not np.isfinite(statistic)
        or p_value is not None
        and (not np.isfinite(p_value) or not 0 <= p_value <= 1)
    ):
        raise ValueError("Undefined test statistic or probability.")
    if effect is not None and not np.isfinite(effect):
        raise ValueError("Undefined effect estimate.")
    return {
        "statistic": float(statistic) if statistic is not None else None,
        "p_input": float(p_value) if p_value is not None else None,
        "effect": float(effect) if effect is not None else None,
        "component": suffix,
        **details,
    }


def grouped(frame, names, minimum=2):
    if len(names) != 2:
        raise ValueError("A response and grouping column are required.")
    y = vector(frame, [names[0]], minimum).ravel()
    groups = columns(frame, [names[1]]).iloc[:, 0]
    if groups.isna().any():
        raise ValueError("Missing group assignments are not permitted.")
    codes, labels = pd.factorize(groups, sort=False)
    if not 2 <= len(labels) <= 10:
        raise ValueError("Two to ten observed groups are required.")
    samples = [y[codes == i] for i in range(len(labels))]
    if min(map(len, samples)) < minimum:
        raise ValueError("Insufficient observations in a declared group.")
    return y, codes, samples


__all__ = [
    "alternative",
    "columns",
    "fields",
    "grouped",
    "integer",
    "interval",
    "nonconstant",
    "number",
    "numeric",
    "real_array",
    "result",
    "vector",
]
