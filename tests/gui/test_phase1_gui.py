"""Phase 1 GUI smoke tests: single-instance guard (P1.3), Setup path guard (P1.4), commands (P1.5), Status & Banner (P1.7)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from energybaseline.db import session as ses
from energybaseline.gui.commands import UnknownCommandError
from energybaseline.gui.main_window import MainWindow
from energybaseline.gui.single_instance import SingleInstanceGuard
from energybaseline.gui.state import AppState


def test_single_instance_guard_activates_primary_and_rejects_second(qapp: QApplication) -> None:
    srv_name = f"eb_pytest_si_{uuid.uuid4().hex[:12]}"
    activated: list[bool] = []

    primary = SingleInstanceGuard(server_name=srv_name)
    primary.activation_requested.connect(lambda: activated.append(True))
    try:
        assert primary.try_acquire() is True
        assert primary.is_primary is True

        secondary = SingleInstanceGuard(server_name=srv_name)
        assert secondary.try_acquire() is False
        assert secondary.is_primary is False

        qapp.processEvents()
        assert activated == [True]
    finally:
        primary.close()


def test_main_window_commands_path_guard_status_page_and_banners(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    eb_home = tmp_path / "gui_eb_home"
    eb_home.mkdir()
    monkeypatch.setenv("EB_HOME", str(eb_home))
    ses.reset_engine()
    boot = ses.init_database()

    win = MainWindow(AppState(), boot)
    try:
        win.show()
        qapp.processEvents()

        # P1.5: Command allowlist works for known commands and rejects unknown commands
        win.commands.dispatch("nav.status")
        qapp.processEvents()
        with pytest.raises(UnknownCommandError):
            win.commands.dispatch("arbitrary.command")

        # P1.4: SetupPage.open_file rejects '..' traversal and UNC paths
        assert win.setup_page.open_file("../outside.csv", allowed_roots=[tmp_path]) is False
        assert "Rejected unsafe file path" in win.setup_page.info.text()
        assert win.setup_page.open_file(r"\\evilserver\share\data.csv", allowed_roots=[tmp_path]) is False

        # Valid CSV inside allowed root loads cleanly
        good_csv = tmp_path / "facility.csv"
        good_csv.write_text(
            "timestamp,Energy_kWh,Temp_C\n2024-01-01,100,15\n2024-01-02,110,12\n2024-01-03,95,18\n",
            encoding="utf-8",
        )
        assert win.setup_page.open_file(good_csv, allowed_roots=[tmp_path]) is True
        assert win.state.df is not None and len(win.state.df) == 3

        # P1.7: Live StatusPage snapshot reports DB rung/PRAGMAs, pool protocol, cache dir, 0 orphans
        snap = win.status_page.refresh(run_selftest=True)
        assert snap["healthy"] is True
        assert snap["db"]["rung"] == "eb_home"
        assert snap["db"]["pragmas"]["journal_mode"] == "wal"
        assert snap["pool"]["protocol_version"] == "1.0"
        assert snap["pool"]["orphan_count"] == 0
        assert Path(snap["storage"]["cache_dir"]).name == "cache"

        # P1.6 / P1.7: FallbackBanner reflects warning (%TEMP%) and critical red (:memory:) states
        assert win.banner.isHidden() is True

        ses._state["banner_level"] = "warning"
        ses._state["banner_text"] = "Running in %TEMP% — data may be lost."
        assert win.banner.refresh_from_db() == "warning"
        assert win.banner.isHidden() is False
        assert "data may be lost" in win.banner.label.text()

        ses._state["banner_level"] = "critical"
        ses._state["banner_text"] = "CRITICAL: Running IN MEMORY ONLY."
        assert win.banner.refresh_from_db() == "critical"
        assert win.banner.isHidden() is False
        assert "#991B1B" in win.banner.styleSheet()
    finally:
        win.close()
        qapp.processEvents()
        ses.reset_engine()
