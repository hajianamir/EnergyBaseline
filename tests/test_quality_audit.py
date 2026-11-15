"""Executable quality policies: missing evidence, matching, privacy and CLI contracts."""

import copy
import json

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from energybaseline.cli import main
from energybaseline.core.quality_audit import DIMENSIONS, audit_quality, dimension_table


def example():
    frame = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "value": [2.0, 3.0, 4.0],
            "limit": [5.0] * 3,
            "time": ["2026-01-01T00:00:00Z"] * 3,
            "category": ["a"] * 3,
        }
    )
    truth = frame[["id", "value"]].iloc[::-1].reset_index(drop=True)
    policy = {
        "version": 1,
        "unique_by": ["id"],
        "validity": [
            {"kind": "range", "column": "value", "min": 0, "max": 5},
            {"kind": "allowed", "column": "category", "values": ["a", "b"]},
        ],
        "consistency": [
            {"kind": "compare", "left": "value", "op": "le", "right": "limit"},
            {"kind": "foreign_key", "columns": ["id"], "reference": "truth", "reference_columns": ["id"]},
        ],
        "accuracy": {"reference": "truth", "keys": ["id"], "columns": ["value"], "atol": 0.01},
        "timeliness": {"column": "time", "max_age_seconds": 3600},
    }
    return frame, truth, policy


def run(frame, truth, policy):
    return audit_quality(frame, policy, references={"truth": truth}, as_of="2026-01-01T01:00:00Z")


def test_six_axes_are_reference_bound_deterministic_and_read_only():
    frame, truth, policy = example()
    before, ref_before, policy_before = frame.copy(deep=True), truth.copy(deep=True), copy.deepcopy(policy)
    result = run(frame, truth, policy)
    assert list(result["dimensions"]) == list(DIMENSIONS)
    assert result["overall_score"] == result["assessed_score"] == 1
    assert result["fully_assessed_dimensions"] == 6
    assert len(result["reference_sha256"]["truth"]) == 64
    assert result == run(frame, truth, policy)
    assert len(dimension_table(result)) == 6
    assert_frame_equal(frame, before)
    assert_frame_equal(truth, ref_before)
    assert policy == policy_before
    truth.loc[0, "value"] = 99
    changed = run(frame, truth, policy)
    assert changed["reference_sha256"] != result["reference_sha256"]
    assert changed["dimensions"]["accuracy"]["score"] == pytest.approx(2 / 3)


def test_unconfigured_axes_never_receive_perfect_scores():
    result = audit_quality(example()[0])
    assert result["fully_assessed_dimensions"] == 2
    assert result["overall_score"] is None
    assert result["assessed_score"] == 1
    for name in ("accuracy", "timeliness", "validity", "consistency"):
        assert result["dimensions"][name]["status"] == "not_assessed"
        assert result["dimensions"][name]["score"] is None


def test_failures_and_missing_truth_have_distinct_denominators():
    frame, truth, policy = example()
    frame.loc[1, "value"] = np.nan
    truth = truth.iloc[:2]
    result = run(frame, truth, policy)
    accuracy = result["dimensions"]["accuracy"]
    assert accuracy["status"] == "partial"
    assert accuracy["assessed"] == 2 and accuracy["possible"] == 3
    assert accuracy["score"] == 0.5
    assert accuracy["checks"][0]["failed_row_positions"] == [1]
    assert result["overall_score"] is None
    assert result["dimensions"]["consistency"]["failed"] == 2
    assert result["dimensions"]["completeness"]["score"] == pytest.approx(14 / 15)


def test_missing_observation_is_failure_when_reference_truth_exists():
    frame, truth, policy = example()
    frame.loc[2, "value"] = np.nan
    result = run(frame, truth, policy)
    assert result["dimensions"]["accuracy"]["failed"] == 1
    assert result["dimensions"]["accuracy"]["status"] == "assessed"


