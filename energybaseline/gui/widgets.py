"""Reusable Qt widgets: matplotlib canvas, QAbstractTableModel paged views, paste-enabled input grid, worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .table_model import (
    DataFramePagedSource,
    LazyPageSource,
    PagedSortFilterProxyModel,
    PagedTableModel,
    ParquetPagedSource,
)


class PagedTableView(QTableView):
    """Accessible QTableView backed by PagedSortFilterProxyModel + PagedTableModel (zero QTableWidget)."""

    def __init__(
        self,
        *,
        sortable: bool = True,
        accessible_name: str = "Data table",
        accessible_description: str = "Sortable paged results table",
        page_size: int = 512,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._page_size = max(16, int(page_size))
        self.source_model = PagedTableModel(decimals=4, parent=self)
        self.proxy_model = PagedSortFilterProxyModel(parent=self)
        self.proxy_model.setSourceModel(self.source_model)
        self.setModel(self.proxy_model)

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(bool(sortable))
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setTabKeyNavigation(False)
        self.setAccessibleName(accessible_name)
        self.setAccessibleDescription(accessible_description)

    def set_dataframe(self, df: pd.DataFrame | None, *, decimals: int = 4, page_size: int | None = None) -> None:
        ps = self._page_size if page_size is None else max(16, int(page_size))
        src = DataFramePagedSource(df, page_size=ps)
        self.source_model.set_source(src, decimals=decimals, prefetch_first_page=True)
        self._auto_size_columns()

    def set_parquet_source(
        self,
        parquet_path: str | Path,
        *,
        decimals: int = 4,
        page_size: int = 512,
        max_cached_pages: int = 8,
        prefetch_first_page: bool = True,
    ) -> None:
        src = ParquetPagedSource(parquet_path, page_size=page_size, max_cached_pages=max_cached_pages)
        self.source_model.set_source(src, decimals=decimals, prefetch_first_page=prefetch_first_page)
        self._auto_size_columns()

    def set_lazy_source(
        self,
        source: LazyPageSource,
        *,
        decimals: int = 4,
        prefetch_first_page: bool = True,
    ) -> None:
        self.source_model.set_source(source, decimals=decimals, prefetch_first_page=prefetch_first_page)
        self._auto_size_columns()

    def set_filter_text(self, text: str) -> None:
        self.proxy_model.setFilterFixedString(str(text or "").strip())

    def rowCount(self) -> int:
        return self.proxy_model.rowCount()

    def columnCount(self) -> int:
        return self.proxy_model.columnCount()

    def total_source_rows(self) -> int:
        return self.source_model.total_source_rows

    def _auto_size_columns(self) -> None:
        if self.proxy_model.columnCount() == 0:
            return
        self.resizeColumnsToContents()
        for j in range(self.proxy_model.columnCount()):
            if self.columnWidth(j) > 420:
                self.setColumnWidth(j, 420)

    def keyPressEvent(self, event: Any) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            sel = self.selectionModel()
            if sel is not None and sel.hasSelection():
                rows = sorted({idx.row() for idx in sel.selectedIndexes()})
                cols = list(range(self.proxy_model.columnCount()))
                lines = [
                    "\t".join(str(self.proxy_model.headerData(c, Qt.Orientation.Horizontal) or "") for c in cols)
                ]
                for r in rows:
                    vals = [
                        str(self.proxy_model.data(self.proxy_model.index(r, c), Qt.ItemDataRole.DisplayRole) or "")
                        for c in cols
                    ]
                    lines.append("\t".join(vals))
                QGuiApplication.clipboard().setText("\n".join(lines))
                return
        super().keyPressEvent(event)


class MplCanvas(FigureCanvasQTAgg):
    def __init__(self, figsize=(6, 3.2), *, accessible_name: str = "Diagnostic chart") -> None:
        self.fig = Figure(figsize=figsize, layout="constrained")
        super().__init__(self.fig)
        self.setMinimumHeight(220)
        self.setAccessibleName(accessible_name)


def fill_table(
    tbl: PagedTableView | QTableWidget,
    df: pd.DataFrame | None,
    max_rows: int | None = None,
    decimals: int = 4,
) -> None:
    """Populate a PagedTableView (no 3,000-row ceiling) or small interactive form grid."""
    frame = df.iloc[:max_rows] if (df is not None and max_rows is not None) else df
    if isinstance(tbl, PagedTableView):
        tbl.set_dataframe(frame, decimals=decimals)
        return

    tbl.setSortingEnabled(False)
    tbl.clear()
    if frame is None or len(frame.columns) == 0:
        tbl.setRowCount(0)
        tbl.setColumnCount(0)
        return
    tbl.setRowCount(len(frame))
    tbl.setColumnCount(len(frame.columns))
    tbl.setHorizontalHeaderLabels([str(c) for c in frame.columns])
    for i, (_, row) in enumerate(frame.iterrows()):
        for j, v in enumerate(row.values):
            it = QTableWidgetItem()
            if isinstance(v, (bool, np.bool_)):
                it.setText(str(bool(v)))
            elif isinstance(v, (int, float, np.integer, np.floating)) and not (
                isinstance(v, float) and np.isnan(v)
            ):
                it.setData(
                    Qt.ItemDataRole.DisplayRole,
                    round(float(v), decimals) if isinstance(v, (float, np.floating)) else int(v),
                )
            elif v is None or (isinstance(v, float) and np.isnan(v)):
                it.setText("")
            else:
                it.setText(str(v))
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            tbl.setItem(i, j, it)
    tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    tbl.resizeColumnsToContents()
    for j in range(tbl.columnCount()):
        if tbl.columnWidth(j) > 420:
            tbl.setColumnWidth(j, 420)
    tbl.setSortingEnabled(True)


def make_table(
    sortable: bool = True,
    *,
    accessible_name: str = "Data table",
    accessible_description: str = "Sortable paged table view",
    page_size: int = 512,
) -> PagedTableView:
    """Create a QAbstractTableModel + fetchMore + QSortFilterProxyModel table view (no QTableWidget)."""
    return PagedTableView(
        sortable=sortable,
        accessible_name=accessible_name,
        accessible_description=accessible_description,
        page_size=page_size,
    )


def make_form_grid(sortable: bool = False, *, accessible_name: str = "Configuration grid") -> QTableWidget:
    """Small interactive cell-widget editor grid (used only for ~5-30 schema/override rows on SetupPage)."""
    t = QTableWidget()
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSortingEnabled(sortable)
    t.verticalHeader().setVisible(False)
    t.setAccessibleName(accessible_name)
    return t


class PasteTable(QTableWidget):
    """Editable manual-entry grid that accepts Ctrl+V (tab/newline separated, e.g. copied from Excel)."""

    def __init__(self, columns: list[str] | None = None):
        super().__init__(0, len(columns or []))
        if columns:
            self.setHorizontalHeaderLabels(columns)
        self.setAlternatingRowColors(True)
        self.setAccessibleName("Manual data entry paste grid")

    def set_columns(self, columns: list[str]) -> None:
        self.clear()
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.setRowCount(0)

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.StandardKey.Paste):
            text = QGuiApplication.clipboard().text()
            rows = [r.split("\t") for r in text.replace("\r", "").split("\n") if r.strip()]
            r0 = max(0, self.currentRow())
            c0 = max(0, self.currentColumn())
            for i, r in enumerate(rows):
                if r0 + i >= self.rowCount():
                    self.insertRow(self.rowCount())
                for j, v in enumerate(r):
                    if c0 + j < self.columnCount():
                        self.setItem(r0 + i, c0 + j, QTableWidgetItem(v.strip()))
            return
        if e.key() == Qt.Key.Key_Delete:
            for it in self.selectedItems():
                it.setText("")
            return
        super().keyPressEvent(e)

    def add_row(self, values: list[str]) -> None:
        r = self.rowCount()
        self.insertRow(r)
        for j, v in enumerate(values):
            self.setItem(r, j, QTableWidgetItem(str(v)))

    def to_frame(self) -> pd.DataFrame:
        cols = [self.horizontalHeaderItem(j).text() for j in range(self.columnCount())]
        rows = []
        for i in range(self.rowCount()):
            rows.append(
                [(self.item(i, j).text().strip() if self.item(i, j) else "") for j in range(self.columnCount())]
            )
        df = pd.DataFrame(rows, columns=cols).replace("", np.nan)
        for c in df.columns:
            num = pd.to_numeric(df[c].astype("string").str.replace(",", ".", regex=False), errors="coerce")
            if num.notna().sum() >= df[c].notna().sum() and df[c].notna().any():
                df[c] = num
        return df


class KpiCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("kpi")
        self.setAccessibleName(f"KPI card: {title}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.t = QLabel(title)
        self.t.setObjectName("kpiTitle")
        self.v = QLabel("-")
        self.v.setObjectName("kpiValue")
        lay.addWidget(self.t)
        lay.addWidget(self.v)

    def set(self, value: str) -> None:
        self.v.setText(value)


class FuncWorker(QThread):
    """Run a callable off the GUI thread (used for SQL pulls and exports)."""

    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn, *a, **k):
        super().__init__()
        self.fn, self.a, self.k = fn, a, k

    def run(self):
        try:
            self.ok.emit(self.fn(*self.a, **self.k))
        except Exception as exc:  # reported to the user, never crashes the app
            self.fail.emit(str(exc))
