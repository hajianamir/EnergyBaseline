"""Read-only six-axis quality evidence; no inference, imputation, or expression evaluation.

Unconfigured dimensions are unassessed, never silently perfect. Accuracy requires
an explicitly supplied reference and timeliness requires an explicit UTC as-of.
Only counts, row positions and input/policy digests leave this module, not values.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from numbers import Integral

import numpy as np
import pandas as pd

from .ingest import sha256_dataframe

DIMENSIONS = ("completeness", "uniqueness", "validity", "consistency", "accuracy", "timeliness")
OPERATORS = {"eq": "eq", "ne": "ne", "lt": "lt", "le": "le", "gt": "gt", "ge": "ge"}


def _columns(frame, names):
    if not isinstance(names, list) or not names or any(not isinstance(c, str) for c in names):
        raise ValueError("Column selections must be nonempty lists of names.")
    if len(set(names)) != len(names) or any(c not in frame.columns for c in names):
        raise ValueError("Column selections must be unique and present in the input.")
    return names


def _keys(spec, allowed, required=()):
    if not isinstance(spec, dict) or set(spec) - set(allowed) or set(required) - set(spec):
        raise ValueError("Quality policy contains unsupported or missing fields.")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("Policy tolerances and numeric limits must be finite numbers.")
    try:
        numeric = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Policy numeric limit is out of range.") from exc
    if not np.isfinite(numeric):
        raise ValueError("Policy tolerances and numeric limits must be finite numbers.")
    return value if isinstance(value, int) else numeric


def _reference(references, name):
    if not isinstance(name, str) or not name or name not in references:
        raise ValueError("A required named reference was not supplied.")
    return references[name]


def _result(checks, *, possible=None):
    assessed = sum(c["assessed"] for c in checks)
    passed = sum(c["passed"] for c in checks)
    possible = assessed if possible is None else possible
    return {
        "status": "not_assessed" if not assessed else ("partial" if assessed < possible else "assessed"),
        "score": passed / assessed if assessed else None,
        "assessed": assessed,
        "passed": passed,
        "failed": assessed - passed,
        "possible": possible,
        "coverage": assessed / possible if possible else None,
        "checks": checks,
    }


def _check(identifier, valid):
    valid = np.asarray(valid, dtype=bool).reshape(-1)
    failed = np.flatnonzero(~valid)
    return {
        "id": identifier,
        "assessed": len(valid),
        "passed": int(valid.sum()),
        "failed_row_positions": failed[:20].tolist(),
        "positions_truncated": len(failed) > 20,
    }


def _joined_reference(frame, reference, keys, key_types):
    _columns(frame, keys)
    _columns(reference, keys)
    left_keys, right_keys = frame[keys].copy(), reference[keys].copy()
    if not isinstance(key_types, dict) or set(key_types) - set(keys):
        raise ValueError("Key normalization must name only declared matching keys.")
    for column, kind in key_types.items():
        if kind != "datetime_utc":
            raise ValueError("Supported key normalization is datetime_utc.")
        for values in (left_keys, right_keys):
            values[column] = pd.to_datetime(values[column], errors="coerce", utc=True, format="mixed")
    if left_keys.isna().any().any() or right_keys.isna().any().any():
        raise ValueError("Reference matching requires nonmissing keys in both inputs.")
    if left_keys.duplicated().any() or right_keys.duplicated().any():
        raise ValueError("Accuracy reference matching requires unique keys in both inputs.")
    # Separate key and value frames avoid suffix collisions with arbitrary input names.
    left = pd.MultiIndex.from_frame(left_keys)
    right = pd.MultiIndex.from_frame(right_keys)
    # Object values preserve exact integers even when unmatched rows introduce NA.
    return reference.astype(object).set_axis(right).reindex(left).reset_index(drop=True)


def audit_quality(
    frame: pd.DataFrame,
    policy: dict | None = None,
    *,
    references: Mapping[str, pd.DataFrame] | None = None,
    as_of: str | None = None,
) -> dict:
    """Evaluate a version-1 declarative policy without changing any input.

    An overall score exists only when ALL six dimensions are fully assessed.
    ``assessed_score`` is explicitly an incomplete, equal-dimension average when
    evidence is missing; accuracy truth coverage is reported independently.
    References are caller-supplied DataFrames; policy documents cannot open files.
    """
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("Quality audit requires a DataFrame input.")
    policy = {} if policy is None else policy
    references = {} if references is None else references
    if not isinstance(references, Mapping):
        raise ValueError("References must be a mapping of names to DataFrames.")
    for name, reference in references.items():
        if not isinstance(name, str) or not name or not isinstance(reference, pd.DataFrame):
            raise ValueError("References require nonempty names and DataFrames.")
        if not reference.columns.is_unique or any(not isinstance(c, str) for c in reference.columns):
            raise ValueError("Reference column names must be unique strings.")
    _keys(policy, ("version", "columns", "unique_by", "validity", "consistency", "accuracy", "timeliness"))
    if type(policy.get("version", 1)) is not int or policy.get("version", 1) != 1:
        raise ValueError("Unsupported quality policy version.")
    if not frame.columns.is_unique or any(not isinstance(c, str) for c in frame.columns):
        raise ValueError("Quality audit requires unique string column names.")
    canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False)
    columns = (
        _columns(frame, policy.get("columns", list(frame.columns))) if len(frame.columns) or "columns" in policy else []
    )
    dimensions = {}
    completeness = [_check("nonmissing_cells", frame[columns].notna().to_numpy().ravel())]
    dimensions["completeness"] = _result(completeness)
    # Completeness positions are flattened cells, not row locations.
    completeness[0]["position_kind"] = "row_major_cell_offset"
    keys = _columns(frame, policy.get("unique_by", columns)) if columns or "unique_by" in policy else []
    unique = (~frame.duplicated(keys) & frame[keys].notna().all(axis=1)) if keys else np.array([], dtype=bool)
    dimensions["uniqueness"] = _result([_check("unique_nonmissing_key", unique)])

    checks = []
    rules = policy.get("validity", [])
    if not isinstance(rules, list):
        raise ValueError("Validity rules must be a list.")
    for i, rule in enumerate(rules):
        _keys(rule, ("kind", "column", "min", "max", "values"), ("kind", "column"))
        column = _columns(frame, [rule["column"]])[0]
        values = frame[column]
        kind = rule["kind"]
        if kind == "not_null":
            _keys(rule, ("kind", "column"))
            valid = values.notna()
        elif kind == "finite":
            _keys(rule, ("kind", "column"))
            valid = np.isfinite(pd.to_numeric(values, errors="coerce").astype(float))
        elif kind == "range":
            _keys(rule, ("kind", "column", "min", "max"))
            if "min" not in rule and "max" not in rule:
                raise ValueError("Range rules require a lower or upper limit.")
            low, high = (
                _number(rule.get("min", -np.finfo(float).max)),
                _number(rule.get("max", np.finfo(float).max)),
            )
            if low > high:
                raise ValueError("Range minimum cannot exceed maximum.")
            numeric = pd.to_numeric(values, errors="coerce")
            valid = np.isfinite(numeric.to_numpy(dtype=float, na_value=np.nan)) & numeric.astype(object).between(
                low, high
            )
        elif kind == "allowed":
            _keys(rule, ("kind", "column", "values"), ("values",))
            if not isinstance(rule["values"], list) or not rule["values"]:
                raise ValueError("Allowed-values rules require a nonempty list.")
            valid = values.notna() & values.isin(rule["values"])
        else:
            raise ValueError("Unsupported validity rule kind.")
        checks.append(_check(f"validity_{i}", valid))
    dimensions["validity"] = _result(checks)

    checks = []
    rules = policy.get("consistency", [])
    if not isinstance(rules, list):
        raise ValueError("Consistency rules must be a list.")
    used_references = set()
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError("Consistency rules must be objects.")
        if rule.get("kind") == "compare":
            _keys(rule, ("kind", "left", "right", "op"), ("left", "right", "op"))
            _columns(frame, list(dict.fromkeys([rule["left"], rule["right"]])))
            if not isinstance(rule["op"], str) or rule["op"] not in OPERATORS:
                raise ValueError("Unsupported comparison operator.")
            left, right = frame[rule["left"]], frame[rule["right"]]
            try:
                valid = left.notna() & right.notna() & getattr(left, OPERATORS[rule["op"]])(right)
            except TypeError as exc:
                raise ValueError("Comparison rules require compatible column types.") from exc
        elif rule.get("kind") == "foreign_key":
            _keys(
                rule,
                ("kind", "columns", "reference", "reference_columns"),
                ("columns", "reference", "reference_columns"),
            )
            name = rule["reference"]
            ref = _reference(references, name)
            local, remote = _columns(frame, rule["columns"]), _columns(ref, rule["reference_columns"])
            if len(local) != len(remote):
                raise ValueError("Foreign key column counts must agree.")
            valid = frame[local].notna().all(axis=1) & pd.MultiIndex.from_frame(frame[local]).isin(
                pd.MultiIndex.from_frame(ref[remote].dropna())
            )
            used_references.add(name)
        else:
            raise ValueError("Unsupported consistency rule kind.")
        checks.append(_check(f"consistency_{i}", valid))
    dimensions["consistency"] = _result(checks)

    checks = []
    possible = 0
    if "accuracy" in policy:
        spec = policy["accuracy"]
        _keys(spec, ("reference", "keys", "columns", "atol", "rtol", "key_types"), ("reference", "keys", "columns"))
        name = spec["reference"]
        ref = _reference(references, name)
        cols = _columns(frame, spec["columns"])
        _columns(ref, cols)
        joined = _joined_reference(frame, ref, spec["keys"], spec.get("key_types", {}))
        atol, rtol = _number(spec.get("atol", 0)), _number(spec.get("rtol", 0))
        if min(atol, rtol) < 0:
            raise ValueError("Accuracy tolerances cannot be negative.")
        for i, column in enumerate(cols):
            observed = frame[column].reset_index(drop=True)
            truth = joined[column]
            known = truth.notna()
            if pd.api.types.is_numeric_dtype(ref[column]):
                numeric_observed = pd.to_numeric(observed, errors="coerce")
                a = numeric_observed.to_numpy(dtype=float, na_value=np.nan)
                b = pd.to_numeric(truth, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
                known &= np.isfinite(b)
                if atol == 0 and rtol == 0:
                    # Python numeric equality preserves integer distinctions above 2**53.
                    valid = numeric_observed.astype(object).eq(truth.astype(object)).fillna(False).to_numpy(dtype=bool)
                    valid = valid & np.isfinite(a)
                else:
                    for values in (numeric_observed, truth):
                        if any(isinstance(value, Integral) and abs(value) > 2**53 for value in values.astype(object)):
                            raise ValueError(
                                "Large-integer accuracy with nonzero tolerances requires explicit rescaling."
                            )
                    valid = np.isfinite(a) & np.isclose(a, b, atol=atol, rtol=rtol)
            else:
                valid = observed.eq(truth).fillna(False).to_numpy(dtype=bool)
            known_array = known.to_numpy(dtype=bool)
            check = _check(f"accuracy_{i}", np.asarray(valid)[known_array])
            # Map failures back to original input row positions, not joined row labels.
            indices = np.flatnonzero(known_array)
            check["failed_row_positions"] = indices[np.flatnonzero(~np.asarray(valid)[known_array])[:20]].tolist()
            checks.append(check)
            possible += len(frame)
        used_references.add(name)
    dimensions["accuracy"] = _result(checks, possible=possible)

    checks = []
    normalized_as_of = None
    if "timeliness" in policy:
        spec = policy["timeliness"]
        _keys(spec, ("column", "max_age_seconds", "future_tolerance_seconds"), ("column", "max_age_seconds"))
        column = _columns(frame, [spec["column"]])[0]
        if as_of is None:
            raise ValueError("Timeliness requires an explicit timezone-aware as-of timestamp.")
        stamp = pd.Timestamp(as_of)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError("As-of must be a timezone-aware timestamp.")
        stamp = stamp.tz_convert("UTC")
        normalized_as_of = stamp.isoformat()
        age, future = _number(spec["max_age_seconds"]), _number(spec.get("future_tolerance_seconds", 0))
        if age < 0 or future < 0:
            raise ValueError("Freshness limits cannot be negative.")
        # UTC is explicit policy semantics for naive source timestamp strings.
        times = pd.to_datetime(frame[column], errors="coerce", utc=True, format="mixed")
        elapsed = (stamp - times).dt.total_seconds()
        checks.append(_check("timestamp_age_sla", times.notna() & elapsed.between(-future, age)))
    dimensions["timeliness"] = _result(checks)
    scored = [d["score"] for d in dimensions.values() if d["score"] is not None]
    full = all(d["status"] == "assessed" for d in dimensions.values())
    return {
        "schema_version": 1,
        "input_sha256": sha256_dataframe(frame),
        "policy_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "reference_sha256": {name: sha256_dataframe(references[name]) for name in sorted(used_references)},
        "as_of_utc": normalized_as_of,
        "dimensions": dimensions,
        "overall_score": float(np.mean(scored)) if full else None,
        "assessed_score": float(np.mean(scored)) if scored else None,
        "fully_assessed_dimensions": sum(d["status"] == "assessed" for d in dimensions.values()),
        "aggregation": "Equal dimension weights; overall score requires all six dimensions fully assessed.",
        "descriptive_only": True,
        "data_modified": False,
    }


def dimension_table(audit: dict) -> pd.DataFrame:
    """Numeric facts only; no claims of observed accuracy without reference truth."""
    return pd.DataFrame(
        [
            {"dimension": name, **{k: result[k] for k in ("status", "score", "assessed", "failed", "coverage")}}
            for name, result in audit["dimensions"].items()
        ]
    )
