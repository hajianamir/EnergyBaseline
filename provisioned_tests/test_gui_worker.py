"""The native workbench panel runs provisioned engines in its spawn worker, bound to the inputs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from energybaseline.core.pool import shutdown_default_pool
from energybaseline.gui.state import AppState
from energybaseline.gui.workbench import WorkbenchPanel

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/isolated"


@pytest.mark.parametrize("request_name", ["automl-autogluon.json", "chronos-bolt.json", "ghostnet.json"])
def test_panel_runs_provisioned_engines_in_a_worker_and_clears_stale_evidence(request_name, qtbot):
    state = AppState()
    state.df = pd.read_csv(EXAMPLES / "series.csv")
    state.df["time"] = pd.to_datetime(state.df["time"])
    widget = WorkbenchPanel(state)
    qtbot.addWidget(widget)
    widget.show()
    try:
        assert widget.load_request(EXAMPLES / request_name, allowed_roots=[EXAMPLES])
        if request_name == "ghostnet.json":
            # Image jobs name files relative to the working directory of the application.
            request = json.loads(widget.editor.toPlainText())
            request["parameters"]["image_root"] = str(EXAMPLES / "images")
            widget.editor.setPlainText(json.dumps(request))
        widget.run_button.click()
        handle = widget.handle
        assert handle is not None
        qtbot.waitUntil(lambda: widget.handle is None, timeout=300000)
        assert widget.result is not None, widget.status.text()
        assert handle.worker_pid and handle.worker_pid != os.getpid()
        result = widget.result["result"]
        assert result["baseline_changed"] is False
        if request_name == "automl-autogluon.json":
            assert (
                result["engine"] == "autogluon" and result["test_rmse"] < 0.6 * result["fit_mean_reference_test_rmse"]
            )
            assert widget.result["input_sha256"] is not None
        elif request_name == "chronos-bolt.json":
            assert result["engine"] == "chronos" and result["mae_ratio_to_last_value"] < 0.4
        else:
            assert result["images"][1]["top"][0]["label"] == "espresso" and widget.result["input_sha256"] is None
        assert "Completed" in widget.status.text()
        widget.editor.setPlainText("{}")
        assert widget.result is None and not widget.output.toPlainText()
    finally:
        widget.cancel()
        receipt = shutdown_default_pool(join_timeout=15)
        assert receipt["orphan_pids"] == []
        widget.close()
