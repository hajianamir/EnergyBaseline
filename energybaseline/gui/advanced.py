"""Advanced settings dialog (AUTO everywhere) and the read-only AUTO Resolutions panel."""
from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from ..core.auto import is_auto
from ..core.schema import AUTO, CATEGORIES, SETTINGS
from ..core import store
from .widgets import fill_table, make_table


class AdvancedDialog(QDialog):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Advanced settings - AUTO is the default for every option")
        self.resize(980, 640)
        self.rows: dict[str, tuple[QCheckBox, QWidget]] = {}
        lay = QVBoxLayout(self)
        info = QLabel("Every setting offers <b>AUTO</b>: the application decides from data profiling and engineering rules, per target, and documents the "
                      "signals and rule in the AUTO Resolutions log and in the report. Untick AUTO to override; overrides are logged.")
        info.setWordWrap(True)
        lay.addWidget(info)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)
        for cat in CATEGORIES:
            page = QWidget()
            grid = QGridLayout(page)
            for j, h in enumerate(["Setting", "Current value", "AUTO", "Recommended / AUTO rule", "Explanation"]):
                lab = QLabel(f"<b>{h}</b>")
                grid.addWidget(lab, 0, j)
            r = 1
            for s in [x for x in SETTINGS if x.category == cat]:
                cur = state.config.get(s.key, AUTO)
                auto = is_auto(cur)
                if s.kind == "choice":
                    w: QWidget = QComboBox()
                    w.setEditable(True)
                    w.addItems(list(s.choices))
                    w.setCurrentText("" if auto else str(cur))
                elif s.kind == "bool":
                    w = QComboBox()
                    w.addItems(["true", "false"])
                    w.setCurrentText("true" if auto else str(cur).lower())
                else:
                    w = QLineEdit("" if auto else str(cur))
                    w.setPlaceholderText("AUTO")
                w.setEnabled(not auto)
                cb = QCheckBox()
                cb.setChecked(auto)
                cb.toggled.connect(lambda on, ww=w: ww.setEnabled(not on))
                lbl = QLabel(s.label)
                lbl.setToolTip(s.key)
                rec = QLabel(s.recommended)
                rec.setWordWrap(True)
                ex = QLabel(s.explain)
                ex.setWordWrap(True)
                grid.addWidget(lbl, r, 0)
                grid.addWidget(w, r, 1)
                grid.addWidget(cb, r, 2, alignment=Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(rec, r, 3)
                grid.addWidget(ex, r, 4)
                self.rows[s.key] = (cb, w)
                r += 1
            grid.setRowStretch(r, 1)
            grid.setColumnStretch(4, 2)
            grid.setColumnStretch(3, 1)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setWidget(page)
            tabs.addTab(sc, cat)
        btns = QHBoxLayout()
        reset = QPushButton("Reset everything to AUTO")
        reset.clicked.connect(self._reset)
        btns.addWidget(reset)
        btns.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        btns.addWidget(bb)
        lay.addLayout(btns)

    def _reset(self):
        for cb, _ in self.rows.values():
            cb.setChecked(True)

    @staticmethod
    def _text(w: QWidget) -> str:
        return w.currentText().strip() if isinstance(w, QComboBox) else w.text().strip()

    def _ok(self):
        changed = []
        for key, (cb, w) in self.rows.items():
            val = AUTO if cb.isChecked() or not self._text(w) else self._text(w)
            if str(self.state.config.get(key, AUTO)) != val:
                changed.append(f"{key}: {self.state.config.get(key, AUTO)} -> {val}")
            self.state.config[key] = val
        if changed:
            store.audit("Advanced settings changed: " + "; ".join(changed), "settings", detail={"changes": changed})
        self.state.config_changed.emit()
        self.accept()


class AutoPanel(QWidget):
    """Read-only 'AUTO Resolutions' panel."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Target:"))
        self.combo = QComboBox()
        self.combo.addItem("(all)")
        self.combo.currentTextChanged.connect(self._render)
        top.addWidget(self.combo, 1)
        self.only_auto = QCheckBox("AUTO only")
        self.only_auto.toggled.connect(self._render)
        top.addWidget(self.only_auto)
        lay.addLayout(top)
        self.tbl = make_table()
        lay.addWidget(self.tbl)
        self.records: list[dict] = []

    def set_records(self, records: list[dict]) -> None:
        self.records = records
        cur = self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("(all)")
        for t in sorted({r["target"] or "global" for r in records}):
            self.combo.addItem(t)
        i = self.combo.findText(cur)
        self.combo.setCurrentIndex(max(0, i))
        self.combo.blockSignals(False)
        self._render()

    def _render(self, *_):
        t = self.combo.currentText()
        rows = [r for r in self.records if t in ("(all)", "") or (r["target"] or "global") == t]
        if self.only_auto.isChecked():
            rows = [r for r in rows if r["mode"] == "AUTO"]
        df = pd.DataFrame(rows, columns=["target", "setting", "mode", "value", "signals", "rule"])
        fill_table(self.tbl, df)
