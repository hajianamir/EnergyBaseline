"""Executable advanced algorithms: real optimizers/networks, independent oracles, holdout isolation and privacy."""

from __future__ import annotations

import copy
from pathlib import Path
from itertools import permutations
import json

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from energybaseline.workbench import OPERATION_NAMES, SPEC_OPERATIONS, run_workbench
from energybaseline.workbench.common import digest
from energybaseline.workbench.deep import ARCHITECTURES, fit_forecast, make_network, torch_scope
from energybaseline.workbench.planning import _crowding, _fronts
from energybaseline.workbench.vision import _boxes, _iou


def execute(operation, parameters, frame=None):
    before = copy.deepcopy(parameters)
    data = frame.copy(deep=True) if frame is not None else None
    request = {"version": 1, "operation": operation, "parameters": parameters}
    output = run_workbench(request, frame)
    assert output["spec_sha256"] == digest(request) and parameters == before
    json.dumps(output, allow_nan=False)
    if frame is not None:
        assert_frame_equal(frame, data)
    assert "PRIVATE" not in json.dumps(output)
    return output["result"]


@pytest.fixture
def series():
    rng = np.random.default_rng(4)
    n = 96
    x = rng.normal(size=n)
    return pd.DataFrame(
        {"time": pd.date_range("2025", periods=n, freq="h"), "x": x, "y": 3 * x + rng.normal(0, 0.1, n)}
    )


def test_actual_automl_nested_chronology_and_outer_holdout_never_selects(series):
    parameters = dict(time="time", target="y", features=["x"], train_rows=72, seed=4, trials=4, folds=3, gap=2)
    row = execute("automl-search", parameters, series)
    changed = series.copy()
    changed.loc[72:, "y"] += 1000
    changed.loc[72:, "x"] -= 1000
    other = execute("automl-search", parameters, changed)
    assert row["selected"] == other["selected"] and row["trials"] == other["trials"]
    assert row["training_sha256"] == other["training_sha256"]
    assert row["test_rmse"] < 2 and other["test_rmse"] > 10
    assert row["trials_completed"] == 4 and {t["parameters"]["family"] for t in row["trials"]} == {
        "ridge",
        "random_forest",
        "extra_trees",
        "hist_gradient_boosting",
    }
    assert not row["external_automl_engines_executed"]


@pytest.mark.parametrize(
    "changes",
    [{"features": ["y"]}, {"engine": "PRIVATE-H2O"}, {"trials": True}, {"gap": 100}, {"families": ["ridge", "ridge"]}],
)
def test_automl_invalid_or_mislabeled_jobs_rejected(series, changes):
    p = dict(time="time", target="y", features=["x"], train_rows=72, seed=4, trials=2)
    p.update(changes)
    with pytest.raises(ValueError) as e:
        execute("automl-search", p, series)
    assert "PRIVATE" not in str(e.value)


@pytest.mark.parametrize("architecture", ARCHITECTURES)
def test_every_reference_architecture_has_real_parameters_gradients_and_fixed_origin_training(architecture, series):
    row = execute(
        "deep-forecast",
        dict(time="time", target="y", train_rows=88, architecture=architecture, seed=7, context=8, width=8, epochs=3),
        series,
    )
    assert row["architecture"] == architecture and row["fit"]["parameter_count"] > 100
    assert row["fit"]["training_windows"] == 73 and len(row["fit"]["weight_sha256"]) == 64
    assert row["test_rows"] == 8 and row["rmse"] >= 0 and not row["uncertainty_intervals_computed"]
    with torch_scope(1) as torch:
        net = make_network(architecture, 1, 8, 4, 8)
        x = torch.randn(3, 8, 1)
        mean, scale = net(x)
        loss = mean.square().mean() + (scale.mean() if scale is not None else 0)
        loss.backward()
        assert mean.shape == (3, 4) and torch.isfinite(mean).all()
        gradients = [p.grad for p in net.parameters() if p.grad is not None]
        assert gradients and all(torch.isfinite(g).all() for g in gradients)
        assert any(torch.any(g != 0) for g in gradients)


