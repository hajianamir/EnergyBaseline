"""Real native panels and spawn-worker algorithms across all advanced capability families."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from energybaseline.core.pool import shutdown_default_pool
from energybaseline.core.tcp import TestConfigPanel as Panel
from energybaseline.core.vsg import VariableSelectionGate
from energybaseline.gui.state import AppState
from energybaseline.gui.workbench import WorkbenchPanel

ROOT = Path(__file__).resolve().parents[2] / "examples/advanced"


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "home"))
    state = AppState()
    state.df = pd.read_csv(ROOT / "series.csv")
    state.df["time"] = pd.to_datetime(state.df.time)
    state.vsg = VariableSelectionGate(state.df, targets=["y"], time_col="time")
    state.vsg.accept()
    state.tcp = Panel(state.vsg)
    state.tcp.lock_preregistration()
    widget = WorkbenchPanel(state)
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    widget.cancel()
    receipt = shutdown_default_pool(join_timeout=15)
    assert receipt["orphan_pids"] == []
    widget.close()


@pytest.mark.parametrize(
    "example",
    [
        "automl-search",
        "forecast-informer",
        "image-classification",
        "image-classification-files",
        "route-optimize",
        "schedule-optimize",
        "stock-flow",
        "agent-sir",
        "digital-twin",
    ],
)
def test_actual_native_advanced_algorithms_are_reachable_without_baseline_mutation(example, panel, qtbot):
    assert panel.load_request(ROOT / (example + ".json"), allowed_roots=[ROOT])
    vsg, tcp, fingerprint = panel.state.vsg, panel.state.tcp, panel.state.tcp.preregistration_sha256
    panel.run_button.click()
    handle = panel.handle
    assert handle is not None
    qtbot.waitUntil(lambda: panel.handle is None, timeout=120000)
    assert panel.result is not None, panel.status.text()
    assert handle.worker_pid and handle.worker_pid != os.getpid()
    assert panel.result["operation"] == json.loads((ROOT / (example + ".json")).read_text())["operation"]
    assert panel.state.vsg is vsg and panel.state.tcp is tcp and tcp.preregistration_sha256 == fingerprint
    assert not panel.result["result"].get("baseline_changed", False)
    panel.editor.setPlainText("{}")
    assert panel.result is None and not panel.output.toPlainText()
