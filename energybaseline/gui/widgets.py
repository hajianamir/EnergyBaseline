"""Reusable Qt widgets: matplotlib canvas, table helpers, paste-enabled table, background worker."""
from __future__ import annotations

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import QFrame, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout


class MplCanvas(FigureCanvasQTAgg):
    def __init__(self, figsize=(6, 3.2)):
        self.fig = Figure(figsize=figsize, layout="constrained")
        super().__init__(self.fig)
        self.setMinimumHeight(220)


def fill_table(tbl: QTableWidget, df: pd.DataFrame | None, max_rows: int = 3000, decimals: int = 4) -> None:
    tbl.setSortingEnabled(False)
    tbl.clear()
    if df is None or len(df.columns) == 0:
        tbl.setRowCount(0)
        tbl.setColumnCount(0)
        return
    d = df.head(max_rows)
    tbl.setRowCount(len(d))
    tbl.setColumnCount(len(d.columns))
    tbl.setHorizontalHeaderLabels([str(c) for c in d.columns])
    for i, (_, row) in enumerate(d.iterrows()):
        for j, v in enumerate(row.values):
            it = QTableWidgetItem()
            if isinstance(v, (bool, np.bool_)):
                it.setText(str(bool(v)))
            elif isinstance(v, (int, float, np.integer, np.floating)) and not (isinstance(v, float) and np.isnan(v)):
                it.setData(Qt.ItemDataRole.DisplayRole, round(float(v), decimals) if isinstance(v, (float, np.floating)) else int(v))
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


def make_table(sortable: bool = True) -> QTableWidget:
    t = QTableWidget()
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSortingEnabled(sortable)
    t.verticalHeader().setVisible(False)
    return t


class PasteTable(QTableWidget):
    """Editable table that accepts Ctrl+V (tab/newline separated, e.g. copied from Excel)."""

    def __init__(self, columns: list[str] | None = None):
        super().__init__(0, len(columns or []))
        if columns:
            self.setHorizontalHeaderLabels(columns)
        self.setAlternatingRowColors(True)

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
            rows.append([(self.item(i, j).text().strip() if self.item(i, j) else "") for j in range(self.columnCount())])
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
