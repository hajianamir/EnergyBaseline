"""Safe composable row-business assertions; no executable expressions, automatic repairs or raw value export."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .common import columns, fields, number


ARITHMETIC = {"add", "subtract", "multiply", "divide"}
COMPARISONS = {"eq", "ne", "lt", "le", "gt", "ge"}
LOGIC = {"all", "any", "not", "implies"}


def _validate(node, depth=0, counter=None):
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if depth > 16 or counter[0] > 500 or not isinstance(node, dict):
        raise ValueError("Business expression depth/node bound exceeded or malformed expression.")
    if "column" in node:
        fields(node, ("column",))
        if not isinstance(node["column"], str):
            raise ValueError("A column expression needs a name.")
        return
    if "literal" in node:
        fields(node, ("literal",))
        if (
            type(node["literal"]) not in (str, int, float, bool)
            or isinstance(node["literal"], float)
            and not np.isfinite(node["literal"])
        ):
            raise ValueError("Business literals must be finite scalar strings/numbers/booleans.")
        return
    if node.get("op") == "in_reference":
        fields(node, ("op", "columns", "reference_columns"))
        for key in ("columns", "reference_columns"):
            values = node[key]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(c, str) for c in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("Reference keys must be explicit unique column-name lists.")
        if len(node["columns"]) != len(node["reference_columns"]):
            raise ValueError("Input and reference key dimensions must agree.")
        return
    fields(node, ("op", "args"), ("atol",))
    op, args = node["op"], node["args"]
    if op not in ARITHMETIC | COMPARISONS | LOGIC | {"is_missing", "is_finite", "absolute", "close"} or not isinstance(
        args, list
    ):
        raise ValueError("Unsupported business expression operator.")
    required = 1 if op in ("not", "is_missing", "is_finite", "absolute") else 2
    if op in ("all", "any"):
        if not 2 <= len(args) <= 20:
            raise ValueError("Boolean composition requires two to twenty operands.")
    elif len(args) != required:
        raise ValueError("Incorrect business expression arity.")
    if op == "close":
        number(node.get("atol", 0), "atol", 0)
    elif "atol" in node:
        raise ValueError("Tolerance applies only to the explicit close operator.")
    for arg in args:
        _validate(arg, depth + 1, counter)


def _numeric(series, *, allow_infinite=False):
    if (
        not pd.api.types.is_numeric_dtype(series)
        or pd.api.types.is_bool_dtype(series)
        or pd.api.types.is_complex_dtype(series)
    ):
        raise ValueError("Arithmetic requires explicit real numeric columns/literals.")
    if pd.api.types.is_integer_dtype(series) and any(abs(int(v)) > 2**53 for v in series.dropna()):
        raise ValueError(
            "Arithmetic on integers above 2**53 requires explicit rescaling; exact comparisons remain supported."
        )
    values = series.astype("Float64")
    if not allow_infinite and np.isinf(values.to_numpy(dtype=float, na_value=np.nan)).any():
        raise ValueError("Infinite arithmetic operands are not permitted.")
    return values


def _boolean(series):
    if not pd.api.types.is_bool_dtype(series):
        raise ValueError("Business boolean composition does not coerce numbers or strings to truth values.")
    return series.astype("boolean")


def _evaluate(node, frame, reference):
    if "column" in node:
        return columns(frame, [node["column"]]).iloc[:, 0].reset_index(drop=True)
    if "literal" in node:
        return pd.Series([node["literal"]] * len(frame))
    op = node["op"]
    if op == "in_reference":
        if reference is None:
            raise ValueError("Reference membership requires an explicit caller-bound reference dataset.")
        left = columns(frame, node["columns"])
        right = columns(reference, node["reference_columns"])
        if right.isna().any().any():
            raise ValueError("Reference key rows must be nonmissing; the engine never silently drops them.")
        for a, b in zip(left.columns, right.columns):
            if left[a].dtype != right[b].dtype:
                raise ValueError("Reference key dtypes must match explicitly; no implicit coercion/rounding.")
        membership = pd.MultiIndex.from_frame(left).isin(pd.MultiIndex.from_frame(right))
        return pd.Series(membership & ~left.isna().any(axis=1).to_numpy(), dtype="boolean")
    args = [_evaluate(arg, frame, reference) for arg in node["args"]]
    if op == "is_missing":
        return args[0].isna().astype("boolean")
    if op == "is_finite":
        value = _numeric(args[0], allow_infinite=True)
        return pd.Series(np.isfinite(value.to_numpy(dtype=float, na_value=np.nan)), dtype="boolean")
    if op in LOGIC:
        args = [_boolean(arg) for arg in args]
        if op == "not":
            return ~args[0]
        if op == "implies":
            return ~args[0] | args[1]
        result = args[0]
        for arg in args[1:]:
            result = result & arg if op == "all" else result | arg
        return result
    if op in ARITHMETIC | {"absolute", "close"}:
        args = [_numeric(arg) for arg in args]
        if op == "absolute":
            return args[0].abs()
        a, b = args
        if op == "close":
            return ((a - b).abs() <= node.get("atol", 0)).astype("boolean")
        if op == "add":
            value = a + b
        elif op == "subtract":
            value = a - b
        elif op == "multiply":
            value = a * b
        else:
            value = a / b.mask(b == 0, pd.NA)
        if np.isinf(value.to_numpy(dtype=float, na_value=np.nan)).any():
            raise ValueError("Business arithmetic overflow; explicitly rescale the policy inputs.")
        return value
    a, b = args
    # Same-type exact comparisons preserve integer precision. Numeric/string coercion is never guessed.
    numeric_a = pd.api.types.is_numeric_dtype(a) and not pd.api.types.is_bool_dtype(a)
    numeric_b = pd.api.types.is_numeric_dtype(b) and not pd.api.types.is_bool_dtype(b)
    if numeric_a != numeric_b:
        raise ValueError("Comparison operands have incompatible explicit types.")
    if numeric_a and (pd.api.types.is_complex_dtype(a) or pd.api.types.is_complex_dtype(b)):
        raise ValueError("Complex business comparisons are not supported.")
    if numeric_a:
        int_a, int_b = pd.api.types.is_integer_dtype(a), pd.api.types.is_integer_dtype(b)
        if int_a != int_b and any(abs(int(v)) > 2**53 for v in (a if int_a else b).dropna()):
            raise ValueError("Mixed float/large-integer comparisons require explicit rescaling.")
        if int_a and int_b:
            a, b = a.astype(object), b.astype(object)
    unknown = a.isna() | b.isna()
    functions = {
        "eq": pd.Series.eq,
        "ne": pd.Series.ne,
        "lt": pd.Series.lt,
        "le": pd.Series.le,
        "gt": pd.Series.gt,
        "ge": pd.Series.ge,
    }
    value = functions[op](a, b).astype("boolean")
    return value.mask(unknown, pd.NA)


def business_validation(frame, reference, spec):
    fields(spec, ("rules",))
    rules = spec["rules"]
    if not isinstance(rules, list) or not 1 <= len(rules) <= 50:
        raise ValueError("Declare one to fifty composable business assertions.")
    identifiers = set()
    total_nodes = 0
    for rule in rules:
        fields(rule, ("id", "assert"))
        if (
            not isinstance(rule["id"], str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", rule["id"])
            or rule["id"] in identifiers
        ):
            raise ValueError("Business rule IDs must be unique short codes.")
        identifiers.add(rule["id"])
        node_count = [0]
        _validate(rule["assert"], counter=node_count)
        total_nodes += node_count[0]
    if len(frame) * len(rules) > 2_000_000 or len(frame) * total_nodes > 20_000_000:
        raise ValueError("Business rule-evaluation resource bound exceeded.")
    output = []
    for rule in rules:
        assessed = _boolean(_evaluate(rule["assert"], frame, reference))
        passed = assessed.fillna(False).to_numpy(dtype=bool)
        failures = np.flatnonzero(~passed)
        output.append(
            {
                "id": rule["id"],
                "evaluated_rows": len(frame),
                "passed_rows": int(passed.sum()),
                "failed_rows": int(len(failures)),
                "unknown_rows": int(assessed.isna().sum()),
                "first_failed_positions": failures[:20].tolist(),
                "failure_list_truncated": len(failures) > 20,
            }
        )
    return {
        "rules": output,
        "all_passed": all(row["failed_rows"] == 0 for row in output),
        "unknown_truth_fails": True,
        "automatic_repairs": False,
        "raw_values_exported": False,
        "engine": "bounded typed declarative business assertions; not Pandera/Great Expectations",
    }
