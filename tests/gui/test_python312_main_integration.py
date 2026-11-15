"""Main integration preserves explicit target roles without enabling energy heuristics globally."""

import logging

import pandas as pd
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from shiboken6 import isValid

from energybaseline.db.session import init_database, reset_engine
from energybaseline.gui.main_window import MainWindow, QtLogHandler
from energybaseline.gui.state import AppState


@pytest.mark.parametrize("domain", ["neutral", "energy"])
def test_explicit_target_survives_reload_and_collect(qapp, tmp_path, monkeypatch, domain):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "state.sqlite").as_posix())
    reset_engine()
    state = AppState()
    state.config["domain_pack"] = domain
    state.targets = ["chosen_output"]
    logger = logging.getLogger("energybaseline")
    handlers_before = {id(h) for h in logger.handlers if isinstance(h, QtLogHandler)}
    window = MainWindow(state, init_database())
    handler = window._log_handler
    assert handler in logger.handlers
    try:
        frame = pd.DataFrame(
            {
                "timestamp": pd.date_range("2024-01-01", periods=40, freq="D"),
                "Energy_kWh": range(100, 140),
                "chosen_output": range(40),
            }
        )
        page = window.setup_page
        page.set_dataset(frame, "synthetic", "manual", "a" * 64)
        assert page.checked_targets() == ["chosen_output"]
        for test_id, checkbox in page.tc_checks.items():
            assert not checkbox.isEnabled()
            assert checkbox.isChecked() == state.tcp.test_panels[test_id].enabled
        assert "autocorrelated" in page.tcp_conf_combo.toolTip()
        # Simulate stale schema target metadata without changing explicit checkboxes.
        for row in range(page.schema_tbl.rowCount()):
            name = page.schema_tbl.item(row, 0).text()
            if name in ("Energy_kWh", "chosen_output"):
                page.schema_tbl.cellWidget(row, 1).blockSignals(True)
                page.schema_tbl.cellWidget(row, 1).setCurrentText("target" if name == "Energy_kWh" else "predictor")
                page.schema_tbl.cellWidget(row, 1).blockSignals(False)
        page.collect()
        assert state.targets == state.dataset_schema.target_columns == ["chosen_output"]
        page.set_dataset(frame, "synthetic", "manual", "b" * 64)
        assert page.checked_targets() == ["chosen_output"]
        assert window.subs["setup"].windowType() == Qt.WindowType.SubWindow
        assert window.subs["setup"].windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    finally:
        window.close()
        assert handler not in logger.handlers
        assert {id(h) for h in logger.handlers if isinstance(h, QtLogHandler)} == handlers_before
        assert not window.analysis_page._poll_timer.isActive()
        assert not window.status_page._timer.isActive()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(window)
        reset_engine()
