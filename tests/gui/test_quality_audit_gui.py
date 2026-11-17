"""The quality tab executes policy/reference inputs without changing authorization."""

import json

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from energybaseline.db.session import init_database, reset_engine
from energybaseline.gui.main_window import MainWindow
from energybaseline.gui.state import AppState


@pytest.fixture
def quality_page(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "state.sqlite").as_posix())
    reset_engine()
    state = AppState()
    state.targets = ["value"]
    window = MainWindow(state, init_database())
    qtbot.addWidget(window)
    frame = pd.DataFrame(
        {"time": pd.date_range("2026-01-01", periods=30, freq="h"), "value": range(30), "driver": range(30)}
    )
    window.setup_page.set_dataset(frame, "synthetic", "manual", "b" * 64)
    yield window.setup_page
    window.close()
    reset_engine()


def test_quality_tab_loads_policy_and_explicit_reference(quality_page, tmp_path):
    page = quality_page
    assert page.quality_table.rowCount() == 6
    assert page.state.quality_audit["fully_assessed_dimensions"] == 2
    assert "2/6" in page.quality_status.text()
    page.accept_and_lock_tcp()
    vsg, tcp, runner = page.state.vsg, page.state.tcp, page.state.compliance_runner
    calls = runner.engine_call_count
    original = page.state.df.copy(deep=True)
    reference = tmp_path / "truth.csv"
    page.state.df.to_csv(reference, index=False)
    policy = {
        "unique_by": ["time"],
        "validity": [{"kind": "finite", "column": "value"}],
        "consistency": [{"kind": "compare", "left": "value", "op": "eq", "right": "driver"}],
        "accuracy": {
            "reference": "truth",
            "keys": ["time"],
            "columns": ["value"],
            "key_types": {"time": "datetime_utc"},
        },
        "timeliness": {"column": "time", "max_age_seconds": 172800},
    }
    path = tmp_path / "quality.json"
    path.write_text(json.dumps(policy))
    assert page.load_quality_reference(reference, name="truth", allowed_roots=[tmp_path])
    page.quality_as_of.setText("2026-01-03T00:00:00Z")
    assert page.load_quality_policy(path, allowed_roots=[tmp_path])
    assert page.state.quality_audit["overall_score"] == 1
    assert "6/6" in page.quality_status.text()
    assert (page.state.vsg, page.state.tcp, page.state.compliance_runner) == (vsg, tcp, runner)
    assert vsg.is_accepted() and tcp.is_locked
    assert runner.engine_call_count == calls
    assert_frame_equal(page.state.df, original)


def test_invalid_policy_and_reload_cannot_reuse_stale_quality_evidence(quality_page, tmp_path):
    page = quality_page
    path = tmp_path / "quality.json"
    path.write_text('{"validity":[{"kind":"execute","column":"value"}]}')
    assert not page.load_quality_policy(path, allowed_roots=[tmp_path])
    assert page.state.quality_audit is None
    assert page.quality_table.rowCount() == 0
    assert "blocked" in page.quality_status.text()
    page._quality_references["old_reference"] = page.state.df.copy()
    frame = page.state.df.copy()
    page.set_dataset(frame, "reload", "manual", "c" * 64)
    assert page._quality_references == page._quality_policy == {}
    assert page.state.quality_audit["fully_assessed_dimensions"] == 2
    assert not page.load_quality_policy(tmp_path / "../outside.json", allowed_roots=[tmp_path])
    assert page.state.quality_audit is None