def test_neural_architecture_search_uses_only_training_suffix_and_restores_global_torch_state(series):
    import torch

    rng = torch.random.get_rng_state().clone()
    threads = torch.get_num_threads()
    p = dict(
        time="time",
        target="y",
        train_rows=88,
        architecture="search",
        seed=7,
        context=8,
        epochs=2,
        search=[dict(architecture="patchtst", width=8), dict(architecture="tide", width=8)],
    )
    first = execute("deep-forecast", p, series)
    modified = series.copy()
    modified.loc[88:, "y"] += 100
    second = execute("deep-forecast", p, modified)
    assert first["search"] == second["search"] and first["fit"] == second["fit"]
    assert first["training_values_sha256"] == second["training_values_sha256"]
    assert torch.equal(rng, torch.random.get_rng_state()) and torch.get_num_threads() == threads
    assert len(first["search"]) == 2 and first["rmse"] != second["rmse"]


@pytest.mark.parametrize(
    "changes",
    [
        {"architecture": "PRIVATE-foundation"},
        {"width": 7},
        {"features": ["y"]},
        {"epochs": 0},
        {"train_rows": 40},
        {"architecture": "nhits", "features": ["x"]},
    ],
)
def test_forecast_invalid_design_or_unimplemented_brand_is_not_silently_substituted(series, changes):
    p = dict(time="time", target="y", train_rows=88, architecture="patchtst", seed=7, context=8, width=8, epochs=1)
    p.update(changes)
    with pytest.raises(ValueError):
        execute("deep-forecast", p, series)


def test_raw_forecaster_never_receives_holdout_and_is_deterministic():
    values = np.sin(np.arange(64) / 5)[:, None]
    kwargs = dict(architecture="deepar", context=8, width=8, epochs=4, seed=3, learning_rate=0.01)
    a, fit = fit_forecast(values, 4, **kwargs)
    b, other = fit_forecast(values.copy(), 4, **kwargs)
    np.testing.assert_array_equal(a, b)
    assert fit == other
    assert fit["last_training_loss"] < fit["first_training_loss"]


def test_cvrp_matches_exhaustive_two_vehicle_permutations_and_capacity():
    coordinates = np.array([[0, 0], [1, 0], [2, 0], [0, 1], [0, 2]])
    costs = np.linalg.norm(coordinates[:, None] - coordinates[None, :], axis=2)
    spec = dict(costs=costs.tolist(), demands=[0, 1, 1, 1, 1], vehicle_capacity=2, vehicles=2)
    row = execute("route-optimize", spec)
    oracle = min(
        sum(costs[a, b] for route in ([0, *order[:2], 0], [0, *order[2:], 0]) for a, b in zip(route, route[1:]))
        for order in permutations(range(1, 5))
    )
    assert row["status"] == "optimal" and row["feasibility_checked"]
    assert row["total_cost"] == pytest.approx(oracle)
    assert sorted(n for route in row["routes"] for n in route["node_positions"][1:-1]) == [1, 2, 3, 4]
    assert all(route["load"] <= 2 for route in row["routes"])
    spec["vehicles"] = 1
    infeasible = execute("route-optimize", spec)
    assert infeasible["status"] == "infeasible" and infeasible["total_cost"] is None


@pytest.mark.parametrize(
    "changes", [{"demands": [0, 0, 1]}, {"costs": [[0, -1, 2], [1, 0, 1], [2, 1, 0]]}, {"vehicles": 0}]
)
def test_cvrp_malformed_or_nonpositive_demands_are_rejected(changes):
    spec = dict(costs=[[0, 1, 2], [1, 0, 1], [2, 1, 0]], demands=[0, 1, 1], vehicle_capacity=2, vehicles=1)
    spec.update(changes)
    with pytest.raises(ValueError):
        execute("route-optimize", spec)


