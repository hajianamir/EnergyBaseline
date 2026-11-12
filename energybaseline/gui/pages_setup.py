"""Setup (data + targets + temporal configuration) and Analysis (run / progress / cancel) pages."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSplitter, QTableWidgetItem, QTextEdit, QVBoxLayout,
                               QWidget)

from ..core import ingest, store
from ..core.auto import AutoLog
from ..core.pipeline import run_analysis
from ..core.temporal import TemporalError, prepare_temporal
from .widgets import PasteTable, fill_table, make_table

log = logging.getLogger("energybaseline.gui")
SAMPLING = ["AUTO", "15min", "30min", "hourly", "daily", "weekly", "monthly", "quarterly", "annual", "custom:300", "mixed"]
AGG = ["AUTO", "mean", "sum", "max", "first", "last"]
DST = ["AUTO", "wall_clock", "utc", "drop_ambiguous"]


class ManualEntryDialog(QDialog):
    """Manual dataset entry: define columns, type or paste rows (Ctrl+V from Excel)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manual data entry")
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Columns (comma-separated, first = timestamp):"))
        self.cols = QLineEdit("Timestamp, Energy_kWh, Temperature_C")
        row.addWidget(self.cols, 1)
        b = QPushButton("Apply columns")
        b.clicked.connect(self._apply)
        row.addWidget(b)
        lay.addLayout(row)
        self.tbl = PasteTable([c.strip() for c in self.cols.text().split(",")])
        for _ in range(12):
            self.tbl.insertRow(self.tbl.rowCount())
        lay.addWidget(self.tbl, 1)
        lay.addWidget(QLabel("Tip: copy cells in Excel and press Ctrl+V in the table; use Add row for more lines. Timestamps like 2024-01-31 13:00."))
        h = QHBoxLayout()
        add = QPushButton("Add row")
        add.clicked.connect(lambda: self.tbl.insertRow(self.tbl.rowCount()))
        h.addWidget(add)
        h.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        h.addWidget(bb)
        lay.addLayout(h)

    def _apply(self):
        self.tbl.set_columns([c.strip() for c in self.cols.text().split(",") if c.strip()])
        for _ in range(12):
            self.tbl.insertRow(self.tbl.rowCount())

    def frame(self) -> pd.DataFrame:
        df = self.tbl.to_frame()
        df = df.dropna(how="all")
        return ingest.coerce_numeric(df)


