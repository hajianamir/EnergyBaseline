"""Prediction & Monitoring: file / SQL / manual input -> baseline deviation dashboard."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateTimeEdit, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from ..core import ingest, monitor, report, sqlsource as sq, store
from ..core.auto import is_auto
from .sql_dialog import SqlDialog
from .widgets import FuncWorker, KpiCard, MplCanvas, PasteTable, fill_table, make_table

log = logging.getLogger("energybaseline.gui")
MODES = {"percent": "± percent of baseline", "cvrmse_multiple": "± multiple of RMSE (k × CV(RMSE))", "interval": "outside the 95 % prediction interval"}


class MonitorPage(QWidget):
    def __init__(self, state, main):
        super().__init__()
        self.state, self.main = state, main
        self.bundles: dict[str, dict] = {}
        self.input_df: pd.DataFrame | None = None
        self.results: dict[str, pd.DataFrame] = {}
        self.kpi_by_target: dict[str, dict] = {}
        outer = QVBoxLayout(self)
        self.banner = QLabel("Prediction & Monitoring is locked: select and lock a baseline on the 'Baseline Selection' tab first.")
        self.banner.setObjectName("banner")
        outer.addWidget(self.banner)
        self.body = QWidget()
        outer.addWidget(self.body, 1)
        lay = QHBoxLayout(self.body)
        # ---------------- left: controls
        left = QWidget()
        left.setMaximumWidth(470)
        ll = QVBoxLayout(left)
        g0 = QGroupBox("Baseline")
        f0 = QFormLayout(g0)
        self.target_combo = QComboBox()
        self.target_combo.currentTextChanged.connect(self._target_changed)
        self.side = QCheckBox("Show all locked targets side by side")
        self.side.toggled.connect(self.redraw)
        self.bl_label = QLabel("-")
        self.bl_label.setWordWrap(True)
        f0.addRow("Target", self.target_combo)
        f0.addRow(self.side)
        f0.addRow(self.bl_label)
        ll.addWidget(g0)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._file_tab(), "1 · File upload")
        self.tabs.addTab(self._sql_tab(), "2 · External SQL")
        self.tabs.addTab(self._manual_tab(), "3 · Manual entry")
        ll.addWidget(self.tabs, 1)
        g3 = QGroupBox("Alert thresholds")
        f3 = QFormLayout(g3)
        self.auto_alert = QCheckBox("AUTO (max(10 %, 2 × CV(RMSE)) rule)")
        self.auto_alert.setChecked(True)
        self.mode = QComboBox()
        for k, v in MODES.items():
            self.mode.addItem(v, k)
        self.val = QDoubleSpinBox()
        self.val.setRange(0.1, 1000)
        self.val.setValue(10.0)
        self.auto_alert.toggled.connect(lambda on: (self.mode.setEnabled(not on), self.val.setEnabled(not on)))
        self.mode.setEnabled(False)
        self.val.setEnabled(False)
        f3.addRow(self.auto_alert)
        f3.addRow("Mode", self.mode)
        f3.addRow("Value", self.val)
        ll.addWidget(g3)
        g4 = QGroupBox("Filter")
        f4 = QFormLayout(g4)
        self.use_range = QCheckBox("Restrict date range")
        self.d0, self.d1 = QDateTimeEdit(QDateTime.currentDateTime().addYears(-1)), QDateTimeEdit(QDateTime.currentDateTime())
        for d in (self.d0, self.d1):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.use_range.toggled.connect(self.redraw)
        f4.addRow(self.use_range)
        f4.addRow("From", self.d0)
        f4.addRow("To", self.d1)
        ll.addWidget(g4)
        self.run_btn = QPushButton("Compute baseline && deviation")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.compute)
        ll.addWidget(self.run_btn)
        lay.addWidget(left)
        # ---------------- right: dashboard
        right = QWidget()
        rl = QVBoxLayout(right)
        self.schema_box = QTextEdit()
        self.schema_box.setReadOnly(True)
        self.schema_box.setMaximumHeight(90)
        rl.addWidget(self.schema_box)
        self.kpi_grid = QGridLayout()
        titles = ["Total deviation", "Total deviation %", "Mean deviation %", "Exceedances", "Max deviation at"]
        self.cards = [KpiCard(t) for t in titles]
        for i, c in enumerate(self.cards):
            self.kpi_grid.addWidget(c, 0, i)
        rl.addLayout(self.kpi_grid)
        self.kpi_table = make_table()
        self.kpi_table.setMaximumHeight(110)
        self.kpi_table.hide()
        rl.addWidget(self.kpi_table)
        self.canvas = MplCanvas((9, 5.2))
        rl.addWidget(self.canvas, 3)
        h = QHBoxLayout()
        h.addWidget(QLabel("Top-N deviating records:"))
        self.topn = QSpinBox()
        self.topn.setRange(5, 500)
        self.topn.setValue(20)
        self.topn.valueChanged.connect(self.redraw)
        h.addWidget(self.topn)
        h.addStretch(1)
        for text, fn in (("Export PDF", self.export_pdf), ("Export Excel", self.export_xlsx), ("Save snapshot", self.save_snapshot), ("Load snapshot…", self.load_snapshot)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            h.addWidget(b)
        rl.addLayout(h)
        self.tbl = make_table()
        rl.addWidget(self.tbl, 2)
        lay.addWidget(right, 1)
        state.selection_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------ input tabs
    def _file_tab(self) -> QWidget:
        w = QWidget()
        l = QFormLayout(w)
        b = QPushButton("Browse Excel / CSV…")
        b.clicked.connect(self.load_file)
        self.file_label = QLabel("No file loaded")
        self.time_combo = QComboBox()
        l.addRow(b)
        l.addRow(self.file_label)
        l.addRow("Timestamp column", self.time_combo)
        l.addRow(QLabel("Columns are validated against the baseline's required predictors before prediction."))
        return w

    def _sql_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        row = QHBoxLayout()
        self.conn_combo = QComboBox()
        self.conn_combo.currentTextChanged.connect(self._conn_changed)
        mb = QPushButton("Connections…")
        mb.clicked.connect(self.manage_connections)
        row.addWidget(self.conn_combo, 1)
        row.addWidget(mb)
        l.addLayout(row)
        self.tpl_combo = QComboBox()
        self.tpl_combo.currentTextChanged.connect(lambda n: n and self.query.setPlainText(self._profile_queries().get(n, "")))
        l.addWidget(self.tpl_combo)
        qb = QGroupBox("Query builder")
        qf = QFormLayout(qb)
        self.q_table, self.q_time = QLineEdit(), QLineEdit()
        self.q_from, self.q_to = QLineEdit(), QLineEdit()
        self.q_from.setPlaceholderText("YYYY-MM-DD")
        self.q_to.setPlaceholderText("YYYY-MM-DD")
        qf.addRow("Table / view", self.q_table)
        qf.addRow("Timestamp column", self.q_time)
        qf.addRow("From", self.q_from)
        qf.addRow("To", self.q_to)
        bb = QPushButton("Build SELECT from required columns")
        bb.clicked.connect(self.build_query)
        qf.addRow(bb)
        l.addWidget(qb)
        self.query = QPlainTextEdit()
        self.query.setPlaceholderText("SELECT ts, col1, col2 FROM table WHERE …  (read-only SELECT queries only)")
        l.addWidget(self.query, 1)
        self.sql_btn = QPushButton("Run query")
        self.sql_btn.clicked.connect(self.run_sql)
        l.addWidget(self.sql_btn)
        self.sql_msg = QLabel("")
        self.sql_msg.setWordWrap(True)
        l.addWidget(self.sql_msg)
        self._reload_connections()
        return w

    def _manual_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        self.form_box = QGroupBox("Single observation (auto-generated from required predictors)")
        self.form_lay = QFormLayout(self.form_box)
        l.addWidget(self.form_box)
        b = QPushButton("Add to batch table ↓")
        b.clicked.connect(self.add_manual_row)
        l.addWidget(b)
        self.batch = PasteTable([])
        l.addWidget(self.batch, 1)
        l.addWidget(QLabel("Batch: type rows, or paste from the clipboard (Ctrl+V)."))
        bu = QPushButton("Use batch table as input")
        bu.clicked.connect(self.use_batch)
        l.addWidget(bu)
        self.form_widgets: dict[str, QWidget] = {}
        self.range_hint: dict[str, tuple] = {}
        return w

    # ------------------------------------------------------------ state
    def refresh(self):
        locked = self.state.locked
        self.body.setEnabled(bool(locked))
        self.banner.setVisible(not locked)
        cur = self.target_combo.currentText()
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItems(list(locked))
        i = self.target_combo.findText(cur)
        self.target_combo.setCurrentIndex(max(0, i))
        self.target_combo.blockSignals(False)
        self.bundles = {}
        self._target_changed(self.target_combo.currentText())

    def bundle(self, target: str) -> dict:
        sel = self.state.locked[target]
        key = target
        if key not in self.bundles or self.bundles[key]["model_name"] != sel["model"]:
            self.bundles[key] = store.load_bundle(self.state.run_id, target, sel["model"])
        return self.bundles[key]

    def _target_changed(self, t):
        if not t or t not in self.state.locked:
            return
        try:
            b = self.bundle(t)
        except Exception as exc:
            self.bl_label.setText(f"Could not load the baseline artifact: {exc}")
            return
        m = b["metrics"]
        self.bl_label.setText(f"<b>{b['model_name']}</b> (run #{self.state.run_id}); CV(RMSE) {m['cvrmse']:.2f} %, R² {m['r2']:.3f}; granularity {b['granularity']}<br>"
                              f"Required columns: {', '.join(b['required_columns']) or '(none)'}<br>Default alert: {b['alert']['mode']} {b['alert']['value']:g} - {b['alert']['why']}")
        self._build_form(b)

    def _build_form(self, b):
        while self.form_lay.rowCount():
            self.form_lay.removeRow(0)
        self.form_widgets, self.range_hint = {}, {}
        fields = monitor.manual_template(b)
        for f in fields:
            if f["kind"] == "datetime":
                w: QWidget = QDateTimeEdit(QDateTime.currentDateTime())
                w.setCalendarPopup(True)
                w.setDisplayFormat("yyyy-MM-dd HH:mm")
            elif f["kind"] == "choice":
                w = QComboBox()
                w.addItems([str(c) for c in f["choices"]])
            else:
                w = QLineEdit()
                w.setPlaceholderText(f["hint"] + (" (optional)" if f["kind"] == "number_optional" else ""))
                if f["kind"] == "number":
                    self.range_hint[f["name"]] = (f.get("min"), f.get("max"))
                    w.textChanged.connect(lambda _t, n=f["name"], ww=w: self._range_check(n, ww))
            self.form_widgets[f["name"]] = w
            self.form_lay.addRow(f["name"], w)
        self.batch.set_columns([f["name"] for f in fields])

    def _range_check(self, name, w):
        lo, hi = self.range_hint.get(name, (None, None))
        try:
            v = float(w.text().replace(",", "."))
        except ValueError:
            w.setStyleSheet("background:#ffe0e0" if w.text() else "")
            return
        warn = lo is not None and (v < lo - 0.1 * (hi - lo) or v > hi + 0.1 * (hi - lo))
        w.setStyleSheet("background:#fff3cd" if warn else "")
        w.setToolTip("Outside the training range - extrapolation risk" if warn else "")

    # ------------------------------------------------------------ inputs
    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open monitoring data", str(Path.cwd() / "samples"), "Data (*.csv *.txt *.xlsx *.xls)")
        if not path:
            return
        try:
            sheet = None
            if Path(path).suffix.lower() in (".xlsx", ".xls"):
                sheets = ingest.list_sheets(path)
                if len(sheets) > 1:
                    sheet, ok = QInputDialog.getItem(self, "Worksheet", "Select the worksheet:", sheets, 0, False)
                    if not ok:
                        return
            df = ingest.load_file(path, sheet)
        except Exception as exc:
            QMessageBox.critical(self, "Open", str(exc))
            return
        self.set_input(df, Path(path).name)

    def set_input(self, df: pd.DataFrame, label: str):
        self.input_df = df
        self.file_label.setText(f"{label}: {len(df)} rows, {df.shape[1]} columns")
        self.time_combo.clear()
        self.time_combo.addItems(["(none)"] + list(df.columns))
        g = ingest.guess_time_column(df)
        if g:
            self.time_combo.setCurrentText(g)
        self.main.log_msg(f"Monitoring input loaded: {label} ({len(df)} rows)")

    def _profiles(self):
        try:
            return {p.name: p for p in sq.load_profiles()}
        except Exception:
            return {}

    def _profile_queries(self) -> dict:
        p = self._profiles().get(self.conn_combo.currentText())
        return p.queries if p else {}

    def _reload_connections(self):
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        self.conn_combo.addItems(list(self._profiles()))
        self.conn_combo.blockSignals(False)
        self._conn_changed(self.conn_combo.currentText())

    def _conn_changed(self, _n):
        self.tpl_combo.clear()
        self.tpl_combo.addItems(list(self._profile_queries()))

    def manage_connections(self):
        SqlDialog(self).exec()
        self._reload_connections()

    def build_query(self):
        t = self.target_combo.currentText()
        if not t:
            return
        b = self.bundle(t)
        try:
            tc = self.q_time.text().strip() or None
            cols = ([tc] if tc else []) + list(b["required_columns"]) + [b["target"]]
            self.query.setPlainText(sq.build_query(self.q_table.text().strip(), cols, tc, self.q_from.text().strip() or None, self.q_to.text().strip() or None))
        except Exception as exc:
            QMessageBox.warning(self, "Query builder", str(exc))

    def run_sql(self):
        prof = self._profiles().get(self.conn_combo.currentText())
        if not prof:
            QMessageBox.information(self, "SQL", "Create or select a connection first (Connections…).")
            return
        self.sql_btn.setEnabled(False)
        self.sql_msg.setText("Querying… (the app stays usable offline; unreachable servers time out after ~10 s)")
        self.sw = FuncWorker(sq.run_query, prof, self.query.toPlainText())
        self.sw.ok.connect(self._sql_ok)
        self.sw.fail.connect(self._sql_fail)
        self.sw.start()

    def _sql_ok(self, df):
        self.sql_btn.setEnabled(True)
        self.sql_msg.setText(f"✔ {len(df)} rows received.")
        self.set_input(ingest.coerce_numeric(df), "SQL query")
        store.audit(f"SQL data pulled: {len(df)} rows via '{self.conn_combo.currentText()}'", "monitoring", run_id=self.state.run_id)

    def _sql_fail(self, msg):
        self.sql_btn.setEnabled(True)
        self.sql_msg.setText("✖ " + msg)
        log.warning("sql failure: %s", msg)

    def add_manual_row(self):
        vals = []
        for name, w in self.form_widgets.items():
            if isinstance(w, QDateTimeEdit):
                vals.append(w.dateTime().toString("yyyy-MM-dd HH:mm:ss"))
            elif isinstance(w, QComboBox):
                vals.append(w.currentText())
            else:
                vals.append(w.text().strip())
        self.batch.add_row(vals)

    def use_batch(self):
        df = self.batch.to_frame().dropna(how="all")
        if df.empty:
            QMessageBox.information(self, "Manual", "The batch table is empty.")
            return
        self.set_input(df, "manual batch")
        self.time_combo.setCurrentText("timestamp" if "timestamp" in df.columns else "(none)")

    # ------------------------------------------------------------ compute
    def _alert(self, b) -> dict | None:
        if self.auto_alert.isChecked():
            return None
        return {"mode": self.mode.currentData(), "value": self.val.value(), "why": "user-defined in the monitoring module"}

    def compute(self):
        if self.input_df is None:
            QMessageBox.information(self, "Monitoring", "Provide data first (file, SQL or manual entry).")
            return
        tc = self.time_combo.currentText()
        tc = None if tc in ("", "(none)") else tc
        targets = list(self.state.locked) if self.side.isChecked() else [self.target_combo.currentText()]
        self.results, self.kpi_by_target, notes = {}, {}, []
        for t in targets:
            try:
                b = self.bundle(t)
                rep = monitor.schema_report(self.input_df, b, tc)
                msg = f"<b>{t}</b>: " + ("OK" if rep["ok"] else "<span style='color:#b42318'>SCHEMA PROBLEM</span>")
                if rep["missing_columns"]:
                    msg += f" - missing columns: {', '.join(rep['missing_columns'])}"
                for k in ("dtype_mismatches", "range_warnings"):
                    msg += "".join(f"<br>⚠ {x}" for x in rep[k])
                if rep["time_issue"]:
                    msg += f"<br>⚠ {rep['time_issue']}"
                if not rep["has_actual"]:
                    msg += "<br>ℹ no actual column: baseline predictions only"
                notes.append(msg)
                if rep["missing_columns"]:
                    continue
                alert = self._alert(b)
                if alert is None:
                    alert = b["alert"]
                res = monitor.predict_deviation(self.input_df, b, tc, alert)
                res.attrs["rmse"] = b["rmse"]
                self.results[t] = res
                self.kpi_by_target[t] = monitor.kpis(res)
                notes += [f"&nbsp;&nbsp;{n}" for n in res.attrs.get("notes", [])]
            except Exception as exc:
                notes.append(f"<b>{t}</b>: <span style='color:#b42318'>{exc}</span>")
                log.warning("monitor failed for %s: %s", t, exc)
        self.schema_box.setHtml("<br>".join(notes))
        if self.results:
            store.audit(f"Monitoring computed for {list(self.results)} ({len(self.input_df)} input rows)", "monitoring", run_id=self.state.run_id)
        self.redraw()

    def _filtered(self) -> dict[str, pd.DataFrame]:
        out = {}
        for t, df in self.results.items():
            d = df
            if self.use_range.isChecked() and isinstance(df.index, pd.DatetimeIndex):
                a = pd.Timestamp(self.d0.dateTime().toString("yyyy-MM-dd HH:mm:ss"))
                z = pd.Timestamp(self.d1.dateTime().toString("yyyy-MM-dd HH:mm:ss"))
                d = df.loc[(df.index >= a) & (df.index <= z)]
                d.attrs.update(df.attrs)
            out[t] = d
        return out

    def redraw(self, *_):
        data = self._filtered()
        fig = self.canvas.fig
        fig.clear()
        if not data:
            self.canvas.draw_idle()
            return
        n = len(data)
        for i, (t, df) in enumerate(data.items()):
            a1 = fig.add_subplot(2, n, i + 1)
            a2 = fig.add_subplot(2, n, n + i + 1, sharex=a1)
            report.draw_dashboard_axes(a1, a2, t, df)
        self.canvas.draw_idle()
        kp = {t: monitor.kpis(d) for t, d in data.items()}
        self.kpi_by_target = kp
        if n == 1:
            self.kpi_table.hide()
            for c in self.cards:
                c.show()
            k = next(iter(kp.values()))
            if k.get("has_actual"):
                self.cards[0].set(f"{k['total_deviation']:+,.5g}")
                self.cards[1].set("-" if k["total_deviation_pct"] is None else f"{k['total_deviation_pct']:+.2f} %")
                self.cards[2].set(f"{k['mean_deviation_pct']:+.2f} %")
                self.cards[3].set(f"{k['exceedances']} ({k['exceedance_share_pct']:.1f} %)")
                self.cards[4].set(k["max_deviation_at"][:16])
            else:
                for c in self.cards:
                    c.set("n/a")
            d0 = next(iter(data.values()))
            fill_table(self.tbl, monitor.top_deviations(d0, self.topn.value()).reset_index().rename(columns={"index": "timestamp"}))
        else:
            for c in self.cards:
                c.hide()
            self.kpi_table.show()
            fill_table(self.kpi_table, pd.DataFrame([{"target": t, **{k_: v for k_, v in k.items() if k_ not in ("period",)}} for t, k in kp.items()]))
            frames = []
            for t, d in data.items():
                x = monitor.top_deviations(d, self.topn.value()).reset_index().rename(columns={"index": "timestamp"})
                x.insert(0, "target", t)
                frames.append(x)
            fill_table(self.tbl, pd.concat(frames) if frames else None)

    # ------------------------------------------------------------ exports
    def export_pdf(self):
        data = self._filtered()
        if not data:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export dashboard PDF", "dashboard.pdf", "PDF (*.pdf)")
        if p:
            report.export_dashboard_pdf(data, self.kpi_by_target, p)
            self.main.log_msg(f"Dashboard exported: {p}")

    def export_xlsx(self):
        data = self._filtered()
        if not data:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export dashboard Excel", "dashboard.xlsx", "Excel (*.xlsx)")
        if p:
            report.export_dashboard_excel(data, self.kpi_by_target, p)
            self.main.log_msg(f"Dashboard exported: {p}")

    def save_snapshot(self):
        if not self.results:
            return
        name, ok = QInputDialog.getText(self, "Snapshot", "Snapshot name:", text=pd.Timestamp.now().strftime("session %Y-%m-%d %H:%M"))
        if not ok:
            return
        for t, df in self.results.items():
            store.save_snapshot(self.state.run_id, t, name, df, monitor.kpis(df))
        self.main.log_msg(f"Snapshot '{name}' saved to SQLite")

    def load_snapshot(self):
        snaps = store.list_snapshots()
        if not snaps:
            QMessageBox.information(self, "Snapshots", "No snapshots stored yet.")
            return
        labels = [f"#{s['id']} · {s['name']} · {s['target']} · {s['created']}" for s in snaps]
        pick, ok = QInputDialog.getItem(self, "Load snapshot", "Snapshot:", labels, 0, False)
        if ok:
            s = snaps[labels.index(pick)]
            df = store.load_snapshot(s["id"])
            df.attrs.update({"alert": {"mode": "interval", "value": 0}, "rmse": float(df["deviation"].std() or 1.0)})
            self.results = {s["target"]: df}
            self.redraw()
