"""Main integration preserves explicit target roles without enabling energy heuristics globally."""

import pandas as pd
import pytest
from PySide6.QtCore import Qt

from energybaseline.db.session import init_database, reset_engine
from energybaseline.gui.main_window import MainWindow
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
    window = MainWindow(state, init_database())
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
        assert window.subs["setup"].windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    finally:
        window.close()
        reset_engine()
