"""Phase 3 headless Qt tests: ImpactPreviewPanel (VSG-3), VSG/TCP compliance tabs, What-If, and UI-only post_hoc badge."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["EB_NONINTERACTIVE"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = REPO_ROOT / "samples"


def test_gui_impact_preview_vsg_tcp_whatif_and_post_hoc_ui_badge(
    qtbot,
    eb_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    import energybaseline.db.session as ses
    from energybaseline.gui.main_window import MainWindow
    from energybaseline.gui.pages_setup import ImpactPreviewPanel, format_limitations_badges
    from energybaseline.gui.state import AppState

    ses.reset_engine()
    boot = ses.init_database()
    state = AppState()
    win = MainWindow(state, boot_selftest=boot)
    qtbot.addWidget(win)

    # 1. Verify system.health and system.sbom commands in compile-time CommandRegistry
    health = win.commands.dispatch("system.health")
    sbom = win.commands.dispatch("system.sbom")
    assert health["command"] == "system.health" and health["ok"] is True
    assert sbom["bomFormat"] == "CycloneDX" and sbom["component_count"] >= 20

    # 2. Load sample dataset -> initializes VSG, TCP, ImpactPreviewPanel, and AutoPanel hash
    sample_csv = SAMPLES / "monthly_building.csv"
    assert win.setup_page.open_file(sample_csv, allowed_roots=[SAMPLES]) is True
    assert isinstance(win.setup_page.impact_preview, ImpactPreviewPanel)
    assert win.setup_page.impact_preview.last_preview["total_tests"] > 0
    assert "Fast:" in win.setup_page.impact_preview.lbl_estimates.text()
    assert len(win.auto_panel.auto_log_hash) == 64

    # 3. AnalysisPage blocks execution before VSG and TCP are accepted (PIPE-1)
    win.analysis_page.start()
    assert "Blocked:" in win.analysis_page.status.text()

    # 4. Elevated alpha (> 0.10) surfaces UI warning
    win.setup_page.tcp_alpha_combo.setCurrentText("0.15")
    assert win.setup_page.lbl_alpha_warning.isHidden() is False
    win.setup_page.tcp_alpha_combo.setCurrentText("0.05")
    assert win.setup_page.lbl_alpha_warning.isHidden() is True

    # 5. Accept & lock VSG + TCP, then execute zero-recompute What-If in UI
    digest = win.setup_page.accept_and_lock_tcp()
    assert len(digest) == 64
    calls_before = state.compliance_runner.engine_call_count
    assert calls_before > 0

    win.setup_page.tcp_alpha_combo.setCurrentText("0.01")
    win.setup_page.tcp_corr_combo.setCurrentText("bonferroni")
    wi = win.setup_page.run_what_if_ui()
    assert wi["engine_calls_delta"] == 0
    assert state.compliance_runner.engine_call_count == calls_before

    # 6. Unlocking for post-test amendment activates UI-only [post_hoc] Limitations badge
    #    while serialized provenance contains ZERO 'post_hoc' occurrences!
    assert win.setup_page.lbl_limitations.isHidden() is True
    win.setup_page.unlock_tcp_for_amendment("Reviewer post-test sensitivity check")
    assert win.setup_page.lbl_limitations.isHidden() is False
    badges = format_limitations_badges(state.vsg.provenance.to_dict())
    assert len(badges) >= 1 and any("post_hoc" in b for b in badges)
    assert "post_hoc" in win.setup_page.lbl_limitations.text()

    serialized_prov = json.dumps(state.vsg.provenance.to_dict())
    assert "post_hoc" not in serialized_prov.lower()
