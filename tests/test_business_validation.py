"""Executable declarative business policies: no evaluation, raw-value leak, coercion or repair."""

from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from energybaseline.workbench import run_workbench
from energybaseline.workbench.common import digest


def col(name):
    return {"column": name}


def literal(value):
    return {"literal": value}


def expr(op, *args, **extra):
    return {"op": op, "args": list(args), **extra}


def run(frame, rules, reference=None):
    plan = {
        "version": 1,
        "operation": "business-validation",
        "parameters": {"rules": [{"id": f"R{i}", "assert": rule} for i, rule in enumerate(rules)]},
    }
    original = frame.copy(deep=True)
    before = copy.deepcopy(plan)
    result = run_workbench(plan, frame, reference=reference)
    assert_frame_equal(original, frame)
    assert plan == before
    assert result["spec_sha256"] == digest(plan)
    assert result["reference_sha256"] is not None if reference is not None else result["reference_sha256"] is None
    assert "PRIVATE" not in json.dumps(result, allow_nan=False)
    assert not result["result"]["automatic_repairs"] and not result["result"]["raw_values_exported"]
    return result["result"]


def test_arithmetic_implication_missing_and_kleene_unknown_are_not_automatic_repairs():
    frame = pd.DataFrame(
        {
            "quantity": [2.0, 3.0, np.nan, 2.0],
            "price": [5.0, 5.0, 7.0, 0.0],
            "total": [10.0, 14.0, 10.0, 0.0],
            "active": [True, True, False, True],
            "name": ["PRIVATE-A"] * 4,
        }
    )
    rules = [
        expr("close", expr("multiply", col("quantity"), col("price")), col("total"), atol=0.01),
        expr("implies", col("active"), expr("gt", col("quantity"), literal(0))),
        expr("eq", expr("divide", col("total"), col("price")), col("quantity")),
        expr("not", expr("is_missing", col("quantity"))),
        expr("any", expr("is_missing", col("quantity")), expr("ge", col("quantity"), literal(2))),
    ]
    rows = run(frame, rules)["rules"]
    assert [(r["passed_rows"], r["failed_rows"], r["unknown_rows"]) for r in rows] == [
        (2, 2, 1),
        (4, 0, 0),
        (1, 3, 2),
        (3, 1, 0),
        (4, 0, 0),
    ]
    assert rows[0]["first_failed_positions"] == [1, 2]


@pytest.mark.parametrize("op,expected", [("eq", 1), ("ne", 2), ("lt", 1), ("le", 2), ("gt", 1), ("ge", 2)])
def test_exact_large_unsigned_integer_comparisons_do_not_round_through_float(op, expected):
    base = 2**63 + 10
    frame = pd.DataFrame({"a": pd.Series([base - 1, base, base + 1], dtype="uint64")})
    row = run(frame, [expr(op, col("a"), literal(base))])["rules"][0]
    assert row["passed_rows"] == expected


def test_reference_composite_keys_are_explicit_bound_and_nonmissing():
    frame = pd.DataFrame({"id": [1, 2, 3, 1], "region": ["PRIVATE-A", "PRIVATE-A", "PRIVATE-B", None]})
    reference = pd.DataFrame({"key": [1, 3], "area": ["PRIVATE-A", "PRIVATE-B"]})
    policy = {"op": "in_reference", "columns": ["id", "region"], "reference_columns": ["key", "area"]}
    row = run(frame, [policy], reference)["rules"][0]
    assert row["passed_rows"] == 2 and row["first_failed_positions"] == [1, 3]
    with pytest.raises(ValueError):
        run(frame, [policy])
    missing = reference.copy()
    missing.loc[0, "area"] = None
    with pytest.raises(ValueError):
        run(frame, [policy], missing)
    mismatched = reference.copy()
    mismatched["key"] = mismatched.key.astype(str)
    with pytest.raises(ValueError):
        run(frame, [policy], mismatched)


def test_failure_export_is_bounded_and_position_based_not_index_or_values():
    frame = pd.DataFrame({"a": range(100)}, index=["PRIVATE-INDEX"] * 100)
    result = run(frame, [expr("lt", col("a"), literal(-1))])
    row = result["rules"][0]
    assert row["failed_rows"] == 100 and row["failure_list_truncated"]
    assert row["first_failed_positions"] == list(range(20)) and not result["all_passed"]


@pytest.mark.parametrize(
    "rule",
    [
        expr("__import__", literal("PRIVATE-SECRET")),
        expr("all", literal(1), literal(True)),
        expr("add", literal("PRIVATE-SECRET"), literal(1)),
        expr("eq", col("a"), literal("PRIVATE-SECRET")),
        expr("not", literal(1)),
        {"op": "is_missing", "args": []},
        {"column": "a", "execute": "PRIVATE-SECRET"},
        {"literal": float("inf")},
        expr("eq", expr("add", literal(2**63), literal(1)), col("a")),
        expr("eq", expr("multiply", literal(1e308), literal(1e308)), literal(0)),
        expr("eq", col("a"), literal(float(2**63))),
    ],
)
def test_unsafe_untyped_or_overflowing_policies_fail_closed_without_value_leaks(rule):
    frame = pd.DataFrame({"a": pd.Series([2**63 + 10], dtype="uint64")})
    with pytest.raises(ValueError) as error:
        run(frame, [rule])
    assert "PRIVATE-SECRET" not in str(error.value)


def test_boolean_and_arithmetic_operators_and_finite_check():
    frame = pd.DataFrame({"a": [1.0, -2.0, np.nan, np.inf]})
    row = run(frame, [expr("is_finite", col("a"))])["rules"][0]
    assert row["passed_rows"] == 2
    frame = frame.iloc[:3].copy()
    rules = [
        expr("eq", expr("absolute", col("a")), literal(2)),
        expr("all", expr("gt", col("a"), literal(0)), expr("le", col("a"), literal(2))),
        expr("eq", expr("subtract", expr("add", col("a"), literal(1)), literal(1)), col("a")),
    ]
    rows = run(frame, rules)["rules"]
    assert [r["passed_rows"] for r in rows] == [1, 1, 2]
    assert all(r["unknown_rows"] == 1 for r in rows)


def test_depth_and_node_bounds_enforced_before_execution():
    node = literal(True)
    for _ in range(18):
        node = expr("not", node)
    with pytest.raises(ValueError):
        run(pd.DataFrame({"a": [1]}), [node])
    node = literal(True)
    for _ in range(3):
        node = expr("all", *[copy.deepcopy(node) for _ in range(10)])
    with pytest.raises(ValueError):
        run(pd.DataFrame({"a": [1]}), [node])


def test_total_expression_work_bound_is_not_only_rule_count():
    node = expr("eq", col("a"), literal(1))
    # Use 404 nodes (<500) to exceed total node-work without overflowing the rule-count bound.
    branch = expr("all", *[copy.deepcopy(node) for _ in range(10)])
    tree = expr("all", *[copy.deepcopy(branch) for _ in range(13)])
    frame = pd.DataFrame({"a": np.ones(60_000)})
    with pytest.raises(ValueError):
        run(frame, [tree])
