"""Native Qt regression and real desktop-to-spawned-worker authorization proofs."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from PySide6.QtWidgets import QLabel

from energybaseline.gui.main_window import MainWindow
from energybaseline.gui.state import AppState
from energybaseline.core import store
from energybaseline.core.ingest import sha256_dataframe
from energybaseline.db import session


def test_neutral_schema_target_controls_state_and_gate_stay_aligned(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_HOME", str(tmp_path))
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    session.reset_engine()
    window = MainWindow(AppState(), session.init_database())
    qtbot.addWidget(window)
    rng = np.random.default_rng(77)
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2020-01-01", periods=48, freq="MS"),
            "quality_offset": rng.normal(-8, 1, 48),
            "process_setting": rng.normal(size=48),
        }
    )
    window.setup_page.set_dataset(frame, "neutral.csv", "manual", sha256_dataframe(frame))
    assert window.state.targets == ["quality_offset"]
    assert window.setup_page.checked_targets() == window.state.targets
    assert window.state.dataset_schema.target_columns == window.state.targets
    assert list(window.state.vsg.targets) == window.state.targets
    assert window.setup_page.impact_preview.last_preview["total_tests"] > 0
    schema = window.setup_page.build_schema_from_ui()
    assert schema.get_column("quality_offset").quantity_type.value == "quality"
    assert store.load_dataset_schema(schema_id=window.setup_page.persist_schema_to_db()).target_columns == [
        "quality_offset"
    ]


def test_desktop_dispatches_input_bound_inference_to_actual_spawn_worker(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_HOME", str(tmp_path))
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    session.reset_engine()
    window = MainWindow(AppState(), session.init_database())
    qtbot.addWidget(window)
    window.state.config.update(
        {
            "domain_pack": "manufacturing",
            "seed": 42,
            "model_families": ["linear"],
            "hpo_trials": 0,
            "enable_nn": False,
            "enable_mining": False,
            "include_importance": False,
            "include_eda_attachment": False,
            "lags": [],
            "rolling_windows": [],
        }
    )
    rng = np.random.default_rng(531)
    x = rng.normal(size=72)
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2010-01-01", periods=72, freq="MS"),
            "quality_offset": -8 + x + rng.normal(0, 0.5, 72),
            "process_setting": x,
        }
    )
    window.setup_page.set_dataset(frame, "manufacturing.csv", "manual", sha256_dataframe(frame))
    window.setup_page.collect()
    window.setup_page.rebuild_compliance_models()
    assert window.setup_page.accept_vsg_gate()
    assert len(window.setup_page.accept_and_lock_tcp()) == 64
    window.analysis_page.start()
    assert window.analysis_page._job_handle is not None
    qtbot.waitUntil(lambda: window.state.run is not None or window.analysis_page._job_handle is None, timeout=180000)
    assert window.state.run is not None, window.analysis_page.status.text()
    target = window.state.run.targets["quality_offset"]
    assert target.status == "ok", target.error
    assert target.validation_contract["evaluator"] == "raw-fold-training-v1"
    assert any(row["p_raw"] is not None for row in target.test_catalog)
    assert all(v is None for v in target.thresholds.values())
    assert window.state.run_id is not None
    assert store.load_run(window.state.run_id).targets["quality_offset"].status == "ok"


@pytest.mark.parametrize("fidelity", [0.81234, 0.97321])
def test_native_surrogate_card_renders_the_actual_artifact_value(qtbot, monkeypatch, fidelity):
    from types import SimpleNamespace
    from energybaseline.gui.pages_results import SelectionPage

    # A bounded UI fixture, not evidence of fitting a real model. Real fits and
    # worker transport are exercised above and in the original GUI suite.
    state = AppState()
    state.run_id = None
    row = {
        "rank": 1,
        "name": "test",
        "family": "kernel",
        "score": 70.0,
        "n_params": 10,
        "metrics": {"r2": 0.8, "cvrmse": 12.0, "nmbe": 1.0, "mae": 2.0, "mape": 3.0, "smape": 4.0},
        "uncertainty": {"lower": -1.0, "upper": 1.0, "basis": "empirical residual quantiles"},
        "reason": "fixture",
        "failures": [],
        "formula": {
            "kind": "ensemble/kernel",
            "text": "Executable kernel architecture; approximation is diagnostic only.",
            "surrogate": {"surrogate_banner": "APPROXIMATE_DIAGNOSTIC_SURROGATE", "fidelity_r2": fidelity},
        },
    }
    target = SimpleNamespace(
        status="ok",
        notice="",
        shortlist=[row],
        register=[],
        qualified_not_shortlisted_register=[],
        separated_registers={},
    )
    state.run = SimpleNamespace(targets={"quality_offset": target})
    page = SelectionPage(state, SimpleNamespace(log_msg=lambda *args: None))
    qtbot.addWidget(page)
    state.run_changed.emit()
    labels = " | ".join(label.text() for label in page.candidate_cards["quality_offset"][0].findChildren(QLabel))
    assert f"{fidelity:.3f}" in labels
    assert "NOT model formula" in labels
    assert "APPROXIMATE_DIAGNOSTIC_SURROGATE" in labels
