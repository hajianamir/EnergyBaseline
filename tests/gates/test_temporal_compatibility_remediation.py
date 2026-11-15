"""Keep compatibility preprocessing safe without silently replacing its API."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression

from energybaseline.pipeline import InFoldModelPipeline
from energybaseline.validation import InFoldPreprocessor
from energybaseline.validation.ashrae14_metrics import compute_ashrae14_oos_metrics


@pytest.mark.parametrize("memory", [[-1], [0], [True], [1.5], [float("inf")]])
@pytest.mark.parametrize("setting", ["lags", "rolling_windows"])
def test_invalid_memory_is_rejected_in_both_public_compatibility_paths(memory, setting):
    with pytest.raises(ValueError):
        InFoldPreprocessor(**{setting: memory})
    with pytest.raises(ValueError):
        InFoldModelPipeline(LinearRegression, **{setting: memory})


def test_rolling_training_uses_real_history_not_compressed_observation_positions():
    history = pd.DataFrame({"x": np.arange(50, dtype=float)})
    train = history.iloc[20:40]
    prep = InFoldPreprocessor(scale=False, enforce_k_max=False, selection_method="none", lags=[1], lag_cols=["x"])
    transformed = prep.fit_transform(train, np.arange(20), history=history.iloc[:40])
    assert transformed.iloc[0]["x_lag1"] == 19
    assert transformed.iloc[-1]["x_lag1"] == 38
    assert prep.medians_["x_lag1"] == 28.5
    changed = train.copy()
    changed.loc[25, "x"] = -100
    with pytest.raises(ValueError, match="exact training"):
        prep.fit(changed, np.arange(20), history=history)
    with pytest.raises(ValueError, match="observation order"):
        prep.fit(train.iloc[::-1], np.arange(20), history=history)


def test_preprocessing_audit_fingerprint_covers_middle_predictor_values():
    frame = pd.DataFrame({"x": np.arange(50, dtype=float)})
    original = InFoldPreprocessor().fit(frame, np.arange(50)).summary().train_fingerprint_sha256
    frame.loc[25, "x"] = -77
    changed = InFoldPreprocessor().fit(frame, np.arange(50)).summary().train_fingerprint_sha256
    assert changed != original


@pytest.mark.parametrize("interval", ["daily", "weekly", "subhourly", "unknown"])
def test_compatibility_metrics_do_not_certify_unrecognized_standard_intervals(interval):
    y = np.arange(40, dtype=float) + 100
    metrics = compute_ashrae14_oos_metrics(y, y, granularity=interval)
    assert metrics.r2 == 1
    assert not metrics.meets_ashrae14
    assert not metrics.standard_thresholds_applicable
    assert metrics.to_dict()["threshold_policy_version"] == 2


def test_constant_target_cannot_pass_standard_via_undefined_r2():
    metrics = compute_ashrae14_oos_metrics(np.ones(40), np.ones(40), granularity="monthly")
    assert not metrics.meets_ashrae14