def test_job_shop_has_known_optimum_and_independent_precedence_nonoverlap():
    jobs = [
        [dict(machine=0, duration=3), dict(machine=1, duration=2)],
        [dict(machine=1, duration=2), dict(machine=0, duration=1)],
    ]
    row = execute("schedule-optimize", dict(jobs=jobs))
    assert row["status"] == "optimal" and row["makespan"] == pytest.approx(5)
    operations = row["operations"]
    for job in (0, 1):
        tasks = [task for task in operations if task["job_position"] == job]
        assert tasks[0]["end"] <= tasks[1]["start"] + 1e-7
    for machine in (0, 1):
        tasks = sorted([task for task in operations if task["machine"] == machine], key=lambda task: task["start"])
        assert tasks[0]["end"] <= tasks[1]["start"] + 1e-7
    impossible = execute("schedule-optimize", dict(jobs=jobs, deadlines=[1, 1]))
    assert impossible["status"] == "infeasible" and impossible["operations"] == []


def test_job_release_time_and_duplicate_machine_visits_are_respected():
    jobs = [[dict(machine=0, duration=1), dict(machine=0, duration=2)]]
    row = execute("schedule-optimize", dict(jobs=jobs, release_times=[4]))
    assert row["makespan"] == pytest.approx(7)
    assert row["operations"][0]["start"] == pytest.approx(4)


def test_nsga2_actual_offspring_front_feasibility_objectives_and_reproducibility():
    spec = dict(
        lower=[0, 0],
        upper=[2, 2],
        objectives=[dict(linear=[0, 0], quadratic=[1, 1], bias=0), dict(linear=[-4, -4], quadratic=[1, 1], bias=8)],
        seed=4,
        population=16,
        generations=8,
        matrix=[[-1, -1]],
        constraint_upper=[-1],
    )
    row = execute("evolutionary-optimize", spec)
    assert row == execute("evolutionary-optimize", spec)
    X = np.asarray(row["solutions"])
    F = np.asarray(row["objectives"])
    assert len(X) >= 3 and (X.sum(axis=1) >= 1 - 1e-8).all()
    np.testing.assert_allclose(F, np.c_[(X**2).sum(axis=1), ((X - 2) ** 2).sum(axis=1)])
    for i in range(len(F)):
        assert not any(np.all(f <= F[i]) and np.any(f < F[i]) for j, f in enumerate(F) if j != i)
    assert not row["optimality_proven"] and row["objective_evaluations"] == 16 * 9
    spec.update(matrix=[[1, 1]], constraint_upper=[-1])
    assert execute("evolutionary-optimize", spec)["status"] == "no_feasible_solution_found"


def test_nondomination_constraints_and_crowding_match_small_oracle():
    F = np.array([[0, 2], [1, 1], [2, 0], [3, 3], [0, 0]])
    fronts = _fronts(F, np.array([0, 0, 0, 0, 1.0]))
    assert set(fronts[0]) == {0, 1, 2} and fronts[-1] == [4]
    crowd = _crowding(F, fronts[0])
    assert np.isinf(crowd[0]) and np.isinf(crowd[2]) and crowd[1] == 2


def test_stock_flow_matches_exact_decay_conservation_and_no_clipping():
    spec = dict(
        initial=[10, 0], stoichiometry=[[-1], [1]], rate_matrix=[[0.2, 0]], constant_rates=[0], duration=5, steps=20
    )
    row = execute("stock-flow-simulate", spec)
    np.testing.assert_allclose(row["final_stocks"], [10 * np.exp(-1), 10 * (1 - np.exp(-1))], rtol=1e-7)
    assert row["total_stock_conserved_by_structure"] and row["maximum_total_stock_deviation"] < 1e-8
    spec.update(rate_matrix=[[0, 0]], constant_rates=[10])
    with pytest.raises(ValueError):
        execute("stock-flow-simulate", spec)


