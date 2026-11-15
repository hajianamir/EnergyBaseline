"""Strict, bounded inputs for the offline exploratory workbench."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

import numpy as np
import pandas as pd


def fields(spec, required=(), optional=()):
    if not isinstance(spec, Mapping) or set(spec) - set(required) - set(optional) or set(required) - set(spec):
        raise ValueError("Missing or unsupported workbench specification fields.")


def integer(value, name, low=1, high=1_000_000):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}].")
    return value


def number(value, name, low=-np.inf, high=np.inf):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not np.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"{name} must be a finite number in the declared bounds.")
    return float(value)


def real_array(values, name):
    if not isinstance(values, list):
        raise ValueError(f"{name} must be a JSON numeric array.")

    def check(items):
        for value in items:
            if isinstance(value, list):
                check(value)
            elif (
                type(value) not in (int, float)
                or not np.isfinite(value)
                or (isinstance(value, int) and abs(value) > 2**53)
            ):
                raise ValueError(f"{name} requires finite real numbers, without implicit text or boolean conversion.")

    check(values)
    return np.asarray(values, dtype=float)


def columns(frame, names):
    if not isinstance(names, list) or not names or any(not isinstance(c, str) for c in names):
        raise ValueError("Columns must be a nonempty list of names.")
    if len(set(names)) != len(names) or any(c not in frame.columns for c in names):
        raise ValueError("Columns must exist and be unique.")
    return frame.loc[:, names]


def numeric(frame, names, *, missing=False):
    selected = columns(frame, names)
    if any(
        not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_complex_dtype(s) or pd.api.types.is_bool_dtype(s)
        for _, s in selected.items()
    ):
        raise ValueError("Explicit real numeric dtypes are required; implicit text conversion is forbidden.")
    if selected.size > 2_000_000:
        raise ValueError("Numeric operation exceeds two million selected cells.")
    # Predictive/optimization workbench uses float64, unlike exact quality checks.
    if any(
        any(abs(int(v)) > 2**53 for v in s.dropna()) for _, s in selected.items() if pd.api.types.is_integer_dtype(s)
    ):
        raise ValueError("Integers above 2**53 require explicit rescaling before float-based analytics.")
    result = selected.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(result).any() or (not missing and np.isnan(result).any()):
        raise ValueError("Nonfinite numeric values are not allowed for this operation.")
    return result


def binary(frame, name):
    selected = columns(frame, [name]).iloc[:, 0]
    if selected.isna().any() or not selected.isin([0, 1]).all():
        raise ValueError("Binary inputs must contain only explicit 0/1 values and no missing values.")
    return selected.to_numpy(dtype=int)


def chronological(frame, name):
    column = columns(frame, [name]).iloc[:, 0]
    if pd.api.types.is_numeric_dtype(column):
        raise ValueError("Time must be explicit timestamps, not an inferred numeric epoch unit.")
    time = pd.to_datetime(column, utc=True, errors="coerce")
    if time.isna().any() or time.duplicated().any() or not time.is_monotonic_increasing:
        raise ValueError("Time must be valid, unique and strictly increasing; rows are never silently sorted.")
    return time


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def finite_json(value):
    """Reject nonstandard JSON (including a solver/library accidentally returning NaN)."""
    json.dumps(value, allow_nan=False)
    return value
