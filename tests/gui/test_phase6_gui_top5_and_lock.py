"""Phase 6 Gate G6 Headless GUI Tests — Pre-Lock Top-5 Visibility, Labelled Surrogates, Separated Registers & SC-7/SC-9 Lock."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget
import pytest

from energybaseline.core import store
from energybaseline.core.pipeline import run_analysis
from energybaseline.gui.pages_results import ResultsPage, SelectionPage
from energybaseline.gui.state import AppState


class _StubMain(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.logs: list[str] = []

    def log_msg(self, msg: str) -> None:
        self.logs.append(msg)


def test_gui_top5_visible_before_lock_labelled_surrogates_separated_registers_and_override_guard(
    qapp: object,
    eb_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Top-5 visibility before lock, separated Qualified-Not-Top5 vs Rejected tables, surrogate labels & SC-7 override."""
    from energybaseline.db.session import init_database

    _ = (qapp, eb_home)
    init_database()
    df = pd.read_csv("samples/monthly_building.csv")
    rid = store.new_run(None, ["Energy_kWh"], {"hpo_trials": "0", "nn_max_epochs": "10", "enable_mining": "false"})
    run, bundles = run_analysis(
        df,
        "Month",
        ["Energy_kWh"],
        {"hpo_trials": "0", "nn_max_epochs": "10", "enable_mining": "false"},
        dataset_sha="gui-phase6-sha",
        dataset_info={"name": "monthly_building.csv", "rows": len(df), "cols": df.shape[1]},
        artifact_fn=lambda t, m: str(store.artifact_path(rid, t, m)),
    )
    store.save_run(rid, run, bundles)

    # Inject one non-parametric shortlisted entry with labelled surrogate if Top-5 happened to be all linear on monthly sample
    tr = run.targets["Energy_kWh"]
    if all(s["formula"].get("kind") == "equation" for s in tr.shortlist):
        synth_np = dict(tr.shortlist[-1])
        synth_np["name"] = "RandomForest_SurrogateCheck"
        synth_np["family"] = "Tree-based"
        synth_np["kind"] = "tree"
        synth_np["formula"] = {
            "text": "Architecture: bagged tree ensemble B=120 trees\n[APPROXIMATE_DIAGNOSTIC_SURROGATE — fidelity R²=0.941]",
            "terms": [],
            "kind": "ensemble/kernel",
            "is_parametric": False,
            "surrogate": {
                "is_surrogate": True,
                "surrogate_banner": "APPROXIMATE_DIAGNOSTIC_SURROGATE",
                "fidelity_r2": 0.941,
                "approximation_expression": "[SURROGATE APPROXIMATION ONLY] y_surr ~= 14200 + 310*HDD",
            },
        }
        tr.shortlist[-1] = synth_np

    state = AppState()
    state.run_id = rid
    state.run = run
    stub_main = _StubMain()

    res_page = ResultsPage(state, stub_main)
    sel_page = SelectionPage(state, stub_main)
    state.run_changed.emit()

    # 1. Separate PagedTableView widgets for Qualified (Not Top-5) vs Rejected Models Register
    assert res_page.qualified_other_tbl.total_source_rows() == len(tr.qualified_not_shortlisted_register)
    assert res_page.register.total_source_rows() == len(tr.register)
    assert res_page.qualified_other_tbl.total_source_rows() >= 1

    # 2. Top-5 is visible BEFORE lock!
    assert sel_page.is_shortlist_visible_before_lock("Energy_kWh") is True
    assert len(sel_page.candidate_cards["Energy_kWh"]) == len(tr.shortlist)

    # Verify non-parametric card explicitly displays APPROXIMATE_DIAGNOSTIC_SURROGATE + Fidelity R², never "Closed-form prediction equation"
    np_card = sel_page.candidate_cards["Energy_kWh"][-1]
    card_labels = " | ".join(lbl.text() for lbl in np_card.findChildren(QLabel))
    assert "NOT model formula" in card_labels
    assert "APPROXIMATE_DIAGNOSTIC_SURROGATE" in card_labels
    assert "0.941" in card_labels

    # 3. Selecting Rank #2 (override) WITHOUT >= 15-char engineering justification is blocked by SC-7!
    warnings_shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings_shown.append(f"{title}: {text}"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    btns = sel_page.groups["Energy_kWh"].buttons()
    assert len(btns) >= 2
    btns[1].setChecked(True)  # Rank #2 override
    sel_page.notes["Energy_kWh"].setText("short")
    sel_page.lock("Energy_kWh")
    assert "Energy_kWh" not in state.locked
    assert any("SC-7" in w for w in warnings_shown)

    # 4. Providing documented >= 15-char justification succeeds and locks cleanly!
    sel_page.notes["Energy_kWh"].setText(
        "Selected Rank #2 linear change-point baseline for direct utility M&V auditability."
    )
    sel_page.lock("Energy_kWh")
    assert "Energy_kWh" in state.locked
    assert sel_page.is_shortlist_visible_before_lock("Energy_kWh") is False