class SetupPage(QWidget):
    def __init__(self, state, main):
        super().__init__()
        self.state, self.main = state, main
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, fn in (("Open CSV / Excel / Access…", self.open_file), ("Manual entry…", self.manual_entry)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.info = QLabel("No dataset loaded. Sample datasets are in the 'samples' folder.")
        bar.addWidget(self.info, 1)
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(split, 1)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Data preview</b> (first rows, raw as imported)"))
        self.preview = make_table(False)
        ll.addWidget(self.preview, 3)
        ll.addWidget(QLabel("<b>Data dictionary / import validation</b>"))
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        ll.addWidget(self.report, 2)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        g1 = QGroupBox("Columns and targets")
        f1 = QFormLayout(g1)
        self.time_combo = QComboBox()
        f1.addRow("Timestamp column", self.time_combo)
        self.units = QLineEdit()
        self.units.setPlaceholderText("optional unit normalisation, e.g. Elec=MWh, Temp=degF")
        f1.addRow("Declared units", self.units)
        self.target_list = QListWidget()
        self.target_list.itemChanged.connect(self._targets_changed)
        f1.addRow("TARGET column(s) - one baseline per target", self.target_list)
        rl.addWidget(g1, 2)
        g2 = QGroupBox("Temporal configuration (AUTO detects and asks you to confirm)")
        f2 = QFormLayout(g2)
        self.sampling = self._combo(SAMPLING, True)
        self.agg = self._combo(AGG)
        self.tz = QLineEdit()
        self.tz.setPlaceholderText("AUTO (naive wall-clock) or IANA name, e.g. Europe/Berlin")
        self.dst = self._combo(DST)
        self.b0, self.b1 = QLineEdit(), QLineEdit()
        self.r0, self.r1 = QLineEdit(), QLineEdit()
        for w in (self.b0, self.b1, self.r0, self.r1):
            w.setPlaceholderText("AUTO / YYYY-MM-DD")
        f2.addRow("Sampling period", self.sampling)
        f2.addRow("Aggregation (if data finer)", self.agg)
        f2.addRow("Timezone", self.tz)
        f2.addRow("DST handling", self.dst)
        h = QHBoxLayout()
        h.addWidget(self.b0)
        h.addWidget(QLabel("to"))
        h.addWidget(self.b1)
        f2.addRow("Baseline period", h)
        h2 = QHBoxLayout()
        h2.addWidget(self.r0)
        h2.addWidget(QLabel("to"))
        h2.addWidget(self.r1)
        f2.addRow("Reporting period (optional)", h2)
        self.override_tbl = make_table(False)
        self.override_tbl.setColumnCount(2)
        self.override_tbl.setHorizontalHeaderLabels(["Target", "Sampling override (per target)"])
        self.override_tbl.horizontalHeader().setStretchLastSection(True)
        f2.addRow(self.override_tbl)
        vb = QPushButton("Validate temporal setup…")
        vb.clicked.connect(self.validate_temporal)
        f2.addRow(vb)
        rl.addWidget(g2, 3)
        split.addWidget(right)
        split.setSizes([620, 520])

    @staticmethod
    def _combo(items, editable=False) -> QComboBox:
        c = QComboBox()
        c.setEditable(editable)
        c.addItems(items)
        return c

    # ------------------------------------------------------------ data loading
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open dataset", str(Path.cwd() / "samples"),
                                              "Data files (*.csv *.txt *.xlsx *.xls *.mdb *.accdb);;All files (*)")
        if not path:
            return
        try:
            sheet = table = None
            ext = Path(path).suffix.lower()
            if ext in (".xlsx", ".xls", ".xlsm"):
                sheets = ingest.list_sheets(path)
                if len(sheets) > 1:
                    sheet, ok = QInputDialog.getItem(self, "Worksheet", "Select the worksheet:", sheets, 0, False)
                    if not ok:
                        return
            elif ext in (".mdb", ".accdb"):
                tabs = ingest.list_access_tables(path)
                table, ok = QInputDialog.getItem(self, "Access table", "Select the table:", tabs, 0, False)
                if not ok:
                    return
            df = ingest.load_file(path, sheet, table)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            log.error("import failed: %s", exc)
            return
        sha = ingest.sha256_file(path)
        ingest.retain_raw_copy(path)
        self.set_dataset(df, Path(path).name, path, sha)

    def manual_entry(self):
        dlg = ManualEntryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            df = dlg.frame()
            if df.empty:
                QMessageBox.warning(self, "Manual entry", "No rows were entered.")
                return
            import hashlib
            sha = hashlib.sha256(df.to_csv().encode()).hexdigest()
            self.set_dataset(df, "manual_entry", "manual", sha)

    def set_dataset(self, df: pd.DataFrame, name: str, path: str, sha: str):
        s = self.state
        s.df, s.dataset_name, s.dataset_path, s.dataset_sha = df, name, path, sha
        s.time_col = ingest.guess_time_column(df)
        s.dataset_id = store.register_dataset(name, path, sha, df)
        self.time_combo.clear()
        self.time_combo.addItems(list(df.columns))
        if s.time_col:
            self.time_combo.setCurrentText(s.time_col)
        self.target_list.blockSignals(True)
        self.target_list.clear()
        for c in df.columns:
            if c != s.time_col and pd.api.types.is_numeric_dtype(df[c]):
                it = QListWidgetItem(c)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Unchecked)
                self.target_list.addItem(it)
        # pre-tick the first energy-looking column
        for i in range(self.target_list.count()):
            if any(k in self.target_list.item(i).text().lower() for k in ("kwh", "energy", "elec", "gas", "consum", "m3")):
                self.target_list.item(i).setCheckState(Qt.CheckState.Checked)
                break
        self.target_list.blockSignals(False)
        self._targets_changed()
        fill_table(self.preview, df.head(300))
        dd = ingest.data_dictionary(df, s.time_col)
        s.data_dictionary = dd
        rep = ingest.import_report(df, s.time_col)
        html = (f"<b>{name}</b>: {rep['rows']} rows × {rep['columns']} columns; time column: <b>{rep['time_column']}</b>; "
                f"span: {rep.get('time_span', 'n/a')}<br>duplicate rows: {rep['duplicate_rows']}, duplicate timestamps: {rep.get('duplicate_timestamps', 'n/a')}, "
                f"unparseable timestamps: {rep.get('unparseable_timestamps', 'n/a')}<br>constant columns: {rep['constant_columns'] or 'none'}; "
                f"columns with missing values: {rep['columns_with_missing'] or 'none'}<br>" + "".join(f"<span style='color:#b45309'>⚠ {w}</span><br>" for w in rep["warnings"])
                + dd.round(3).to_html(index=False, border=0))
        self.report.setHtml(html)
        self.info.setText(f"{name}: {len(df)} rows, {df.shape[1]} columns (raw copy retained under data/uploads)")
        s.data_changed.emit()
        self.main.log_msg(f"Dataset loaded: {name}")

    def _targets_changed(self, *_):
        t = self.checked_targets()
        self.override_tbl.setRowCount(len(t))
        for i, name in enumerate(t):
            it = QTableWidgetItem(name)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.override_tbl.setItem(i, 0, it)
            cb = self._combo(SAMPLING, True)
            self.override_tbl.setCellWidget(i, 1, cb)

    def checked_targets(self) -> list[str]:
        return [self.target_list.item(i).text() for i in range(self.target_list.count()) if self.target_list.item(i).checkState() == Qt.CheckState.Checked]

    # ------------------------------------------------------------ settings
    def collect(self) -> None:
        """Push the Setup choices into state (AUTO entries do NOT override Advanced settings)."""
        s = self.state
        s.time_col = self.time_combo.currentText() or s.time_col
        s.targets = self.checked_targets()
        units = {}
        for part in self.units.text().split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                units[k.strip()] = v.strip()
        s.units = units
        cfg: dict = {}

        def put(key, val):
            val = (val or "").strip()
            if val and val.upper() != "AUTO":
                cfg[key] = val
        put("sampling_period", self.sampling.currentText())
        put("aggregation", self.agg.currentText())
        put("timezone", self.tz.text())
        put("dst_handling", self.dst.currentText())
        if self.b0.text().strip() and self.b1.text().strip():
            cfg["baseline_window"] = f"{self.b0.text().strip()},{self.b1.text().strip()}"
        if self.r0.text().strip() and self.r1.text().strip():
            cfg["reporting_window"] = f"{self.r0.text().strip()},{self.r1.text().strip()}"
        per = {}
        for i in range(self.override_tbl.rowCount()):
            t = self.override_tbl.item(i, 0).text()
            v = self.override_tbl.cellWidget(i, 1).currentText().strip()
            if v and v.upper() != "AUTO":
                per[t] = {"sampling_period": v}
        if per:
            cfg["per_target"] = per
        s.setup_config = cfg

    def validate_temporal(self):
        s = self.state
        if s.df is None:
            QMessageBox.information(self, "Temporal setup", "Load a dataset first.")
            return
        self.collect()
        if not s.targets:
            QMessageBox.information(self, "Temporal setup", "Tick at least one TARGET column.")
            return
        msgs = []
        for t in s.targets:
            cfg = s.effective_config()
            cfg.update((cfg.get("per_target") or {}).get(t, {}))
            auto = AutoLog({k: v for k, v in cfg.items() if k != "per_target"}, t)
            try:
                out, rep, g = prepare_temporal(self.apply_units(s.df), s.time_col, auto)
                m = (f"<b>{t}</b>: declared <b>{rep.declared}</b>, detected <b>{rep.detected}</b> (regularity {rep.regularity:.0%}) → applied grid '{rep.applied}', "
                     f"{rep.n_input_rows} → {rep.n_output_rows} rows, duplicates {rep.n_duplicates}, gap intervals {rep.n_gaps_filled_as_missing}"
                     + ("<br><i>Sampling period was AUTO-detected - please confirm it is correct.</i>" if rep.needs_confirmation else ""))
                m += "".join(f"<br>⚠ {x}" for x in rep.messages)
            except TemporalError as exc:
                m = f"<b>{t}</b>: <span style='color:#b42318'>REJECTED - {exc}</span>"
            except Exception as exc:
                m = f"<b>{t}</b>: error - {exc}"
            msgs.append(m)
        box = QMessageBox(self)
        box.setWindowTitle("Temporal validation")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br><br>".join(msgs))
        box.exec()

    def apply_units(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.state.units:
            return df
        out, log_ = ingest.normalize_units(df, self.state.units)
        for m in log_:
            store.audit("Unit normalisation: " + m, "ingest")
        return out


class AnalysisWorker(QThread):
    progress = Signal(str, float, str)
    done = Signal(object, int)
    failed = Signal(str)

    def __init__(self, df, time_col, targets, config, dataset_sha, dataset_info, dataset_id):
        super().__init__()
        self.args = (df, time_col, targets, config, dataset_sha, dataset_info, dataset_id)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        df, time_col, targets, config, sha, info, did = self.args
        try:
            rid = store.new_run(did, targets, config)
            run, bundles = run_analysis(df, time_col, targets, config, dataset_sha=sha, dataset_info=info,
                                        progress=lambda t, p, m: self.progress.emit(t, p, m), cancel=lambda: self._cancel,
                                        artifact_fn=lambda t, m: str(store.artifact_path(rid, t, m)))
            store.save_run(rid, run, bundles)
            self.done.emit(run, rid)
        except Exception as exc:  # the GUI must never crash because of an analysis problem
            log.exception("analysis crashed")
            self.failed.emit(str(exc))


class AnalysisPage(QWidget):
    def __init__(self, state, main):
        super().__init__()
        self.state, self.main = state, main
        self.worker: AnalysisWorker | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Run the full pipeline</b>: temporal validation → quality & preprocessing → EDA → data mining → feature engineering/selection → "
                             "model portfolio incl. neural networks → scoring → Top-5 shortlist (independently for each target)."))
        bar = QHBoxLayout()
        self.run_btn = QPushButton("Run analysis")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        bar.addWidget(self.run_btn)
        bar.addWidget(self.cancel_btn)
        self.status = QLabel("Idle.")
        bar.addWidget(self.status, 1)
        lay.addLayout(bar)
        self.bars_box = QGroupBox("Per-target progress")
        self.bars_lay = QFormLayout(self.bars_box)
        lay.addWidget(self.bars_box)
        self.summary = make_table()
        lay.addWidget(QLabel("<b>Result summary</b>"))
        lay.addWidget(self.summary, 1)
        self.bars: dict[str, tuple[QProgressBar, QLabel]] = {}

    def start(self):
        s = self.state
        self.main.setup_page.collect()
        if s.df is None:
            QMessageBox.information(self, "Run analysis", "Load a dataset on the Setup tab first.")
            return
        if not s.targets:
            QMessageBox.information(self, "Run analysis", "Tick at least one TARGET column on the Setup tab.")
            return
        if not s.time_col:
            QMessageBox.information(self, "Run analysis", "Select the timestamp column on the Setup tab.")
            return
        df = self.main.setup_page.apply_units(s.df)
        while self.bars_lay.rowCount():
            self.bars_lay.removeRow(0)
        self.bars = {}
        for t in s.targets:
            pb, lab = QProgressBar(), QLabel("queued")
            pb.setRange(0, 100)
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(pb, 1)
            hl.addWidget(lab, 2)
            self.bars_lay.addRow(t, row)
            self.bars[t] = (pb, lab)
        info = {"name": s.dataset_name, "rows": len(s.df), "cols": s.df.shape[1], "description": f"{s.dataset_name} - {len(s.df)} rows; time column '{s.time_col}'; targets {s.targets}."}
        self.worker = AnalysisWorker(df, s.time_col, list(s.targets), s.effective_config(), s.dataset_sha, info, s.dataset_id)
        self.worker.progress.connect(self._progress)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status.setText("Running…")
        self.main.log_msg(f"Analysis started for {s.targets}")
        self.worker.start()

    def cancel(self):
        if self.worker:
            self.worker.cancel()
            self.status.setText("Cancelling after the current step…")

    def _progress(self, target, pct, msg):
        if target in self.bars:
            self.bars[target][0].setValue(int(pct))
            self.bars[target][1].setText(msg)
        self.main.statusBar().showMessage(f"{target}: {msg}")

    def _failed(self, msg):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status.setText("Failed.")
        QMessageBox.critical(self, "Analysis failed", msg)

    def _done(self, run, rid):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status.setText("Cancelled." if run.cancelled else f"Completed (run #{rid}).")
        self.state.run, self.state.run_id, self.state.locked = run, rid, {}
        rows = []
        for t, tr in run.targets.items():
            best = tr.shortlist[0] if tr.shortlist else None
            rows.append({"target": t, "status": tr.status, "granularity": tr.granularity, "n_obs": tr.n_obs, "quality": tr.quality.get("score"),
                         "best_model": best["name"] if best else "", "cvrmse_%": best["metrics"]["cvrmse"] if best else None,
                         "r2": best["metrics"]["r2"] if best else None, "shortlisted": len(tr.shortlist), "notice": tr.notice or tr.error})
        fill_table(self.summary, pd.DataFrame(rows))
        self.state.run_changed.emit()
        self.main.log_msg(f"Analysis finished (run #{rid}). Open 'Baseline Selection' to choose a baseline per target.")
        QMessageBox.information(self, "Analysis finished", "Analysis complete. Review the results and choose one baseline per target on the 'Baseline Selection' tab.")
        self.main.show_page("selection")
