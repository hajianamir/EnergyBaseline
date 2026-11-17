"""Real Qt and spawned-worker execution, invalidation, file guards and unchanged authorization."""

from __future__ import annotations

import json
import os

import pandas as pd
import pytest

from energybaseline.core.pool import get_pool_manager, shutdown_default_pool
from energybaseline.core.tcp import TestConfigPanel as Panel
from energybaseline.core.vsg import VariableSelectionGate
from energybaseline.gui.state import AppState
from energybaseline.gui.workbench import WorkbenchPanel


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "home"))
    state = AppState()
    state.df = pd.DataFrame({"time": pd.date_range("2025", periods=80, freq="D"), "y": range(80), "x": range(80)})
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


def _optimization():
    return {
        "version": 1,
        "operation": "linear-optimize",
        "parameters": {
            "objective": [-3, -2],
            "lower": [0, 0],
            "upper": [1, 1],
            "integrality": [1, 1],
            "matrix": [[2, 2]],
            "constraint_lower": [0],
            "constraint_upper": [3],
        },
    }


def test_real_qt_workbench_spawn_completion_and_no_gate_mutation(panel, qtbot, tmp_path):
    path = tmp_path / "job.json"
    path.write_text(json.dumps(_optimization()))
    assert panel.load_request(path, allowed_roots=[tmp_path])
    vsg, tcp, preregistration = panel.state.vsg, panel.state.tcp, panel.state.tcp.preregistration_sha256
    panel.run_button.click()
    handle = panel.handle
    assert handle is not None
    qtbot.waitUntil(lambda: panel.handle is None, timeout=120000)
    assert panel.result is not None, panel.status.text()
    assert panel.result["result"]["objective"] == -3
    assert handle.worker_pid and handle.worker_pid != os.getpid()
    assert panel.state.vsg is vsg and panel.state.tcp is tcp
    assert tcp.preregistration_sha256 == preregistration and tcp.is_locked and vsg.is_accepted()
    assert json.loads(panel.output.toPlainText())["operation"] == "linear-optimize"
    panel.editor.setPlainText("{}")
    assert panel.result is None and not panel.output.toPlainText()


def test_gui_reload_cancels_stale_result_and_clears_reference(panel, qtbot):
    panel.editor.setPlainText(json.dumps(_optimization()))
    panel.run_job()
    assert panel.handle is not None
    panel.reference = pd.DataFrame({"private": ["must-clear"]})
    panel.state.data_changed.emit()
    qtbot.waitUntil(lambda: panel.handle is None, timeout=120000)
    assert panel.result is None and panel.reference is None
    assert not panel.editor.toPlainText() and not panel.output.toPlainText()


def test_gui_path_guards_bad_job_and_reference_do_not_retain_evidence(panel, tmp_path):
    folder = tmp_path / "allowed"
    folder.mkdir()
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps(_optimization()))
    panel.result = {"stale": True}
    panel.output.setPlainText("stale")
    assert not panel.load_request(outside, allowed_roots=[folder])
    assert panel.result is None and not panel.output.toPlainText()
    assert not panel.load_reference(outside, allowed_roots=[folder])
    bad = folder / "bad.json"
    bad.write_text("not json PRIVATE-VALUE")
    assert not panel.load_request(bad, allowed_roots=[folder])
    assert "PRIVATE-VALUE" not in panel.status.text()
    panel.editor.setPlainText("[]")
    panel.run_job()
    assert panel.handle is None and panel.result is None
    assert get_pool_manager() is not None
