"""Actual isolated native worker execution for the Stage 6 catalog additions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energybaseline.core.pool import shutdown_default_pool
from energybaseline.core.tcp import TestConfigPanel as Panel
from energybaseline.core.vsg import VariableSelectionGate
from energybaseline.gui.state import AppState
from energybaseline.gui.workbench import WorkbenchPanel

ROOT = Path(__file__).resolve().parents[2] / "examples/catalog"
MANIFEST = json.loads((ROOT / "manifest.json").read_text())


@pytest.mark.parametrize("example", MANIFEST, ids=[x["request"] for x in MANIFEST])
def test_real_catalog_workers_preserve_input_bound_gate_and_clear_stale_evidence(example, qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "home"))
    state = AppState()
    state.df = pd.read_csv(ROOT / example["dataset"])
    if "time" not in state.df:
        state.df["time"] = pd.date_range("2025", periods=len(state.df), freq="h")
    state.df["time"] = pd.to_datetime(state.df.time)
    state.df["gate_target"] = np.arange(len(state.df), dtype=float) + 1
    state.vsg = VariableSelectionGate(state.df, targets=["gate_target"], time_col="time")
    state.vsg.accept()
    state.tcp = Panel(state.vsg)
    state.tcp.lock_preregistration()
    vsg, tcp, fingerprint = state.vsg, state.tcp, state.tcp.preregistration_sha256
    widget = WorkbenchPanel(state)
    qtbot.addWidget(widget)
    widget.show()
    try:
        assert widget.load_request(ROOT / example["request"], allowed_roots=[ROOT])
        widget.run_button.click()
        handle = widget.handle
        assert handle is not None
        qtbot.waitUntil(lambda: widget.handle is None, timeout=120000)
        assert widget.result is not None, widget.status.text()
        assert handle.worker_pid and handle.worker_pid != os.getpid()
        result = widget.result["result"]
        assert not result["baseline_changed"] and not result["p_values_or_confidence_intervals_computed"]
        assert state.vsg is vsg and state.tcp is tcp and tcp.preregistration_sha256 == fingerprint
        if "accuracy" in result:
            assert result["accuracy"] == 1.0
        if "test_accuracy" in result:
            assert result["test_accuracy"] == 1.0
        if "holdout_harrell_concordance" in result:
            assert result["holdout_harrell_concordance"] > 0.7
        widget.editor.setPlainText("{}")
        assert widget.result is None and not widget.output.toPlainText()
    finally:
        widget.cancel()
        receipt = shutdown_default_pool(join_timeout=15)
        assert receipt["orphan_pids"] == []
        widget.close()
