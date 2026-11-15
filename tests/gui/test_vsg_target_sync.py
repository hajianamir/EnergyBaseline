"""Target edits refresh pending gates immediately; viewing targets never edits roles."""

import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import Qt

from energybaseline.core.vsg import ComplianceGateError
from energybaseline.db.session import init_database, reset_engine
from energybaseline.gui.main_window import MainWindow
from energybaseline.gui.state import AppState


@pytest.fixture
def setup_page(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "state.sqlite").as_posix())
    reset_engine()
    state = AppState()
    state.targets = ["output_a"]
    window = MainWindow(state, init_database())
    qtbot.addWidget(window)
    x = np.arange(48, dtype=float)
    window.setup_page.set_dataset(
        pd.DataFrame(
            {
                "timestamp": pd.date_range("2024-01-01", periods=48, freq="D"),
                "output_a": 2 * x + np.sin(x),
                "output_b": 3 * x + np.cos(x),
                "driver": x,
            }
        ),
        "synthetic",
        "manual",
        "a" * 64,
    )
    yield window.setup_page
    window.close()
    reset_engine()


def check_target(page, name, checked):
    item = next(
        page.target_list.item(i) for i in range(page.target_list.count()) if page.target_list.item(i).text() == name
    )
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)


def combo_targets(page):
    return [page.vsg_target_combo.itemText(i) for i in range(page.vsg_target_combo.count())]


def assert_current_targets(page, expected):
    assert page.state.targets == expected
    assert combo_targets(page) == expected
    assert list(page.state.vsg.targets) == expected
    assert page.state.dataset_schema.target_columns == expected
    assert not page.state.vsg.is_accepted()
    assert not page.state.tcp.is_locked
    assert page.state.vsg_id is page.state.tcp_id is None
    for target in expected:
        assert not set(expected) & set(page.state.vsg.included_variables(target))


def test_checklist_immediately_refreshes_vsg_and_clears_stale_authorization(setup_page):
    page = setup_page
    old_vsg = page.state.vsg
    page.accept_and_lock_tcp()
    assert old_vsg.is_accepted() and page.state.tcp.is_locked
    old_runner = page.state.compliance_runner
    calls = old_runner.engine_call_count
    check_target(page, "output_b", True)
    assert_current_targets(page, ["output_a", "output_b"])
    assert page.state.vsg is not old_vsg
    assert page.state.compliance_runner.engine_call_count == 0
    assert old_runner.engine_call_count == calls
    with pytest.raises(ComplianceGateError):
        page.state.vsg.assert_accepted()
    check_target(page, "output_a", False)
    assert_current_targets(page, ["output_b"])


def test_schema_role_combo_and_metadata_api_refresh_without_collect(setup_page):
    page = setup_page
    row = next(r for r in range(page.schema_tbl.rowCount()) if page.schema_tbl.item(r, 0).text() == "output_b")
    page.schema_tbl.cellWidget(row, 1).setCurrentText("target")
    assert_current_targets(page, ["output_a", "output_b"])
    page.set_column_metadata("output_a", role="predictor")
    assert_current_targets(page, ["output_b"])


def test_clear_all_and_reselect_clears_stale_tables_status_and_impact(setup_page):
    page = setup_page
    page.accept_and_lock_tcp()
    check_target(page, "output_a", False)
    assert page.state.targets == []
    assert combo_targets(page) == []
    assert page.state.vsg is page.state.tcp is page.state.compliance_runner is None
    assert page.state.vsg_id is page.state.tcp_id is None
    assert page.vsg_tbl.rowCount() == page.integrity_tbl.rowCount() == page.tcp_results_tbl.rowCount() == 0
    assert "pending" in page.vsg_status.text().lower()
    assert "pending" in page.tcp_status.text().lower()
    check_target(page, "output_b", True)
    assert_current_targets(page, ["output_b"])


def test_view_switch_and_unchanged_collect_preserve_gate_objects(setup_page):
    page = setup_page
    check_target(page, "output_b", True)
    page.vsg_target_combo.setCurrentText("output_b")
    vsg, tcp, runner = page.state.vsg, page.state.tcp, page.state.compliance_runner
    vsg.override_variable("output_b", "driver", "exclude", reason="Explicit reviewer exclusion")
    before = vsg.to_dict()
    page.collect()
    assert vsg.to_dict() == before
    assert (page.state.vsg, page.state.tcp, page.state.compliance_runner) == (vsg, tcp, runner)
    assert page.vsg_target_combo.currentText() == "output_b"
    check_target(page, "driver", True)
    assert page.vsg_target_combo.currentText() == "output_b"
    assert_current_targets(page, ["output_a", "output_b", "driver"])


def test_schema_json_reload_refreshes_targets(setup_page, tmp_path):
    page = setup_page
    schema_path = tmp_path / "two-targets.json"
    check_target(page, "output_b", True)
    assert page.save_schema_file(schema_path, allowed_roots=[tmp_path]) == schema_path
    check_target(page, "output_b", False)
    assert page.load_schema_file(schema_path, allowed_roots=[tmp_path])
    assert_current_targets(page, ["output_a", "output_b"])


def test_invalid_metadata_fails_closed_and_recovers(setup_page):
    page = setup_page
    page.accept_and_lock_tcp()
    page.set_column_metadata("timestamp", role="predictor")
    assert page.state.vsg is page.state.tcp is page.state.compliance_runner is None
    assert combo_targets(page) == []
    assert page.impact_preview.last_preview == {}
    assert "time" in page.vsg_status.text()
    page.set_column_metadata("timestamp", role="time")
    assert_current_targets(page, ["output_a"])
