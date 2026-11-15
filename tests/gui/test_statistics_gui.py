"""Actual native Qt registration and spawned statistical execution, not mocked worker results."""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from energybaseline.core.pool import shutdown_default_pool
from energybaseline.core.tcp import TestConfigPanel as Panel
from energybaseline.core.vsg import VariableSelectionGate
from energybaseline.gui.state import AppState
from energybaseline.gui.statistics import StatisticalPlanPanel


def plan():
    return {
        "version": 1,
        "time": "time",
        "targets": ["y"],
        "metadata": [],
        "sampling": "independent",
        "assumption_justification": "Synthetic independent normal observations for native integration.",
        "correction": "tcp",
        "tests": [{"id": "H1", "method": "one_sample_t", "columns": ["y"], "parameters": {"null": 0}}],
    }


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "home"))
    state = AppState()
    state.df = pd.DataFrame(
        {"time": pd.date_range("2025", periods=80, freq="h"), "y": np.random.default_rng(5).normal(1, 1, 80)}
    )
    state.vsg = VariableSelectionGate(state.df, targets=["y"], time_col="time")
    state.vsg.accept()
    state.tcp = Panel(state.vsg)
    widget = StatisticalPlanPanel(state)
    qtbot.addWidget(widget)
    widget.show()
    widget.editor.setPlainText(json.dumps(plan()))
    yield widget
    widget.cancel()
    receipt = shutdown_default_pool(join_timeout=15)
    assert receipt["orphan_pids"] == []
    widget.close()


def test_actual_native_registration_explicit_lock_worker_execution_and_invalidation(panel, qtbot):
    panel.run_job()
    assert panel.handle is None and panel.result is None
    assert panel.register_plan() and not panel.state.tcp.is_locked
    panel.run_job()
    assert panel.handle is None
    fingerprint = panel.state.tcp.lock_preregistration()
    panel.run_button.click()
    handle = panel.handle
    assert handle is not None
    qtbot.waitUntil(lambda: panel.handle is None, timeout=120000)
    assert panel.result is not None, panel.status.text()
    assert handle.worker_pid and handle.worker_pid != os.getpid()
    assert panel.result["preregistration_sha256"] == fingerprint
    assert panel.result["frequentist"][0]["p_input"] < 0.001
    assert panel.state.tcp.preregistration_sha256 == fingerprint
    assert not panel.register_plan() and panel.result is None
    assert "amendment" in panel.status.text()


def test_actual_worker_result_cannot_survive_unannounced_gate_amendment(panel, qtbot):
    assert panel.register_plan()
    panel.state.tcp.lock_preregistration()
    panel.run_job()
    assert panel.handle is not None
    panel.state.tcp.unlock_for_amendment(reason="Actual native stale-result test amendment")
    qtbot.waitUntil(lambda: panel.handle is None, timeout=120000)
    assert panel.result is None and not panel.output.toPlainText()


def test_plan_edits_and_config_changes_invalidate_existing_or_inflight_evidence(panel, qtbot):
    assert panel.register_plan()
    panel.state.tcp.lock_preregistration()
    panel.run_job()
    panel.state.config_changed.emit()
    qtbot.waitUntil(lambda: panel.handle is None, timeout=120000)
    assert panel.result is None and not panel.output.toPlainText()
    revised = plan()
    revised["tests"][0]["parameters"]["null"] = 1
    panel.editor.setPlainText(json.dumps(revised))
    panel.run_job()
    assert panel.handle is None


def test_registration_rejects_missing_or_unaccepted_gates_and_private_bad_json(panel):
    panel.state.vsg = None
    assert not panel.register_plan()
    panel.editor.setPlainText("PRIVATE-SECRET")
    assert not panel.register_plan() and "PRIVATE" not in panel.status.text()
    panel.run_job()
    assert panel.handle is None
