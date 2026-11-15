"""Accepted inference is bound to the input frame and locked specification."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energybaseline.core.authorization import assert_bound_inference
from energybaseline.core.tcp import TestConfigPanel as Panel
from energybaseline.core.vsg import VariableSelectionGate, ComplianceGateError


def gates():
    rng = np.random.default_rng(632)
    x = rng.normal(size=48)
    df = pd.DataFrame(
        {
            "time": pd.date_range("2020-01-01", periods=48, freq="MS"),
            "setting": x,
            "target": 30 + x + rng.normal(size=48),
        }
    )
    vsg = VariableSelectionGate(df, targets=["target"], time_col="time")
    vsg.accept()
    tcp = Panel(vsg)
    tcp.lock_preregistration()
    return df, vsg, tcp


def test_unchanged_copy_is_authorized():
    df, vsg, tcp = gates()
    assert_bound_inference(df.copy(), "time", ["target"], vsg, tcp)


@pytest.mark.parametrize("change", ["target", "setting", "time", "dtype"])
def test_changed_inputs_cannot_reuse_a_previously_accepted_gate(change):
    df, vsg, tcp = gates()
    altered = df.copy()
    if change == "time":
        altered[change] += pd.Timedelta(days=1)
    elif change == "dtype":
        altered["setting"] = altered["setting"].astype("float32")
    else:
        altered.loc[0, change] += 100
    with pytest.raises(ComplianceGateError, match="input"):
        assert_bound_inference(altered, "time", ["target"], vsg, tcp)


def test_direct_specification_mutation_cannot_bypass_preregistration():
    df, vsg, tcp = gates()
    tcp.alpha = 0.001
    with pytest.raises(ComplianceGateError, match="specification has changed"):
        assert_bound_inference(df, "time", ["target"], vsg, tcp)


def test_real_spawn_worker_receives_and_checks_locked_authorization(tmp_path):
    from energybaseline.core.pool import AnalysisPoolManager

    df, vsg, tcp = gates()
    manager = AnalysisPoolManager(max_tasks=1, cache_dir=tmp_path / "cache")
    try:
        handle = manager.submit_analysis(
            df=df,
            time_col="time",
            targets=["target"],
            vsg=vsg,
            tcp=tcp,
            threshold_bytes=1,
            config={
                "domain_pack": "neutral",
                "model_families": ["linear"],
                "hpo_trials": 0,
                "enable_nn": False,
                "enable_mining": False,
                "include_importance": False,
                "include_eda_attachment": False,
            },
        )
        run, _ = handle.wait(timeout=150)
        result = run.targets["target"]
        assert result.status == "ok", result.error
        assert result.validation_contract["evaluator"] == "raw-fold-training-v1"
        assert any(item.get("p_raw") is not None for item in result.test_catalog)
    finally:
        manager.shutdown_cleanly(join_timeout=5.0)
