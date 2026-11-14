"""Desktop snapshots visibly reject missing/tampered baseline bindings."""

from __future__ import annotations

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QWidget, QMessageBox

from energybaseline.gui.page_monitor import MonitorPage
from energybaseline.gui.state import AppState
from energybaseline.core import store


class Main(QWidget):
    def log_msg(self, message):
        self.message = message


def test_gui_snapshot_requires_version_binding(qapp, monkeypatch):
    state = AppState()
    main = Main()
    page = MonitorPage(state, main)
    page.results = {
        "energy": pd.DataFrame(
            {
                "actual": [10.0, 11.0],
                "baseline": [10.0, 10.0],
                "deviation": [0.0, 1.0],
                "deviation_pct": [0.0, 10.0],
                "exceedance": [False, True],
            },
            index=pd.date_range("2025-01-01", periods=2),
        )
    }
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("unbound", True))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    # Uses the real persistence guard; an unbound result must not create a snapshot.
    page.save_snapshot()
    assert warnings and "locked baseline" in warnings[0]
    assert not hasattr(main, "message")
    page.close()
    main.close()


def test_gui_loaded_snapshot_displays_baseline_and_input_hash(qapp, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    state = AppState()
    main = Main()
    page = MonitorPage(state, main)
    # UI-only test: verified persistence and corruption checks are exercised by gate tests.
    frame = pd.DataFrame(
        {
            "actual": [10.0],
            "baseline": [10.0],
            "lower": [9.0],
            "upper": [11.0],
            "deviation": [0.0],
            "deviation_pct": [0.0],
            "cum_deviation": [0.0],
            "cum_deviation_pct": [0.0],
            "normalized_deviation": [0.0],
            "exceedance": [False],
            "alert": [""],
        },
        index=pd.date_range("2025-01-01", periods=1),
    )
    frame.attrs["monitoring_snapshot"] = {"baseline_version": 3, "input_sha256": "a" * 64}
    monkeypatch.setattr(
        store, "list_snapshots", lambda: [{"id": 1, "name": "one", "target": "energy", "created": "now"}]
    )
    monkeypatch.setattr(store, "load_snapshot", lambda sid: frame)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **kw: (a[3][0], True))
    page.load_snapshot()
    assert "version 3" in page.schema_box.toPlainText()
    assert "a" * 64 in page.schema_box.toPlainText()
    assert np.isfinite(page.results["energy"].actual).all()
    page.close()
    main.close()