def test_agent_simulation_population_zero_rate_extremes_and_seeded_interventions():
    spec = dict(
        agents=5,
        edges=[[0, 1], [1, 2], [2, 3], [3, 4]],
        initial_infected=[0],
        transmission_rate=0,
        recovery_rate=0,
        step_size=1,
        steps=10,
        seed=1,
    )
    row = execute("agent-simulate", spec)
    assert row["trajectory_counts_SIR"] == [[4, 1, 0]] * 11
    spec.update(transmission_rate=100, recovery_rate=0)
    outbreak = execute("agent-simulate", spec)
    assert outbreak == execute("agent-simulate", spec)
    assert outbreak["trajectory_counts_SIR"][1] == [3, 2, 0] and outbreak["trajectory_counts_SIR"][-1] == [0, 5, 0]
    assert all(sum(counts) == 5 for counts in outbreak["trajectory_counts_SIR"])
    spec["interventions"] = [dict(step=0, transmission_multiplier=0)]
    assert execute("agent-simulate", spec)["trajectory_counts_SIR"] == [[4, 1, 0]] * 11


def test_frozen_digital_twin_recovers_true_state_transition_without_holdout_feedback():
    rng = np.random.default_rng(54)
    n = 100
    control = rng.normal(size=n)
    state = np.empty(n)
    state[0] = 2
    for t in range(n - 1):
        state[t + 1] = 0.8 * state[t] + 0.4 * control[t] + 0.1
    frame = pd.DataFrame({"time": pd.date_range("2025", periods=n, freq="h"), "state": state, "control": control})
    spec = dict(time="time", states=["state"], inputs=["control"], train_rows=70, ridge=0)
    row = execute("digital-twin", spec, frame)
    assert row["transition"][0][0] == pytest.approx(0.8, abs=1e-12)
    assert row["control_matrix"][0][0] == pytest.approx(0.4, abs=1e-12)
    assert row["bias"][0] == pytest.approx(0.1, abs=1e-12) and row["rmse_by_state"][0] < 1e-12
    altered = frame.copy()
    altered.loc[70:, "state"] += 100
    other = execute("digital-twin", spec, altered)
    assert row["transition"] == other["transition"] and row["training_sha256"] == other["training_sha256"]
    assert other["rmse_by_state"][0] == pytest.approx(100)


def image_data(segmentation=False):
    rng = np.random.default_rng(14)
    images = []
    labels = []
    for i in range(24):
        if segmentation:
            mask = np.zeros((8, 8))
            mask[2:6, 2:6] = 1
            image = mask * 0.8 + rng.uniform(0, 0.1, (8, 8))
            labels.append(mask.tolist())
        else:
            label = i % 2
            image = rng.uniform(0, 0.1, (8, 8)) + 0.8 * label
            labels.append(label)
        images.append(image.tolist())
    return dict(images=images, labels=labels, train_images=16, seed=7, epochs=20, width=4, learning_rate=0.01)


def test_actual_conv2d_classifier_learns_and_holdout_labels_do_not_affect_weights():
    spec = image_data()
    row = execute("image-classification", spec)
    assert row["accuracy"] >= 0.875 and row["last_training_loss"] < row["first_training_loss"]
    assert np.asarray(row["confusion_matrix"]).sum() == 8
    changed = copy.deepcopy(spec)
    changed["labels"][16:] = [1 - v for v in spec["labels"][16:]]
    other = execute("image-classification", changed)
    assert (
        row["trained_weight_sha256"] == other["trained_weight_sha256"]
        and row["train_image_sha256"] == other["train_image_sha256"]
    )
    assert other["accuracy"] <= 0.125


@pytest.mark.parametrize("operation", ["image-segmentation", "image-detection"])
def test_actual_unet_training_segmentation_and_component_detection(operation):
    spec = image_data(True)
    spec["epochs"] = 40
    row = execute(operation, spec)
    assert row["dice"] > 0.7 and row["last_training_loss"] < row["first_training_loss"]
    assert not row["raw_pixels_or_text_exported"]
    if operation == "image-detection":
        assert row["recall"] > 0.75 and row["precision"] > 0.75 and row["target_object_count"] == 8


