"""Phase 5 GUI tests: 1M-row fetchMore paging, zero QTableWidget on ResultsPage, 3000-row ceiling removal, UX-5 & UX-8."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication, QTableView, QTableWidget

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["EB_NONINTERACTIVE"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_paged_table_view_fetch_more_on_1m_rows_without_full_materialization(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    from energybaseline.gui.table_model import CallableChunkSource
    from energybaseline.gui.widgets import PagedTableView, fill_table, make_table

    # 1. 1,000,000-row lazy CallableChunkSource: verify bounded resident rows & zero full-frame materialization
    total_rows = 1_000_000
    chunk_calls: list[tuple[int, int]] = []

    def _loader(start: int, stop: int) -> dict[str, np.ndarray]:
        chunk_calls.append((start, stop))
        idx = np.arange(start, stop, dtype=np.int64)
        return {
            "row_id": idx,
            "temp_c": 15.0 + 0.01 * (idx % 1000),
            "energy_kwh": 100.0 + 2.5 * (idx % 500),
        }

    src = CallableChunkSource(
        total_rows=total_rows,
        columns=["row_id", "temp_c", "energy_kwh"],
        chunk_loader=_loader,
        page_size=256,
        max_cached_pages=6,
    )
    tbl = make_table(accessible_name="One million row meter preview")
    assert isinstance(tbl, PagedTableView)
    assert isinstance(tbl, QTableView)
    assert not isinstance(tbl, QTableWidget)

    tbl.set_lazy_source(src, prefetch_first_page=True)
    assert tbl.total_source_rows() == 1_000_000
    assert tbl.rowCount() == 256
    assert tbl.source_model.full_frame_materialized is False

    for _ in range(15):
        assert tbl.proxy_model.canFetchMore(QModelIndex()) is True
        tbl.proxy_model.fetchMore(QModelIndex())

    assert tbl.rowCount() == 16 * 256
    # Bounded 6-page LRU cache guarantees at most 6 * 256 = 1,536 rows resident in memory (< 0.16% of 1M rows)!
    assert tbl.source_model.peak_resident_rows <= 6 * 256
    assert len(chunk_calls) <= 32

    # 2. 1,000,000-row ParquetPagedSource through PagedTableView
    pq_file = tmp_path / "gui_1m.parquet"
    schema = pa.schema([("id", pa.int64()), ("kwh", pa.float64())])
    with pq.ParquetWriter(pq_file, schema) as writer:
        for b_start in range(0, 1_000_000, 100_000):
            ids = np.arange(b_start, b_start + 100_000, dtype=np.int64)
            writer.write_batch(
                pa.RecordBatch.from_arrays([pa.array(ids), pa.array(ids.astype(float) * 0.5)], schema=schema),
                row_group_size=100_000,
            )

    tbl.set_parquet_source(pq_file, page_size=500, max_cached_pages=4, prefetch_first_page=True)
    assert tbl.total_source_rows() == 1_000_000
    assert tbl.rowCount() == 500
    tbl.proxy_model.fetchMore(QModelIndex())
    assert tbl.rowCount() == 1000
    assert tbl.source_model.peak_resident_rows <= 2000
    assert tbl.source_model.full_frame_materialized is False

    # 3. Proof that 3,000-row truncation ceiling is gone on DataFrame fill_table
    df_5k = pd.DataFrame({"idx": np.arange(5000), "val": np.linspace(0.0, 1.0, 5000)})
    fill_table(tbl, df_5k)
    assert tbl.total_source_rows() == 5000
    while tbl.proxy_model.canFetchMore(QModelIndex()):
        tbl.proxy_model.fetchMore(QModelIndex())
    assert tbl.rowCount() == 5000


def test_results_page_zero_qtablewidget_ux5_register_and_ux8_accessibility(
    qapp: QApplication,
    eb_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    import energybaseline.db.session as ses
    from energybaseline.gui.main_window import MainWindow
    from energybaseline.gui.pages_results import REGISTER_COLUMNS
    from energybaseline.gui.state import AppState
    from energybaseline.models import ModelEvaluationEntry

    # 1. Static AST proof: zero QTableWidget references in energybaseline/gui/pages_results.py
    src_text = (REPO_ROOT / "energybaseline" / "gui" / "pages_results.py").read_text(encoding="utf-8")
    tree = ast.parse(src_text)
    names_used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs_used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "QTableWidget" not in names_used and "QTableWidget" not in attrs_used

    ses.reset_engine()
    boot = ses.init_database()
    state = AppState()
    win = MainWindow(state, boot_selftest=boot)
    try:
        res_page = win.results_page
        # 2. Runtime widget-tree proof: zero QTableWidget descendants inside ResultsPage
        assert res_page.findChildren(QTableWidget) == []
        assert len(res_page.data_tables) == 9

        # 3. UX-8 Accessibility & Focus Policy verification across ResultsPage controls & tables
        for ctrl in (
            res_page.combo,
            res_page.register_filter,
            res_page.tabs,
            res_page.gen_btn,
            res_page.prev_btn,
            res_page.open_btn,
        ):
            assert ctrl.accessibleName().strip() != ""
            assert ctrl.focusPolicy() == Qt.FocusPolicy.StrongFocus
        for tbl in res_page.data_tables:
            assert tbl.accessibleName().strip() != ""
            assert tbl.accessibleDescription().strip() != ""
            assert tbl.focusPolicy() == Qt.FocusPolicy.StrongFocus

        # 4. UX-5 Model Evaluation Register rendering + sorting + live search filtering
        sample_entries = [
            ModelEvaluationEntry(
                family_id="F1",
                family_name="Linear Baseline",
                model_name="F1_HAC_Linear",
                status="qualified",
                status_badge="QUALIFIED",
                r2=0.91,
                cvrmse=7.4,
                nmbe=0.3,
                mae=12.1,
                rmse=15.4,
                wape=5.8,
                composite_score=88.5,
                in_mcs=True,
                n_obs=120,
                n_params=4,
                fit_seconds=0.02,
                formula_summary="y = 100 + 3.2*temp",
                reason="Meets validation thresholds.",
            ),
            ModelEvaluationEntry(
                family_id="F2",
                family_name="ASHRAE Change-Point",
                model_name="F2_ASHRAE_5P",
                status="shortlisted",
                status_badge="SHORTLISTED",
                r2=0.96,
                cvrmse=4.8,
                nmbe=-0.1,
                mae=8.2,
                rmse=10.1,
                wape=3.9,
                composite_score=94.2,
                in_mcs=True,
                n_obs=120,
                n_params=5,
                fit_seconds=0.08,
                formula_summary="y = 95 + 4.1*HDD + 5.8*CDD",
                reason="Meets validation thresholds.",
            ),
            ModelEvaluationEntry(
                family_id="F9",
                family_name="Decomposition Hybrid",
                model_name="F9_Prophet_Optional",
                status="skipped",
                status_badge="SKIPPED",
                r2=None,
                cvrmse=None,
                nmbe=None,
                mae=None,
                rmse=None,
                wape=None,
                composite_score=None,
                in_mcs=False,
                n_obs=120,
                n_params=0,
                fit_seconds=0.001,
                formula_summary="not fitted",
                reason="Optional extra 'prophet' not installed.",
            ),
        ]
        df_reg = res_page.populate_model_evaluation_register(sample_entries)
        assert list(df_reg.columns) == list(REGISTER_COLUMNS)
        assert res_page.eval_register_tbl.rowCount() == 3

        res_page.register_filter.setText("SHORTLISTED")
        assert res_page.eval_register_tbl.rowCount() == 1
        assert (
            res_page.eval_register_tbl.proxy_model.data(res_page.eval_register_tbl.proxy_model.index(0, 2))
            == "F2_ASHRAE_5P"
        )

        res_page.register_filter.setText("")
        assert res_page.eval_register_tbl.rowCount() == 3
    finally:
        win.close()
        qapp.processEvents()
        ses.reset_engine()
