"""Actual training-only transformations, nested fits and raw validation population."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression, Ridge

from energybaseline.core.evaluation import RawEvaluationPlan
from energybaseline.core.features import FeatureSpec, validate_memory
from energybaseline.core.models import Candidate, ColsDesign, Ctx, evaluate_candidate
from energybaseline.core.temporal import granularity


def data():
    rng = np.random.default_rng(361)
    t = rng.normal(15, 10, 120)
    x = rng.normal(size=120)
    frame = pd.DataFrame(
        {
            "temperature_C": t,
            "setting": x,
            "category": np.where(np.arange(120) % 2, "A", "B"),
            "target": 30 + 2 * x + np.maximum(0, 12 - t) + rng.normal(0, 3, 120),
        },
        index=pd.date_range("2010-01-01", periods=120, freq="MS"),
    )
    frame.loc[frame.index[85:], "category"] = "VALIDATION_ONLY"
    frame.loc[frame.index[[4, 6, 22, 90]], "setting"] = np.nan
    return frame


def plan(frame, **extra):
    cfg = dict(
        domain_pack="energy",
        outlier_action_policy="flag_only",
        selection_method="correlation",
        max_features=4,
        lags=[1, 3],
        rolling_windows=[3],
    )
    cfg.update(extra)
    return RawEvaluationPlan(frame, "target", ["target"], granularity("monthly"), cfg, 41)


def test_heldout_labels_do_not_affect_quality_features_knots_or_predictions():
    frame = data()
    train, valid = np.arange(80), np.arange(85, 120)
    first = plan(frame).prepare(train, valid)
    altered = frame.copy()
    altered.loc[altered.index[valid], "target"] = np.linspace(-1e9, 1e9, len(valid))
    second = plan(altered).prepare(train, valid)
    assert first.audit == second.audit
    pd.testing.assert_frame_equal(first.train_X, second.train_X)
    pd.testing.assert_frame_equal(first.valid_X, second.valid_X)
    assert all("VALIDATION_ONLY" not in levels for levels in first.audit["category_levels"].values())
    a = LinearRegression().fit(first.train_X, first.train_y).predict(first.valid_X)
    b = LinearRegression().fit(second.train_X, second.train_y).predict(second.valid_X)
    np.testing.assert_allclose(a, b)


def test_raw_validation_labels_are_not_filtered_as_outliers():
    frame = data()
    frame.loc[frame.index[-1], "target"] = 1e8
    evaluation = plan(frame, outlier_method="iqr", outlier_treatment="remove", outlier_action_policy="auto_apply")
    fold = evaluation.prepare(np.arange(80), np.arange(85, 120))
    assert len(evaluation.y) == len(frame)
    assert len(fold.valid_X) == 35 and evaluation.y[-1] == 1e8
    assert fold.audit["n_valid"] == 35


def test_nested_hpo_uses_raw_inner_folds_and_exact_purge():
    evaluation = plan(data(), domain_pack="neutral")
    ctx = Ctx(target="target", seed=42, hpo_trials=2, gap=3, cv_scheme="forward_chaining", evaluation_plan=evaluation)
    candidate = Candidate(
        "Ridge test",
        "Linear",
        1,
        "linear",
        lambda c: ColsDesign(),
        lambda p, c: Ridge(**p),
        space=lambda trial: {"alpha": trial.suggest_float("alpha", 0.1, 2.0)},
    )
    outer = evaluation.prepare(np.arange(90), np.arange(95, 120))
    fit = evaluation.fit(candidate, outer.train_X, outer.train_y, ctx, np.arange(90))
    assert "independently prepared inner folds" in fit.hpo_note
    assert np.isfinite(fit.predict(outer.valid_X)).all()
    inner = list(evaluation.inner_folds(np.arange(90), ctx))
    for fold in inner:
        assert fold.train_positions.max() + ctx.gap < fold.valid_positions.min()
        assert max(fold.train_positions) < 90 and max(fold.valid_positions) < 90


def test_evaluator_metrics_cover_raw_labels_not_final_training_filter():
    frame = data().drop(columns="category")
    evaluation = plan(frame, domain_pack="neutral", selection_method="none")
    evaluation.folds = [(np.arange(60), np.arange(70, 90)), (np.arange(90), np.arange(100, 120))]
    # Deployment training may exclude observations; OOF population must not shrink.
    final_index = frame.index[:-5]
    X = frame.loc[final_index, ["setting"]].fillna(0.0)
    y = frame.loc[final_index, "target"].to_numpy()
    ctx = Ctx(target="target", seed=42, hpo_trials=0, gap=3, evaluation_plan=evaluation, energy_policy=False)
    candidate = Candidate("Linear", "Linear", 1, "linear", lambda c: ColsDesign(), lambda p, c: LinearRegression())
    result = evaluate_candidate(candidate, X, y, [], ctx)
    assert result["metrics"]["n"] == 40
    assert len(result["oof"]) == len(final_index)
    assert result["metrics"]["rmse"] > 0


@pytest.mark.parametrize("values", [[-1], [0], [1.5], [True], [float("inf")]])
def test_future_or_invalid_lags_are_rejected(values):
    with pytest.raises(ValueError):
        validate_memory(values, setting="lags")
    spec = FeatureSpec(time_aware=True, raw_cols=["setting"], lag_cols=["setting"], lags=values)
    with pytest.raises(ValueError):
        spec.build(data())


def test_cli_uses_the_neutral_sdk_evaluator_without_standard_defaults(tmp_path):
    import json
    import os
    import subprocess
    import sys

    frame = data().iloc[:48].drop(columns="category").reset_index(names="timestamp")
    source = tmp_path / "process.csv"
    frame.to_csv(source, index=False)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "model_families": ["linear"],
                "hpo_trials": 0,
                "enable_mining": False,
                "enable_nn": False,
                "include_importance": False,
                "include_eda_attachment": False,
            }
        )
    )
    command = [
        sys.executable,
        "-m",
        "energybaseline.cli",
        "analyze",
        str(source),
        "--time-col",
        "timestamp",
        "--targets",
        "target",
        "--config",
        str(config),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=90, env={**os.environ, "OMP_NUM_THREADS": "2"}
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    result = payload["targets"]["target"]
    assert payload["domain_policy"]["key"] == "neutral"
    assert result["validation_contract"]["evaluator"] == "raw-fold-training-v1"
    assert all(value is None for value in result["thresholds"].values())


def test_ensemble_outer_prediction_does_not_use_its_validation_labels():
    evaluation = plan(data(), domain_pack="neutral", selection_method="none")
    evaluation.folds = [(np.arange(60), np.arange(70, 90)), (np.arange(90), np.arange(100, 120))]
    ctx = Ctx(target="target", seed=41, gap=3, cv_scheme="forward_chaining", hpo_trials=0)
    first = Candidate("OLS", "Linear", 1, "linear", lambda c: ColsDesign(), lambda p, c: LinearRegression())
    second = Candidate("Ridge", "Linear", 1, "linear", lambda c: ColsDesign(), lambda p, c: Ridge())
    before, weights = evaluation.ensemble_predictions(first, second, ctx)
    altered = data()
    altered.loc[altered.index[70:90], "target"] = 1e9
    changed = plan(altered, domain_pack="neutral", selection_method="none")
    changed.folds = evaluation.folds
    after, _ = changed.ensemble_predictions(first, second, ctx)
    np.testing.assert_allclose(before[70:90], after[70:90])
    assert np.isfinite(before[100:120]).all()
    assert all(np.isclose(weight.sum(), 1.0) and (weight >= 0).all() for weight in weights)


def test_persisted_raw_validation_trace_reproduces_shortlist_metrics():
    from energybaseline.sdk import analyze
    from energybaseline.core.models import metrics

    frame = data().iloc[:48].drop(columns="category").reset_index(names="timestamp")
    frame.loc[len(frame) - 1, "target"] = 1e8
    run, _ = analyze(
        frame,
        "timestamp",
        ["target"],
        {
            "model_families": ["linear"],
            "hpo_trials": 0,
            "enable_mining": False,
            "enable_nn": False,
            "include_importance": False,
            "include_eda_attachment": False,
            "outlier_method": "iqr",
            "outlier_threshold": 1.5,
            "outlier_treatment": "remove",
            "outlier_action_policy": "auto_apply",
        },
    )
    target = run.targets["target"]
    assert target.status == "ok", target.error
    assert target.n_obs < target.validation_contract["n_evaluation_population"]
    trace = target.validation_series
    assert len(trace) == len(frame)
    for row in target.shortlist:
        valid = trace[row["name"]].notna()
        observed = metrics(trace.loc[valid, "actual"], trace.loc[valid, row["name"]])
        assert observed["n"] == row["metrics"]["n"]
        assert observed["rmse"] == pytest.approx(row["metrics"]["rmse"])


def test_integer_predictors_accept_fractional_training_only_outlier_treatment():
    frame = data().drop(columns="category")
    frame["setting"] = np.arange(len(frame), dtype=int) % 13
    frame.loc[frame.index[55], "setting"] = 10000
    evaluation = plan(
        frame,
        domain_pack="neutral",
        outlier_method="iqr",
        outlier_threshold=1.5,
        outlier_treatment="winsorize",
        outlier_action_policy="auto_apply",
    )
    fold = evaluation.prepare(np.arange(80), np.arange(85, 120))
    assert np.isfinite(fold.train_X.to_numpy()).all()
    assert np.isfinite(fold.valid_X.to_numpy()).all()