def test_no_raw_or_reference_values_are_serialized():
    secret = "synthetic-sensitive-value-never-export"
    frame = pd.DataFrame({"id": [1, 2], "value": [secret, None]})
    truth = pd.DataFrame({"id": [1, 2], "value": [secret, secret]})
    result = audit_quality(
        frame, {"accuracy": {"reference": "truth", "keys": ["id"], "columns": ["value"]}}, references={"truth": truth}
    )
    assert secret not in json.dumps(result, allow_nan=False)
    assert result["dimensions"]["accuracy"]["score"] == 0.5
    assert result["dimensions"]["accuracy"]["checks"][0]["failed_row_positions"] == [1]


@pytest.mark.parametrize("mutation", ["duplicate_input", "duplicate_reference", "null_input_key", "null_reference_key"])
def test_ambiguous_accuracy_matching_fails_closed(mutation):
    frame, truth, policy = example()
    if mutation == "duplicate_input":
        frame.loc[1, "id"] = frame.loc[0, "id"]
    elif mutation == "duplicate_reference":
        truth.loc[1, "id"] = truth.loc[0, "id"]
    elif mutation == "null_input_key":
        frame.loc[1, "id"] = np.nan
    else:
        truth.loc[1, "id"] = np.nan
    with pytest.raises(ValueError, match="keys"):
        run(frame, truth, policy)


@pytest.mark.parametrize(
    "policy",
    [
        {"version": 2},
        {"unexpected": True},
        {"columns": ["missing"]},
        {"unique_by": []},
        {"validity": "not a list"},
        {"consistency": {}},
        {"validity": [{"kind": "eval", "column": "value"}]},
        {"validity": [{"kind": "range", "column": "value", "min": 10, "max": 0}]},
        {"validity": [{"kind": "range", "column": "value"}]},
        {"validity": [{"kind": "allowed", "column": "value", "values": []}]},
        {"validity": [{"kind": "not_null", "column": "value", "min": 0}]},
        {"accuracy": {"reference": "absent", "keys": ["id"], "columns": ["value"]}},
        {"accuracy": {"reference": "truth", "keys": ["id"], "columns": ["value"], "atol": -1}},
        {"consistency": [{"kind": "compare", "left": "value", "right": "limit", "op": "execute"}]},
        {
            "consistency": [
                {"kind": "foreign_key", "columns": ["id"], "reference": "truth", "reference_columns": ["id", "value"]}
            ]
        },
    ],
)
def test_bad_policies_fail_without_execution(policy):
    frame, truth, _ = example()
    with pytest.raises(ValueError):
        run(frame, truth, policy)


@pytest.mark.parametrize("as_of", [None, "2026-01-01", "NaT", "not-a-date"])
def test_freshness_requires_reproducible_explicit_timezone(as_of):
    frame, truth, policy = example()
    with pytest.raises(ValueError):
        audit_quality(frame, policy, references={"truth": truth}, as_of=as_of)


def test_stale_future_and_unparseable_timestamps_fail_sla():
    frame, truth, policy = example()
    frame["time"] = ["2025-01-01", "2027-01-01", "invalid"]
    result = run(frame, truth, policy)
    assert result["dimensions"]["timeliness"]["score"] == 0
    assert result["dimensions"]["timeliness"]["failed"] == 3


def test_empty_data_and_nullable_numeric_are_json_safe():
    empty = audit_quality(pd.DataFrame({"id": pd.Series(dtype="int64")}))
    assert empty["overall_score"] is empty["assessed_score"] is None
    frame = pd.DataFrame({"n": pd.Series([1, None, 3], dtype="Int64")})
    result = audit_quality(frame, {"validity": [{"kind": "finite", "column": "n"}]})
    assert result["dimensions"]["validity"]["failed"] == 1
    json.dumps(result, allow_nan=False)


def test_duplicate_rows_and_missing_keys_do_not_inflate_uniqueness():
    frame = pd.DataFrame({"id": [1, 1, None, 2]})
    result = audit_quality(frame, {"unique_by": ["id"]})
    assert result["dimensions"]["uniqueness"]["score"] == 0.5


