"""The workbench panel explains missing engine provisioning without retaining evidence."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from energybaseline.core.pool import shutdown_default_pool
from energybaseline.gui.state import AppState
from energybaseline.gui.workbench import WorkbenchPanel

EXAMPLES = Path(__file__).resolve().parents[2] / "examples/isolated"


@pytest.mark.parametrize(
    ("request_name", "guidance"),
    [
        ("automl-h2o.json", "engines install h2o"),
        ("chronos-bolt.json", "assets install chronos-bolt-tiny"),
        ("ghostnet.json", "assets install ghostnet-imagenet"),
    ],
)
def test_panel_reports_a_missing_runtime_without_evidence(request_name, guidance, qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "empty-home"))
    state = AppState()
    state.df = pd.read_csv(EXAMPLES / "series.csv")
    state.df["time"] = pd.to_datetime(state.df["time"])
    widget = WorkbenchPanel(state)
    qtbot.addWidget(widget)
    try:
        notice = widget.notice.text()
        assert "examples/isolated" in notice and "separately provisioned" in notice
        assert widget.load_request(EXAMPLES / request_name, allowed_roots=[EXAMPLES])
        widget.run_button.click()
        qtbot.waitUntil(lambda: widget.handle is None, timeout=120000)
        assert widget.result is None and not widget.output.toPlainText()
        assert "Separate provisioning is required" in widget.status.text()
        assert guidance in widget.status.text() and "baseline unchanged" in widget.status.text()
    finally:
        widget.cancel()
        receipt = shutdown_default_pool(join_timeout=15)
        assert receipt["orphan_pids"] == []
        widget.close()