@pytest.mark.parametrize("change", ["duplicate", "group", "unknown_class", "out_of_range", "nan", "bad_shape"])
def test_vision_invalid_inputs_and_cross_split_leakage_fail_closed(change):
    spec = image_data()
    spec["epochs"] = 1
    if change == "duplicate":
        spec["images"][-1] = copy.deepcopy(spec["images"][0])
    elif change == "group":
        spec["sample_groups"] = [1] * 24
    elif change == "unknown_class":
        spec["labels"][-1] = 5
    elif change == "out_of_range":
        spec["images"][0][0][0] = 2
    elif change == "nan":
        spec["images"][0][0][0] = float("nan")
    else:
        spec["images"] = [[[0]]] * 24
    with pytest.raises(ValueError):
        execute("image-classification", spec)


def test_component_coordinates_iou_and_template_ocr_without_plaintext_export():
    mask = np.zeros((8, 8), bool)
    mask[1:3, 2:5] = True
    assert _boxes(mask, 2) == [[2, 1, 5, 3]] and _iou([2, 1, 5, 3], [2, 1, 5, 3]) == 1
    a = np.ones((7, 5))
    a[1:6, 1] = 0
    a[5, 1:4] = 0
    b = np.ones((7, 5))
    b[1, 1:4] = 0
    b[1:6, 2] = 0
    image = np.concatenate([a, np.ones((7, 2)), b, np.ones((7, 2)), a], axis=1)
    row = execute(
        "template-ocr",
        dict(image=image.tolist(), templates=[a.tolist(), b.tolist()], threshold=0.5, maximum_distance=0.2),
    )
    assert row["template_positions"] == [0, 1, 0] and row["unknown_glyphs"] == 0
    assert not row["recognized_plaintext_exported"] and not row["raw_pixels_exported"]
    ambiguous = execute("template-ocr", dict(image=a.tolist(), templates=[a.tolist(), a.tolist()], threshold=0.5))
    assert ambiguous["template_positions"] == [None]


def test_specification_only_operations_never_consume_implicit_raw_dataset(series):
    assert len(OPERATION_NAMES) == 32 and len(SPEC_OPERATIONS) == 10
    for operation in SPEC_OPERATIONS:
        with pytest.raises(ValueError):
            execute(operation, {}, series)


@pytest.mark.parametrize(
    "example",
    json.loads((Path(__file__).resolve().parents[1] / "examples/advanced/manifest.json").read_text()),
    ids=lambda row: row["request"],
)
def test_all_advanced_examples_execute_in_real_cli_handler(example, tmp_path, capsys):
    from energybaseline.cli import main

    root = Path(__file__).resolve().parents[1] / "examples/advanced"
    args = ["workbench", str(root / example["request"])]
    if "dataset" in example:
        args += ["--dataset", str(root / example["dataset"])]
    target = tmp_path / "result.json"
    assert main([*args, "--out", str(target)]) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(target.read_text())


def test_real_png_reader_binds_content_and_rejects_traversal_and_corrupt_images(tmp_path):
    from PIL import Image

    spec = image_data()
    spec["epochs"] = 2
    images = spec.pop("images")
    names = []
    for i, image in enumerate(images):
        name = f"{i}.png"
        Image.fromarray(np.rint(np.asarray(image) * 255).astype("uint8")).save(tmp_path / name)
        names.append(name)
    spec.update(image_files=names, image_root=str(tmp_path), image_size=[8, 8], color_mode="L")
    row = execute("image-classification", spec)
    assert len(row["source_file_sha256"]) == 24 and len(row["all_image_values_sha256"]) == 64
    original = spec["image_files"][0]
    spec["image_files"][0] = "../PRIVATE.png"
    with pytest.raises(ValueError) as error:
        execute("image-classification", spec)
    assert "PRIVATE" not in str(error.value)
    spec["image_files"][0] = original
    (tmp_path / original).write_text("PRIVATE-CORRUPT-IMAGE")
    with pytest.raises(ValueError) as error:
        execute("image-classification", spec)
    assert "PRIVATE" not in str(error.value)