def test_cli_executes_policy_and_enforces_threshold(tmp_path, capsys):
    frame, truth, policy = example()
    frame.to_csv(tmp_path / "input.csv", index=False)
    truth.to_csv(tmp_path / "truth.csv", index=False)
    (tmp_path / "policy.json").write_text(json.dumps(policy))
    command = [
        "quality-audit",
        str(tmp_path / "input.csv"),
        "--policy",
        str(tmp_path / "policy.json"),
        "--reference",
        f"truth={tmp_path / 'truth.csv'}",
        "--as-of",
        "2026-01-01T01:00:00Z",
        "--fail-under",
        "1",
    ]
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["fully_assessed_dimensions"] == 6
    assert main(["quality-audit", str(tmp_path / "input.csv"), "--fail-under", "0.5"]) == 1
    assert json.loads(capsys.readouterr().out)["overall_score"] is None
    assert main(["quality-audit", str(tmp_path / "input.csv"), "--reference", "bad-binding"]) == 2
    assert "unique name=path" in capsys.readouterr().err


@pytest.mark.parametrize("references", [[], {"bad": None}, {"": pd.DataFrame()}, {1: pd.DataFrame()}])
def test_bad_reference_mappings_are_rejected(references):
    with pytest.raises(ValueError):
        audit_quality(example()[0], references=references)


def test_datetime_key_normalization_is_explicit_and_detects_collisions():
    frame = pd.DataFrame({"key": ["2026-01-01T00:00:00Z", "2025-12-31T19:00:00-05:00"], "value": [1, 2]})
    ref = pd.DataFrame({"key": ["2026-01-01T00:00:00Z"], "value": [1]})
    policy = {
        "accuracy": {"reference": "r", "keys": ["key"], "columns": ["value"], "key_types": {"key": "datetime_utc"}}
    }
    with pytest.raises(ValueError, match="unique keys"):
        audit_quality(frame, policy, references={"r": ref})


def test_input_shape_and_invalid_versions_are_not_silently_coerced():
    with pytest.raises(ValueError):
        audit_quality(example()[0], {"version": True})
    with pytest.raises(ValueError):
        audit_quality(pd.DataFrame(columns=["x", "x"]))
    with pytest.raises(ValueError):
        audit_quality(pd.DataFrame(), {"columns": ["missing"]})


def test_cli_missing_files_return_structured_error(tmp_path, capsys):
    assert main(["quality-audit", str(tmp_path / "missing.csv")]) == 2
    assert json.loads(capsys.readouterr().err)["valid"] is False


def test_incompatible_comparisons_return_safe_validation_error():
    frame = pd.DataFrame({"left": ["private-cell-literal"], "right": [1]})
    with pytest.raises(ValueError, match="compatible column types") as error:
        audit_quality(frame, {"consistency": [{"kind": "compare", "left": "left", "right": "right", "op": "lt"}]})
    assert "private-cell-literal" not in str(error.value)


def test_empty_frame_does_not_bypass_key_validation():
    with pytest.raises(ValueError):
        audit_quality(pd.DataFrame(), {"unique_by": ["missing"]})


def test_large_integer_accuracy_cannot_be_inflated_by_float_rounding():
    frame = pd.DataFrame({"id": [1], "value": [2**53 + 1]})
    truth = pd.DataFrame({"id": [1], "value": [2**53]})
    policy = {"accuracy": {"reference": "r", "keys": ["id"], "columns": ["value"]}}
    result = audit_quality(frame, policy, references={"r": truth})
    assert result["dimensions"]["accuracy"]["score"] == 0
    policy["accuracy"]["atol"] = 0.1
    with pytest.raises(ValueError, match="explicit rescaling"):
        audit_quality(frame, policy, references={"r": truth})


def test_missing_reference_rows_preserve_exact_known_integer_truth():
    frame = pd.DataFrame({"id": [1, 2], "value": [2**53 + 1, 5]})
    truth = pd.DataFrame({"id": [1], "value": [2**53 + 1]})
    result = audit_quality(
        frame, {"accuracy": {"reference": "r", "keys": ["id"], "columns": ["value"]}}, references={"r": truth}
    )
    assert result["dimensions"]["accuracy"]["score"] == 1
    assert result["dimensions"]["accuracy"]["coverage"] == 0.5


def test_numeric_range_limits_preserve_large_integer_distinctions():
    frame = pd.DataFrame({"n": [2**53, 2**53 + 1]})
    result = audit_quality(frame, {"validity": [{"kind": "range", "column": "n", "min": 2**53 + 1}]})
    assert result["dimensions"]["validity"]["score"] == 0.5
