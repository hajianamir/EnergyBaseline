"""Real NLP/survival fits, analytic oracles, leakage and privacy regressions."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
from scipy.optimize._numdiff import approx_derivative
from statsmodels.duration.hazard_regression import PHReg

from energybaseline.workbench import run_workbench
from energybaseline.workbench.common import digest
from energybaseline.workbench.survival import concordance, weibull_objective

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/catalog"


def execute(operation, spec, frame):
    request = dict(version=1, operation=operation, parameters=copy.deepcopy(spec))
    original, before = frame.copy(deep=True), copy.deepcopy(request)
    output = run_workbench(request, frame)
    assert request == before
    assert_frame_equal(frame, original)
    assert output["spec_sha256"] == digest(request)
    assert output["input_sha256"] is not None
    assert "PRIVATE" not in json.dumps(output, allow_nan=False)
    result = output["result"]
    assert not result["baseline_changed"] and not result["model_persisted"]
    assert not result["p_values_or_confidence_intervals_computed"]
    return result


@pytest.fixture
def documents():
    text = [
        "PRIVATE apple fruit orchard sweet",
        "PRIVATE train station rail ticket",
        "PRIVATE computer software code binary",
    ]
    return pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=120, freq="h"),
            "text": [text[i % 3] for i in range(120)],
            "label": ["PRIVATE-class-" + str(i % 3) for i in range(120)],
        }
    )


def nlp_spec(classifier=True):
    spec = dict(text="text", time="time", train_rows=90, gap=3, max_features=100)
    spec.update(target="label") if classifier else spec.update(topics=3)
    return spec


@pytest.fixture
def survival_data():
    rng = np.random.default_rng(318)
    n = 360
    x = rng.normal(size=n)
    # Weibull PH and Weibull AFT are equivalent parameterizations of this DGP.
    failure = 8 * np.exp(-0.8 * x) * rng.weibull(1.7, n)
    censor = rng.uniform(4, 30, n)
    duration = np.minimum(failure, censor)
    return pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=n, freq="h"),
            "x": x,
            "duration": duration,
            "event": (failure <= censor).astype(int),
            "entry": 0.0,
        }
    )


def survival_spec(method="cox_breslow"):
    return dict(
        duration="duration",
        event="event",
        features=["x"],
        time="time",
        train_rows=260,
        gap=3,
        method=method,
        horizons=[1.0, 3.0, 6.0],
    )


def test_multiclass_actual_predictions_privacy_and_train_only_vocabulary(documents):
    result = execute("text-classification", nlp_spec(), documents)
    assert result["class_count"] == 3 and result["accuracy"] == result["macro_f1"] == 1.0
    assert np.array(result["confusion_matrix_true_rows_predicted_columns"]).trace() == 30
    assert 0 < result["log_loss"] < 0.4
    assert result["train_rows"] == 87 and result["purged_rows"] == 3
    assert not result["plaintext_or_tokens_exported"] and not result["pretrained_weights_used"]
    changed = documents.copy()
    changed.loc[87:, "text"] = "neverobservedtoken"
    other = execute("text-classification", nlp_spec(), changed)
    for key in ("vocabulary_sha256", "vocabulary_size", "model_sha256", "training_sha256"):
        assert result[key] == other[key]
    assert other["test_documents_without_known_tokens"] == 30
    assert other["accuracy"] < 0.4


def test_binary_text_and_single_class_holdout(documents):
    documents = documents[documents.label != "PRIVATE-class-2"].reset_index(drop=True)
    spec = nlp_spec()
    spec["train_rows"] = 60
    documents.loc[60:, "label"] = documents.label.iloc[0]
    result = execute("text-classification", spec, documents)
    assert result["class_count"] == 2 and np.isfinite(result["log_loss"])


def test_topics_actual_nmf_nonnegative_mass_and_no_future_vocabulary(documents):
    result = execute("text-topics", nlp_spec(False), documents)
    assert result["topic_count"] == 3 and result["test_reconstruction_frobenius"] < 0.01
    assert np.isclose(sum(result["mean_test_topic_weights"]), 1.0)
    assert min(result["training_topic_mass"]) > 0
    changed = documents.copy()
    changed.loc[87:, "text"] = "neverobservedtoken"
    other = execute("text-topics", nlp_spec(False), changed)
    for key in ("components_sha256", "training_sha256", "vocabulary_sha256", "training_reconstruction_error"):
        assert result[key] == other[key]
    assert other["test_documents_without_topic_weight"] == 30
    assert other["mean_test_topic_weights"] == [0.0] * 3


@pytest.mark.parametrize("operation", ["text-classification", "text-topics"])
@pytest.mark.parametrize(
    "change",
    [
        {"time": "text"},
        {"train_rows": True},
        {"gap": 0},
        {"gap": 80},
        {"max_features": 10001},
        {"train_rows": 120},
        {"unexpected": 1},
    ],
)
def test_nlp_strict_bounds(documents, operation, change):
    spec = nlp_spec(operation == "text-classification")
    spec.update(change)
    with pytest.raises(ValueError):
        run_workbench(dict(version=1, operation=operation, parameters=spec), documents)


@pytest.mark.parametrize("bad", [None, 9, "", " ", "x" * 10001])
def test_text_rejects_missing_or_unbounded_documents(documents, bad):
    documents["text"] = documents.text.astype(object)
    documents.loc[0, "text"] = bad
    with pytest.raises(ValueError):
        run_workbench(dict(version=1, operation="text-classification", parameters=nlp_spec()), documents)


def test_text_rejects_unseen_classes_and_target_leakage(documents):
    for spec in [dict(nlp_spec(), target="text"), dict(nlp_spec(), target="time")]:
        with pytest.raises(ValueError):
            run_workbench(dict(version=1, operation="text-classification", parameters=spec), documents)
    documents.loc[119, "label"] = "PRIVATE-unseen"
    with pytest.raises(ValueError, match="raw error details are not exported") as exc:
        run_workbench(dict(version=1, operation="text-classification", parameters=nlp_spec()), documents)
    assert "PRIVATE" not in str(exc.value)


@pytest.mark.parametrize("method", ["cox_breslow", "weibull_aft"])
def test_survival_real_likelihood_discrimination_monotone_prediction_and_no_leakage(survival_data, method):
    spec = survival_spec(method)
    result = execute("survival-regression", spec, survival_data)
    assert result["optimizer_converged"] and result["training_events"] > 100
    assert result["train_rows"] == 257 and result["test_rows"] == 100
    assert 0.7 < result["holdout_harrell_concordance"] < 1
    assert result["holdout_comparable_pairs"] > 1000
    prediction = result["mean_test_conditional_survival"]
    assert all(0 < x < 1 for x in prediction) and np.all(np.diff(prediction) < 0)
    if method == "weibull_aft":
        assert 1.2 < result["weibull_shape"] < 2.2
    changed = survival_data.copy()
    changed.loc[257:, "duration"] *= 1.3
    changed.loc[257:, "event"] = 0
    other = execute("survival-regression", spec, changed)
    for key in (
        "training_sha256",
        "parameters_sha256",
        "baseline_hazard_sha256",
        "training_negative_log_likelihood",
        "predictions_sha256",
    ):
        assert result[key] == other[key]
    assert other["holdout_harrell_concordance"] is None and other["holdout_comparable_pairs"] == 0


@pytest.mark.parametrize("method", ["cox_breslow", "weibull_aft"])
def test_delayed_entry_is_conditional_and_likelihood_handles_left_truncation(survival_data, method):
    # Entry is explicitly before each observed duration; at horizon=entry S(t|T>entry)=1.
    survival_data.loc[:, "duration"] += 2.0
    survival_data.loc[:, "entry"] = 1.0
    spec = dict(survival_spec(method), entry="entry", horizons=[1.0, 3.0, 6.0])
    result = execute("survival-regression", spec, survival_data)
    assert result["delayed_entry"] and result["mean_test_conditional_survival"][0] == 1.0
    assert np.all(np.diff(result["mean_test_conditional_survival"]) <= 0)


def test_weibull_likelihood_gradient_includes_censoring_and_delayed_entry():
    x = np.c_[np.ones(5), [-1, 0, 1, 0.2, 0.6]]
    t, event, entry = np.array([1, 2, 3, 4, 5.0]), np.array([1, 0, 1, 1, 0]), np.array([0, 0.5, 1, 2, 0])
    parameters = np.array([1.2, -0.3, 0.4])
    value, gradient = weibull_objective(parameters, x, t, event, entry)
    shape = np.exp(parameters[-1])
    scale = np.exp(x @ parameters[:-1])
    from scipy.stats import weibull_min

    reference = -np.sum(
        event * weibull_min.logpdf(t, shape, scale=scale)
        + (1 - event) * weibull_min.logsf(t, shape, scale=scale)
        - weibull_min.logsf(entry, shape, scale=scale)
    )
    assert value == pytest.approx(reference, rel=1e-12)
    numeric = approx_derivative(lambda p: weibull_objective(p, x, t, event, entry)[0], parameters).ravel()
    np.testing.assert_allclose(gradient, numeric, atol=1e-7)


def test_cox_breslow_ties_and_entry_match_independent_risk_set_likelihood():
    x = np.array([[-1], [0], [1], [0.2], [0.5], [0.8]])
    t, event, entry = np.array([2, 3, 3, 5, 6, 6.0]), np.array([1, 1, 1, 0, 1, 0]), np.array([0, 0, 2, 0, 3, 1.0])
    beta = np.array([0.6])
    model = PHReg(t, x, status=event, entry=entry, ties="breslow")
    manual = sum(
        float(x[i] @ beta) - np.log(np.exp(x[(entry <= t[i]) & (t >= t[i])] @ beta).sum())
        for i in np.flatnonzero(event)
    )
    assert model.loglike(beta) == pytest.approx(manual, rel=1e-12)


def test_concordance_ties_censoring_and_entry():
    assert concordance(np.array([1, 2, 3.0]), np.array([1, 1, 0]), np.array([3, 2, 1.0]), np.zeros(3)) == (1.0, 3)
    assert concordance(np.array([1, 2.0]), np.array([1, 0]), np.ones(2), np.zeros(2)) == (0.5, 1)
    assert concordance(np.array([1, 2.0]), np.array([1, 0]), np.ones(2), np.array([0, 1.5])) == (None, 0)
    assert concordance(np.ones(2), np.ones(2), np.ones(2), np.zeros(2)) == (None, 0)


@pytest.mark.parametrize(
    "change",
    [
        {"features": ["duration"]},
        {"features": ["event"]},
        {"features": ["time"]},
        {"features": []},
        {"entry": "duration"},
        {"train_rows": True},
        {"gap": 0},
        {"horizons": [0]},
        {"horizons": [2, 1]},
        {"horizons": [1e10]},
        {"horizons": [True]},
        {"horizons": [[1]]},
        {"method": "unknown"},
    ],
)
def test_survival_rejects_leakage_unsupported_contract_and_extrapolation(survival_data, change):
    spec = survival_spec()
    spec.update(change)
    with pytest.raises(ValueError):
        run_workbench(dict(version=1, operation="survival-regression", parameters=spec), survival_data)


@pytest.mark.parametrize(
    "column,value",
    [
        ("duration", 0),
        ("duration", -1),
        ("duration", np.inf),
        ("event", 2),
        ("event", None),
        ("entry", -1),
        ("x", np.nan),
    ],
)
def test_survival_rejects_invalid_observations(survival_data, column, value):
    survival_data.loc[0, column] = value
    with pytest.raises(ValueError):
        run_workbench(
            dict(version=1, operation="survival-regression", parameters=dict(survival_spec(), entry="entry")),
            survival_data,
        )


def test_survival_nonidentifiability_and_no_events_fail_closed(survival_data):
    for column, value in [("x", 1), ("event", 0)]:
        changed = survival_data.copy()
        changed.loc[:256, column] = value
        with pytest.raises(ValueError):
            run_workbench(dict(version=1, operation="survival-regression", parameters=survival_spec()), changed)


@pytest.mark.parametrize("name", ["text-classification", "text-topics", "cox-breslow", "weibull-aft"])
def test_actual_independent_cli_examples(name, tmp_path):
    request = EXAMPLES / (name + ".json")
    dataset = "text.csv" if name.startswith("text") else "survival.csv"
    output = tmp_path / "result.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "energybaseline.cli",
            "workbench",
            str(request),
            "--dataset",
            str(EXAMPLES / dataset),
            "--out",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text())
    assert json.loads(completed.stdout) == result
    assert result["operation"] == json.loads(request.read_text())["operation"]
    assert not result["result"]["baseline_changed"]
    assert "PRIVATE" not in output.read_text()


@pytest.fixture
def graph_data():
    frame = pd.read_csv(EXAMPLES / "nodes.csv")
    frame["label"] = "PRIVATE-" + frame.label
    spec = json.loads((EXAMPLES / "graph-node-classification.json").read_text())["parameters"]
    return frame, spec


def test_real_gcn_learns_message_passing_and_holdout_labels_never_enter_loss(graph_data):
    frame, spec = graph_data
    result = execute("graph-node-classification", spec, frame)
    assert result["test_accuracy"] == result["test_macro_f1"] == 1.0
    assert result["final_train_cross_entropy"] < result["initial_train_cross_entropy"] / 10
    assert result["train_nodes"] == 60 and result["test_nodes"] == 20
    assert not result["node_labels_exported"] and not result["paper_benchmark_parity_claimed"]
    changed = frame.copy()
    changed.loc[60:, "label"] = frame.label.iloc[0]
    other = execute("graph-node-classification", spec, changed)
    for key in ("weights_sha256", "predictions_sha256", "training_labels_sha256", "final_train_cross_entropy"):
        assert result[key] == other[key]
    assert other["test_accuracy"] == 0.5


def test_gcn_graph_normalization_matches_hand_oracle():
    from energybaseline.workbench.graph_learning import normalized_adjacency

    graph = normalized_adjacency(3, [[0, 1]])
    np.testing.assert_allclose(graph, [[0.5, 0.5, 0], [0.5, 0.5, 0], [0, 0, 1]], atol=1e-7)


@pytest.mark.parametrize(
    "edges",
    [[], [[0, 0]], [[0, 80]], [[-1, 2]], [[True, 2]], [[1, 2, 3]], [[1, 2], [2, 1]], [[0.1, 1]], "PRIVATE-graph"],
)
def test_gcn_rejects_ambiguous_or_invalid_topology(graph_data, edges):
    frame, spec = graph_data
    spec["edges"] = edges
    with pytest.raises(ValueError):
        run_workbench(dict(version=1, operation="graph-node-classification", parameters=spec), frame)


@pytest.mark.parametrize(
    "change",
    [
        {"features": ["label"]},
        {"epochs": 0},
        {"epochs": 301},
        {"learning_rate": True},
        {"train_rows": 80},
        {"width": 65},
        {"seed": -1},
    ],
)
def test_gcn_rejects_leakage_and_unbounded_training(graph_data, change):
    frame, spec = graph_data
    spec.update(change)
    with pytest.raises(ValueError):
        run_workbench(dict(version=1, operation="graph-node-classification", parameters=spec), frame)


def test_gcn_restores_torch_rng_and_thread_state(graph_data):
    import torch

    frame, spec = graph_data
    rng, threads = torch.random.get_rng_state().clone(), torch.get_num_threads()
    execute("graph-node-classification", spec, frame)
    assert torch.equal(rng, torch.random.get_rng_state()) and threads == torch.get_num_threads()


def test_gcn_actual_independent_cli(tmp_path):
    output = tmp_path / "graph.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "energybaseline.cli",
            "workbench",
            str(EXAMPLES / "graph-node-classification.json"),
            "--dataset",
            str(EXAMPLES / "nodes.csv"),
            "--out",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text())
    assert json.loads(completed.stdout) == result
    assert result["result"]["test_accuracy"] == 1.0


@pytest.mark.parametrize("values", [[True, False], [1 + 0j, 0j], ["1", "0"], [1, None]])
def test_preexisting_binary_helper_rejects_silent_type_coercion(values):
    from energybaseline.workbench.common import binary

    frame = pd.DataFrame({"event": values})
    with pytest.raises(ValueError):
        binary(frame, "event")


@pytest.mark.parametrize("dtype", ["int64", "float64", "Int64", "Float64"])
def test_binary_helper_keeps_explicit_real_zero_one_inputs(dtype):
    from energybaseline.workbench.common import binary

    frame = pd.DataFrame({"event": pd.Series([0, 1, 1, 0], dtype=dtype)})
    np.testing.assert_array_equal(binary(frame, "event"), [0, 1, 1, 0])
