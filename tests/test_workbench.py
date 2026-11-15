"""Executable cross-domain contracts; deterministic oracles, leakage, privacy and immutability."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from scipy.optimize import linprog
from statsmodels.stats.multitest import multipletests

from energybaseline.core.tcp import MultipleTestingCorrection, TCPValidationError, adjust_p_values, normalize_correction
from energybaseline.workbench import OPERATION_NAMES, run_workbench
from energybaseline.workbench.common import digest
from energybaseline.workbench.forecasting import baseline_forecast
from energybaseline.workbench.preparation import PastOnlyTargetEncoder, TabularTransformer


def job(operation, **parameters):
    return {"version": 1, "operation": operation, "parameters": parameters}


def execute(operation, frame=None, reference=None, **parameters):
    original = frame.copy(deep=True) if frame is not None else None
    original_reference = reference.copy(deep=True) if reference is not None else None
    request = job(operation, **parameters)
    before = copy.deepcopy(request)
    result = run_workbench(request, frame, reference=reference)
    assert before == request
    assert result["spec_sha256"] == digest(request)
    assert result["operation"] == operation
    json.dumps(result, allow_nan=False)
    if frame is not None:
        assert_frame_equal(original, frame)
        assert len(result["input_sha256"]) == 64
    if reference is not None:
        assert_frame_equal(original_reference, reference)
        assert len(result["reference_sha256"]) == 64
    return result["result"]


@pytest.fixture
def regression_data():
    rng = np.random.default_rng(16)
    n = 160
    X = rng.normal(size=(n, 4))
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="h"),
            **{f"x{i}": X[:, i] for i in range(4)},
            "y": 3 * X[:, 0] + rng.normal(0, 0.01, n),
            "y2": -2 * X[:, 0] + rng.normal(0, 0.01, n),
        }
    )


def test_empirical_financial_risk_fractional_tail_and_initial_peak():
    frame = pd.DataFrame({"a": [-0.1, -0.2, 0.05, 0.1], "b": [-0.1, -0.2, 0.05, 0.1]})
    result = execute("portfolio-risk", frame, returns=["a", "b"], weights=[0.25, 0.75], confidence=0.625)
    assert result["var"] == pytest.approx(0.1)
    assert result["expected_shortfall"] == pytest.approx((0.2 + 0.5 * 0.1) / 1.5)
    assert result["maximum_drawdown"] == pytest.approx(0.28)
    assert result["sample_volatility"] == pytest.approx(frame.a.std(ddof=1))
    assert result["terminal_wealth_per_unit"] == pytest.approx(0.9 * 0.8 * 1.05 * 1.1)
    assert not result["annualized"]


@pytest.mark.parametrize("returns", [[0.1], [-1.1, 0.1], [np.nan, 0.1], [np.inf, 0.1]])
def test_risk_rejects_undefined_data(returns):
    with pytest.raises(ValueError):
        execute("portfolio-risk", pd.DataFrame({"a": returns}), returns=["a"], weights=[1], confidence=0.95)


def test_fairness_defined_and_suppressed_denominators_and_privacy():
    frame = pd.DataFrame(
        {
            "g": ["secret-A"] * 4 + ["secret-B"] * 4 + ["secret-small"],
            "y": [1, 1, 0, 0] * 2 + [1],
            "p": [1, 1, 1, 0, 1, 0, 0, 0, 1],
        }
    )
    result = execute("group-fairness", frame, group="g", outcome="y", prediction="p", minimum_group_size=2)
    assert result["demographic_parity_difference"] == pytest.approx(0.5)
    assert result["disparate_impact_ratio"] == pytest.approx(1 / 3)
    assert result["equalized_odds_difference"] == pytest.approx(0.5)
    assert result["groups"][2] == {"group_position": 2, "n": 1, "status": "suppressed"}
    assert "secret" not in json.dumps(result)
    frame["y"] = 1
    undefined = execute("group-fairness", frame, group="g", outcome="y", prediction="p", minimum_group_size=2)
    assert undefined["equalized_odds_difference"] is None
    assert undefined["groups"][0]["false_positive_rate"] is None


def test_fairness_all_zero_selections_not_perfect_ratio():
    frame = pd.DataFrame({"g": ["a", "a", "b", "b"], "y": [0, 1, 0, 1], "p": [0, 0, 0, 0]})
    result = execute("group-fairness", frame, group="g", outcome="y", prediction="p", minimum_group_size=2)
    assert result["disparate_impact_ratio"] is None
    assert result["demographic_parity_difference"] == 0


def test_categorical_divergence_new_category_and_smoothing():
    reference, frame = pd.DataFrame({"x": ["a"] * 4}), pd.DataFrame({"x": ["b"] * 4})
    result = execute("categorical-divergence", frame, reference=reference, column="x")
    assert result["jensen_shannon_divergence"] == pytest.approx(np.log(2))
    assert result["kl_is_infinite"] and result["kl_divergence"] is None
    smoothed = execute("categorical-divergence", frame, reference=reference, column="x", pseudocount=1)
    assert smoothed["kl_divergence"] == pytest.approx((2 / 3) * np.log(5))
    identical = execute("categorical-divergence", reference, reference=reference, column="x")
    assert identical["kl_divergence"] == identical["jensen_shannon_divergence"] == 0


def test_kaplan_meier_ties_censoring_and_undefined_median():
    frame = pd.DataFrame({"t": [1, 2, 2, 3], "e": [1, 1, 0, 1]})
    result = execute("kaplan-meier", frame, duration="t", event="e")
    assert [r["at_risk"] for r in result["curve"]] == [4, 3, 1]
    assert [r["survival"] for r in result["curve"]] == pytest.approx([0.75, 0.5, 0])
    assert result["median_survival"] == 2
    frame.e = 0
    assert execute("kaplan-meier", frame, duration="t", event="e")["median_survival"] is None


def test_graph_dangling_nodes_components_shortest_paths_and_convergence():
    frame = pd.DataFrame({"s": ["private_a", "private_b", "private_c"], "t": ["private_b", "private_a", "private_d"]})
    result = execute("graph-profile", frame, source="s", target="t", path_source_position=0)
    assert result["nodes"] == 4 and result["weak_components"] == 2
    assert result["converged"] and sum(result["pagerank"]) == pytest.approx(1)
    assert result["pagerank"][0] == pytest.approx(result["pagerank"][1])
    assert result["pagerank"][3] > result["pagerank"][2]
    assert result["shortest_distances"] == [0, 1, None, None]
    assert "private" not in json.dumps(result)
    unfinished = execute("graph-profile", frame, source="s", target="t", max_iterations=1)
    assert not unfinished["converged"]


def test_text_retrieval_no_download_or_raw_text_and_unknown_query():
    frame = pd.DataFrame({"text": ["private apple orchard harvest", "private engine motor service", "apple harvest"]})
    result = execute("text-retrieval", frame, text="text", query="apple harvest", top_k=2)
    assert result["matches"][0]["row_position"] == 2
    assert result["matches"][1]["row_position"] == 0
    assert "private" not in json.dumps(result)
    assert execute("text-retrieval", frame, text="text", query="qqqqzz")["matches"] == []


def test_entity_candidates_reference_fitted_and_non_mutating():
    reference = pd.DataFrame({"name": ["ACME  INC", "Other business"]})
    incoming = pd.DataFrame({"company": ["  acme inc  ", "Acme, Inc", "zzzz"]})
    result = execute(
        "entity-candidates", incoming, reference=reference, column="company", reference_column="name", threshold=0.5
    )
    assert result["matches"][0]["candidates"][0]["reference_position"] == 0
    assert result["matches"][0]["candidates"][0]["cosine_similarity"] == pytest.approx(1)
    assert result["matches"][1]["candidates"][0]["reference_position"] == 0
    assert result["matches"][2]["candidates"] == [] and result["automatic_merges"] == 0
    assert "ACME" not in json.dumps(result)


def test_rating_svd_holdout_and_cold_start():
    frame = pd.DataFrame(
        {
            "u": ["u0", "u0", "u1", "u1", "u2", "u2", "u0", "new"],
            "i": ["i0", "i1", "i0", "i2", "i1", "i2", "i2", "i0"],
            "r": [5.0, 4.0, 4.0, 1.0, 1.0, 2.0, 2.0, 5.0],
        }
    )
    result = execute("rating-svd", frame, user="u", item="i", rating="r", train_rows=6, rank=1, user_position=0)
    assert result["known_test_rows"] == 1 and result["cold_start_rows"] == 1
    assert [r["item_position"] for r in result["recommendations"]] == [2]
    changed = frame.copy()
    changed.loc[6:, "r"] = -1e6
    second = execute("rating-svd", changed, user="u", item="i", rating="r", train_rows=6, rank=1, user_position=0)
    assert second["recommendations"] == result["recommendations"]
    assert second["rmse_known_test"] != result["rmse_known_test"]


def test_lp_milp_oracle_and_infeasible_not_success():
    common = dict(
        objective=[-3, -2], lower=[0, 0], upper=[1, 1], matrix=[[2, 2]], constraint_lower=[0], constraint_upper=[3]
    )
    continuous = execute("linear-optimize", **common, integrality=[0, 0])
    oracle = linprog([-3, -2], A_ub=[[2, 2]], b_ub=[3], bounds=[(0, 1), (0, 1)], method="highs")
    assert continuous["objective"] == pytest.approx(oracle.fun)
    binary = execute("linear-optimize", **common, integrality=[1, 1])
    assert binary["solution"] == pytest.approx([1, 0]) and binary["objective"] == -3
    assert binary["feasibility_checked"] and binary["status"] == "optimal"
    infeasible = execute(
        "linear-optimize",
        objective=[1],
        lower=[0],
        upper=[1],
        integrality=[1],
        matrix=[[1]],
        constraint_lower=[2],
        constraint_upper=[3],
    )
    assert infeasible["status"] == "infeasible" and infeasible["solution"] is None


def test_pareto_directions_and_duplicate_retention():
    frame = pd.DataFrame({"cost": [1, 2, 3, 1], "quality": [3, 2, 4, 3]})
    assert execute("pareto-front", frame, objectives=["cost", "quality"], directions=["min", "max"])[
        "nondominated_positions"
    ] == [0, 2, 3]


@pytest.mark.parametrize("servers,mean_wait,drain,utilization", [(1, 2.25, 8, 1), (2, 0.5, 5, 0.8)])
def test_fcfs_discrete_events_have_known_schedule(servers, mean_wait, drain, utilization):
    frame = pd.DataFrame({"arrival": [0, 1, 1, 3], "service": [3, 2, 1, 2]})
    result = execute("fcfs-queue", frame, arrival="arrival", service="service", servers=servers)
    assert result["mean_wait"] == pytest.approx(mean_wait)
    assert result["drain_time"] == drain
    assert result["utilization"] == pytest.approx(utilization)


@pytest.mark.parametrize("imputation", ["mean", "median", "most_frequent"])
@pytest.mark.parametrize("scaling", ["none", "standard", "robust", "minmax", "power"])
def test_train_only_tabular_imputation_encoding_scaling(imputation, scaling):
    train = pd.DataFrame({"x": [1.0, np.nan, 3.0, 8.0], "cat": ["a", None, "b", "a"], "target": [1, 2, 3, 4]})
    held = pd.DataFrame({"x": [1e9, np.nan], "cat": ["unseen", None], "target": [np.nan, np.nan]})
    transformer = TabularTransformer(
        numeric_columns=["x"],
        categorical_columns=["cat"],
        target_columns=["target"],
        imputation=imputation,
        scaling=scaling,
    )
    with pytest.raises(ValueError):
        transformer.transform(held)
    transformer.fit(train)
    before = transformer.transform(train).copy()
    transformed = transformer.transform(held)
    assert transformed.shape == (2, 3) and np.isfinite(transformed).all()
    assert transformed[0, 1:].sum() == 0
    np.testing.assert_array_equal(transformer.transform(train), before)
    assert held.target.isna().all() and pd.isna(train.loc[1, "x"])


def test_transform_target_guard_all_missing_failure_resets_fitted_state():
    with pytest.raises(ValueError):
        TabularTransformer(numeric_columns=["y"], categorical_columns=[], target_columns=["y"])
    transformer = TabularTransformer(numeric_columns=["x"], categorical_columns=[], target_columns=["y"])
    transformer.fit(pd.DataFrame({"x": [1, 2, 3]}))
    with pytest.raises(ValueError):
        transformer.fit(pd.DataFrame({"x": [np.nan, np.nan]}))
    with pytest.raises(ValueError):
        transformer.transform(pd.DataFrame({"x": [2]}))


def test_transform_and_target_encoding_job_reachability():
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=6, freq="D"),
            "x": [1.0, 2.0, np.nan, 4.0, 5.0, 99.0],
            "cat": ["a", "b", "a", "b", "new", "a"],
            "y": [1, 2, 3, 4, 5, 6],
        }
    )
    result = execute("tabular-transform", frame, numeric=["x"], categorical=["cat"], targets=["y"], train_rows=4)
    assert result["features"] == 3 and not result["targets_imputed"]
    first = execute("target-encoding", frame, category="cat", target="y", time="time", train_rows=4, prior=0)
    changed = frame.copy()
    changed.loc[4:, "y"] = 100000
    second = execute("target-encoding", changed, category="cat", target="y", time="time", train_rows=4, prior=0)
    assert first == second


def test_target_encoder_excludes_current_and_future_labels_and_unknown_prior():
    categories = ["a", "a", "b", "a"]
    encoder = PastOnlyTargetEncoder(prior=0, smoothing=1)
    values = encoder.fit_transform(categories, np.array([10.0, 20.0, 30.0, 40.0]))
    assert values == pytest.approx([0, 5, 0, 10])
    assert encoder.transform(["a", "new"]) == pytest.approx([17.5, 0])
    other = PastOnlyTargetEncoder(prior=0, smoothing=1)
    changed = other.fit_transform(categories, np.array([10.0, 20.0, -1000.0, 40000.0]))
    np.testing.assert_array_equal(changed[:3], values[:3])
    assert changed[3] == values[3]  # the changed label is a different category, not a global-mean leak


@pytest.mark.parametrize(
    "method,expected",
    [("median", [2, 2, 2]), ("last", [3, 3, 3]), ("seasonal_naive", [2, 3, 2]), ("random_walk_drift", [4, 5, 6])],
)
def test_baseline_forecast_oracles(method, expected):
    assert baseline_forecast([1, 2, 3], 3, method=method, period=2) == pytest.approx(expected)


def test_croston_sba_exact_and_all_zero():
    # First event 4 at index 1 => interval 2. Next 8 at index 3 => smoothed size 6, interval 2.
    assert baseline_forecast([0, 4, 0, 8], 3, method="croston_sba", alpha=0.5) == pytest.approx([2.25] * 3)
    assert baseline_forecast([0, 0], 2, method="croston_sba").tolist() == [0, 0]
    with pytest.raises(ValueError):
        baseline_forecast([1, -1], 2, method="croston_sba")


@pytest.mark.parametrize(
    "method", ["median", "last", "seasonal_naive", "random_walk_drift", "croston_sba", "var", "vecm"]
)
def test_forecast_jobs_holdout_and_no_target_imputation(method):
    rng = np.random.default_rng(2)
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=100, freq="D"),
            "a": 20 + np.cumsum(rng.normal(size=100)),
            "b": 30 + np.cumsum(rng.normal(size=100)),
        }
    )
    options = {"rank": 1, "lags": 1} if method == "vecm" else {"lags": 1} if method == "var" else {}
    result = execute(
        "forecast-holdout",
        frame,
        target=["a", "b"] if method in ("var", "vecm") else "a",
        time="time",
        train_rows=80,
        method=method,
        **options,
    )
    assert result["test_rows"] == 20 and np.isfinite(result["rmse"]).all()
    assert not result["p_values_or_intervals_computed"]
    if method == "random_walk_drift":
        pred = baseline_forecast(frame.a[:80], 20, method=method)
        assert result["rmse"][0] == pytest.approx(np.sqrt(np.mean((pred - frame.a[80:]) ** 2)))


@pytest.mark.parametrize("estimator", ["ridge", "huber", "ransac", "knn", "decision_tree"])
def test_regression_estimators_reachable_no_baseline_promotion(regression_data, estimator):
    result = execute(
        "regression-holdout",
        regression_data,
        target="y",
        features=["x0", "x1"],
        time="time",
        train_rows=120,
        estimator=estimator,
    )
    assert result["train_rows"] == 119 and result["test_rows"] == 40 and result["purged_rows"] == 1
    assert np.isfinite(result["rmse"]) and not result["baseline_changed"]
    assert result["rmse"] < 4


@pytest.mark.parametrize("selection", ["rfe", "sequential_forward", "sequential_backward"])
def test_selection_inside_training_and_holdout_labels_cannot_change_ale_or_stress(regression_data, selection):
    parameters = dict(
        target="y",
        features=["x0", "x1", "x2", "x3"],
        time="time",
        train_rows=120,
        estimator="ridge",
        selection=selection,
        feature_count=1,
        ale_feature="x0",
        ale_bins=5,
        stress=[{"x0": 1.0}],
    )
    first = execute("regression-holdout", regression_data, **parameters)
    assert first["selected_feature_positions"] == [0]
    assert first["stress"][0]["mean_prediction_shift"] == pytest.approx(3, abs=0.05)
    ale = first["ale"]
    assert np.average(ale["centered_effects"], weights=ale["counts"]) == pytest.approx(0, abs=1e-12)
    assert np.diff(ale["centered_effects"]).min() > 0
    changed = regression_data.copy()
    changed.loc[120:, "y"] = -1e9
    second = execute("regression-holdout", changed, **parameters)
    assert first["ale"] == second["ale"] and first["selected_feature_positions"] == second["selected_feature_positions"]
    assert first["stress"][0]["mean_prediction_shift"] == second["stress"][0]["mean_prediction_shift"]
    assert second["rmse"] > first["rmse"]


def test_multi_target_consensus_uses_training_only(regression_data):
    spec = dict(features=["x0", "x1", "x2", "x3"], targets=["y", "y2"], time="time", train_rows=120, feature_count=1)
    first = execute("multi-target-consensus", regression_data, **spec)
    assert first["selected_feature_positions"] == [0]
    changed = regression_data.copy()
    changed.loc[120:, ["y", "y2"]] = 0
    assert execute("multi-target-consensus", changed, **spec) == first


@pytest.mark.parametrize(
    "method,backend",
    [("sidak", "sidak"), ("holm_sidak", "holm-sidak"), ("hochberg", "simes-hochberg"), ("hommel", "hommel")],
)
def test_extended_corrections_exact_oracle_order_and_alias(method, backend):
    raw = [0.2, 0.001, 0.04, 0.04, 1, 0]
    result = adjust_p_values(raw, method)
    assert [r["p_raw"] for r in result] == raw
    assert [r["p_adj"] for r in result] == pytest.approx(multipletests(raw, method=backend)[1])
    assert normalize_correction(backend) == method
    assert method in [v.value for v in MultipleTestingCorrection]
    assert adjust_p_values([], method) == []
    with pytest.raises(TCPValidationError):
        adjust_p_values([np.nan], method)


def test_hommel_resource_guard():
    with pytest.raises(TCPValidationError):
        adjust_p_values([0.1] * 5001, "hommel")


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"version": True, "operation": "fcfs-queue", "parameters": {}},
        job("__import__", module="os"),
        {**job("fcfs-queue"), "path": "secret"},
    ],
)
def test_strict_request_envelope(bad):
    with pytest.raises(ValueError):
        run_workbench(bad)


@pytest.mark.parametrize(
    "operation,parameters",
    [
        ("portfolio-risk", dict(returns=["x"], weights=[1], confidence=0.99, annualize=True)),
        ("group-fairness", dict(group="g", outcome="x", prediction="x", minimum_group_size=2)),
        ("graph-profile", dict(source="g", target="x")),
        ("fcfs-queue", dict(arrival="x", service="x", servers=True)),
        ("kaplan-meier", dict(duration="x", event="x")),
        ("regression-holdout", dict(target="x", features=["x"], time="g", train_rows=2, estimator="ridge")),
        ("forecast-holdout", dict(target="x", time="g", train_rows=2, method="last")),
    ],
)
def test_invalid_operations_fail_closed_without_raw_error_values(operation, parameters):
    frame = pd.DataFrame({"g": ["SECRET-VALUE", "OTHER-VALUE"], "x": [2, 3]})
    with pytest.raises(ValueError) as exc:
        run_workbench(job(operation, **parameters), frame)
    assert "SECRET-VALUE" not in str(exc.value)


def test_input_numeric_precision_empty_and_reference_guards():
    for frame in (
        pd.DataFrame({"x": []}),
        pd.DataFrame({"x": [2**53 + 1, 2**53 + 2]}),
        pd.DataFrame({"x": ["1", "2"]}),
        pd.DataFrame({"x": [1 + 2j, 3 + 4j]}),
    ):
        with pytest.raises(ValueError):
            run_workbench(job("portfolio-risk", returns=["x"], weights=[1], confidence=0.95), frame)
    with pytest.raises(ValueError):
        run_workbench(job("categorical-divergence", column="x"), pd.DataFrame({"x": [1]}))
    with pytest.raises(ValueError):
        run_workbench(
            job("kaplan-meier", duration="x", event="e"),
            pd.DataFrame({"x": [1], "e": [1]}),
            reference=pd.DataFrame({"x": [1]}),
        )


def test_cli_success_errors_and_private_errors(tmp_path, capsys):
    from energybaseline.cli import main

    request = tmp_path / "job.json"
    request.write_text(json.dumps(job("linear-optimize", objective=[-1], lower=[0], upper=[1], integrality=[1])))
    output = tmp_path / "result.json"
    assert main(["workbench", str(request), "--out", str(output)]) == 0
    assert json.loads(output.read_text())["result"]["objective"] == -1
    capsys.readouterr()
    request.write_text('{"secret":"PRIVATE-PASSWORD"}')
    assert main(["workbench", str(request)]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE-PASSWORD" not in captured.err + captured.out
    assert json.loads(captured.err)["error_type"] == "ValueError"
    assert main(["workbench", str(tmp_path / "missing.json")]) == 2


def test_registry_has_explicit_tested_operations():
    assert len(OPERATION_NAMES) == 19 and len(set(OPERATION_NAMES)) == 19


@pytest.mark.parametrize(
    "example",
    json.loads((Path(__file__).resolve().parents[1] / "examples/workbench/manifest.json").read_text()),
    ids=lambda row: row["request"],
)
def test_all_documented_examples_execute_through_cli(example, tmp_path, capsys):
    from pathlib import Path
    from energybaseline.cli import main

    root = Path(__file__).resolve().parents[1] / "examples/workbench"
    args = ["workbench", str(root / example["request"])]
    for field in ("dataset", "reference"):
        if field in example:
            args.extend([f"--{field}", str(root / example[field])])
    out = tmp_path / "result.json"
    assert main([*args, "--out", str(out)]) == 0
    result = json.loads(out.read_text())
    assert result["operation"] == json.loads((root / example["request"]).read_text())["operation"]
    assert len(result["spec_sha256"]) == 64
    assert "result" in result
    assert json.loads(capsys.readouterr().out) == result


def test_kaplan_meier_matches_scipy_right_censored_ecdf():
    from scipy.stats import CensoredData, ecdf

    rng = np.random.default_rng(44)
    durations = rng.integers(0, 8, 100)
    events = rng.integers(0, 2, 100)
    frame = pd.DataFrame({"t": durations, "e": events})
    observed = execute("kaplan-meier", frame, duration="t", event="e")["curve"]
    oracle = ecdf(CensoredData(uncensored=durations[events == 1], right=durations[events == 0])).sf
    assert [r["time"] for r in observed] == oracle.quantiles.tolist()
    assert [r["survival"] for r in observed] == pytest.approx(oracle.probabilities)


@pytest.mark.parametrize("value", [True, "1", 2**53 + 1, None])
def test_optimization_never_silently_converts_declared_literals(value):
    with pytest.raises(ValueError):
        execute("linear-optimize", objective=[value], lower=[0], upper=[1], integrality=[1])


def test_nested_empty_matrix_invalid_and_json_nan_forbidden():
    with pytest.raises(ValueError):
        execute("linear-optimize", objective=[1], lower=[0], upper=[1], integrality=[0], matrix=[[]])
    with pytest.raises(ValueError):
        run_workbench(job("linear-optimize", objective=[float("nan")], lower=[0], upper=[1], integrality=[0]))


def test_competing_risks_preserve_probability_and_do_not_treat_competitors_as_censoring():
    frame = pd.DataFrame({"t": [1, 2, 2, 3], "event": [1, 2, 0, 1]})
    result = execute("competing-risks", frame, duration="t", event_code="event")
    assert result["causes"] == [1, 2]
    assert result["curve"][1]["cumulative_incidence"] == pytest.approx([0.25, 0.25])
    assert result["curve"][-1]["cumulative_incidence"] == pytest.approx([0.75, 0.25])
    for row in result["curve"]:
        assert row["event_free_survival"] + sum(row["cumulative_incidence"]) == pytest.approx(1)
    frame.event = 0
    censored = execute("competing-risks", frame, duration="t", event_code="event")
    assert censored["causes"] == [] and all(r["event_free_survival"] == 1 for r in censored["curve"])


@pytest.mark.parametrize("method", ["logistic", "random_forest", "knn"])
def test_binary_classification_holdout_with_fairness_and_purging(regression_data, method):
    frame = regression_data.copy()
    frame["label"] = (frame.y > 0).astype(int)
    frame["group"] = ["private-A", "private-B"] * 80
    result = execute(
        "classification-holdout",
        frame,
        target="label",
        numeric=["x0", "x1"],
        categorical=[],
        time="time",
        train_rows=120,
        estimator=method,
        sensitive="group",
        minimum_group_size=2,
    )
    assert result["accuracy"] >= 0.8 and result["roc_auc"] > 0.9
    assert result["train_rows"] == 119 and result["purged_rows"] == 1
    assert sum(map(sum, result["confusion_matrix_true_rows_predicted_columns"])) == 40
    assert result["fairness"]["groups"][0]["n"] == 20
    assert "private" not in json.dumps(result)
    assert not result["baseline_changed"] and not result["p_values_or_confidence_intervals_computed"]


@pytest.mark.parametrize("method", ["isolation_forest", "local_outlier_factor", "one_class_svm"])
def test_novelty_detection_training_frozen_and_scores_have_no_distribution_free_claim(regression_data, method):
    frame = regression_data.copy()
    frame["event"] = [0] * 120 + [1] * 40
    frame.loc[120:, ["x0", "x1"]] += 50
    result = execute(
        "novelty-holdout",
        frame,
        features=["x0", "x1"],
        time="time",
        train_rows=120,
        method=method,
        contamination=0.05,
        outcome="event",
    )
    assert result["flagged_rows"] == 40 and result["position_list_truncated"]
    assert result["first_flagged_input_positions"] == list(range(120, 140))
    assert result["roc_auc"] is None
    assert not result["automatic_refit"] and not result["baseline_changed"]


def test_binary_classification_single_class_training_rejected_and_test_auc_undefined(regression_data):
    frame = regression_data.copy()
    frame["label"] = (frame.y > 0).astype(int)
    frame.loc[120:, "label"] = 0
    result = execute(
        "classification-holdout",
        frame,
        target="label",
        numeric=["x0"],
        categorical=[],
        time="time",
        train_rows=120,
        estimator="logistic",
    )
    assert result["roc_auc"] is None
    frame.label = 1
    with pytest.raises(ValueError):
        execute(
            "classification-holdout",
            frame,
            target="label",
            numeric=["x0"],
            categorical=[],
            time="time",
            train_rows=120,
            estimator="logistic",
        )


def test_training_encoding_and_consensus_do_not_need_holdout_labels(regression_data):
    frame = regression_data.copy()
    frame["cat"] = ["a", "b"] * 80
    frame.loc[120:, ["y", "y2"]] = np.nan
    result = execute("target-encoding", frame, category="cat", target="y", time="time", train_rows=120, prior=0)
    assert result["test_rows"] == 40
    result = execute(
        "multi-target-consensus",
        frame,
        features=["x0", "x1"],
        targets=["y", "y2"],
        time="time",
        train_rows=120,
        feature_count=1,
    )
    assert result["selected_feature_positions"] == [0]


def test_numeric_epoch_is_not_silently_interpreted_as_nanoseconds(regression_data):
    frame = regression_data.copy()
    frame["time"] = range(len(frame))
    with pytest.raises(ValueError):
        execute("forecast-holdout", frame, target="y", time="time", train_rows=120, method="last")


def test_extended_tcp_corrections_are_gate_locked_and_cache_only_what_if(regression_data):
    from energybaseline.core.cache import CachedComplianceRunner
    from energybaseline.core.tcp import TestConfigPanel as Panel
    from energybaseline.core.vsg import ComplianceGateError, VariableSelectionGate

    frame = regression_data[["time", "x0", "x1", "y"]]
    vsg = VariableSelectionGate(frame, targets=["y"], time_col="time")
    panel = Panel(vsg, config={"tcp_correction": "hommel"})
    runner = CachedComplianceRunner(seed=42)
    with pytest.raises(ComplianceGateError):
        runner.execute_tests(frame, vsg, panel)
    vsg.accept()
    panel.lock_preregistration()
    assert panel.correction == "hommel"
    original = runner.execute_tests(frame, vsg, panel)
    calls = runner.engine_call_count
    assert original and calls > 0
    for method in ("sidak", "holm_sidak", "hochberg", "hommel"):
        probe = panel.clone_for_what_if(correction=method)
        output = runner.what_if(probe)
        assert output["engine_calls_delta"] == 0
        assert output["correction"] == method
        assert all("p_raw" in r and "p_adj" in r for r in output["evaluated_results"])
    assert runner.engine_call_count == calls
    assert len(runner.decision_sensitivity_table(panel)["scenarios"]) == 30
    expanded = runner.decision_sensitivity_table(panel, corrections=["sidak", "hommel"])
    assert len(expanded["scenarios"]) == 12 and expanded["engine_calls_delta"] == 0
