"""Setup (data + targets + temporal configuration) and Analysis (process-pool run / progress / cancel) pages."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
import hashlib
import logging
import os
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import ingest, store
from ..core.auto import AutoLog
from ..core.pool import PoolJobHandle, get_pool_manager
from ..core.temporal import TemporalError, prepare_temporal
from ..io.paths import PathGuardError, resolve_picked_path
from .widgets import PasteTable, fill_table, make_table

log = logging.getLogger("energybaseline.gui")
SAMPLING = [
    "AUTO",
    "15min",
    "30min",
    "hourly",
    "daily",
    "weekly",
    "monthly",
    "quarterly",
    "annual",
    "custom:300",
    "mixed",
]
AGG = ["AUTO", "mean", "sum", "max", "first", "last"]
DST = ["AUTO", "wall_clock", "utc", "drop_ambiguous"]


def _is_interactive() -> bool:
    return os.environ.get("EB_NONINTERACTIVE") != "1"


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
        lay.addWidget(
            QLabel(
                "Tip: copy cells in Excel and press Ctrl+V in the table; use Add row for more lines. "
                "Timestamps like 2024-01-31 13:00."
            )
        )
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
        for text, fn in (
            ("Open CSV / Excel / Access…", lambda: self.open_file()),
            ("Manual entry…", self.manual_entry),
        ):
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
    def open_file(
        self,
        picked_path: str | Path | None = None,
        *,
        allowed_roots: Sequence[str | Path] | None = None,
    ) -> bool:
        if picked_path is None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Open dataset",
                str(Path.cwd() / "samples"),
                "Data files (*.csv *.txt *.xlsx *.xls *.mdb *.accdb);;All files (*)",
            )
            if not path:
                return False
        else:
            path = str(picked_path)

        try:
            safe_path = resolve_picked_path(path, allowed_roots=allowed_roots)
        except PathGuardError as exc:
            msg = f"Rejected unsafe file path: {exc}"
            self.info.setText(msg)
            log.warning("setup.path_guard_rejected path=%s error=%s", path, exc)
            if _is_interactive():
                QMessageBox.critical(self, "Unsafe path rejected", msg)
            return False

        safe_str = str(safe_path)
        try:
            sheet = table = None
            ext = safe_path.suffix.lower()
            if ext in (".xlsx", ".xls", ".xlsm"):
                sheets = ingest.list_sheets(safe_str)
                if len(sheets) > 1 and _is_interactive():
                    sheet, ok = QInputDialog.getItem(self, "Worksheet", "Select the worksheet:", sheets, 0, False)
                    if not ok:
                        return False
                elif sheets:
                    sheet = sheets[0]
            elif ext in (".mdb", ".accdb"):
                tabs = ingest.list_access_tables(safe_str)
                if _is_interactive():
                    table, ok = QInputDialog.getItem(self, "Access table", "Select the table:", tabs, 0, False)
                    if not ok:
                        return False
                elif tabs:
                    table = tabs[0]
            df = ingest.load_file(safe_str, sheet, table)
        except Exception as exc:
            if _is_interactive():
                QMessageBox.critical(self, "Import failed", str(exc))
            self.info.setText(f"Import failed: {exc}")
            log.error("import failed: %s", exc)
            return False

        sha = ingest.sha256_file(safe_str)
        ingest.retain_raw_copy(safe_str)
        self.set_dataset(df, safe_path.name, safe_str, sha)
        return True

    def manual_entry(self):
        dlg = ManualEntryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            df = dlg.frame()
            if df.empty:
                if _is_interactive():
                    QMessageBox.warning(self, "Manual entry", "No rows were entered.")
                return
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
            if any(
                k in self.target_list.item(i).text().lower() for k in ("kwh", "energy", "elec", "gas", "consum", "m3")
            ):
                self.target_list.item(i).setCheckState(Qt.CheckState.Checked)
                break
        self.target_list.blockSignals(False)
        self._targets_changed()
        fill_table(self.preview, df.head(300))
        dd = ingest.data_dictionary(df, s.time_col)
        s.data_dictionary = dd
        rep = ingest.import_report(df, s.time_col)
        html = (
            f"<b>{name}</b>: {rep['rows']} rows \u00d7 {rep['columns']} columns; time column: <b>{rep['time_column']}</b>; "
            f"span: {rep.get('time_span', 'n/a')}<br>duplicate rows: {rep['duplicate_rows']}, "
            f"duplicate timestamps: {rep.get('duplicate_timestamps', 'n/a')}, "
            f"unparseable timestamps: {rep.get('unparseable_timestamps', 'n/a')}<br>"
            f"constant columns: {rep['constant_columns'] or 'none'}; "
            f"columns with missing values: {rep['columns_with_missing'] or 'none'}<br>"
            + "".join(f"<span style='color:#b45309'>\u26a0 {w}</span><br>" for w in rep["warnings"])
            + dd.round(3).to_html(index=False, border=0)
        )
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
        return [
            self.target_list.item(i).text()
            for i in range(self.target_list.count())
            if self.target_list.item(i).checkState() == Qt.CheckState.Checked
        ]

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
            if _is_interactive():
                QMessageBox.information(self, "Temporal setup", "Load a dataset first.")
            return
        self.collect()
        if not s.targets:
            if _is_interactive():
                QMessageBox.information(self, "Temporal setup", "Tick at least one TARGET column.")
            return
        msgs = []
        for t in s.targets:
            cfg = s.effective_config()
            cfg.update((cfg.get("per_target") or {}).get(t, {}))
            auto = AutoLog({k: v for k, v in cfg.items() if k != "per_target"}, t)
            try:
                out, rep, g = prepare_temporal(self.apply_units(s.df), s.time_col, auto)
                _ = (out, g)
                m = (
                    f"<b>{t}</b>: declared <b>{rep.declared}</b>, detected <b>{rep.detected}</b> "
                    f"(regularity {rep.regularity:.0%}) \u2192 applied grid '{rep.applied}', "
                    f"{rep.n_input_rows} \u2192 {rep.n_output_rows} rows, duplicates {rep.n_duplicates}, "
                    f"gap intervals {rep.n_gaps_filled_as_missing}"
                    + (
                        "<br><i>Sampling period was AUTO-detected - please confirm it is correct.</i>"
                        if rep.needs_confirmation
                        else ""
                    )
                )
                m += "".join(f"<br>\u26a0 {x}" for x in rep.messages)
            except TemporalError as exc:
                m = f"<b>{t}</b>: <span style='color:#b42318'>REJECTED - {exc}</span>"
            except Exception as exc:
                m = f"<b>{t}</b>: error - {exc}"
            msgs.append(m)
        if _is_interactive():
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


class AnalysisPage(QWidget):
    """Dispatches ``run_analysis`` to ``AnalysisPoolManager`` (spawn ProcessPoolExecutor; zero QThread)."""

    def __init__(self, state, main):
        super().__init__()
        self.state, self.main = state, main
        self._job_handle: PoolJobHandle | None = None
        self._active_run_id: int | None = None
        self._progress_events: deque[tuple[str, float, str]] = deque()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(40)
        self._poll_timer.timeout.connect(self._poll_pool)

        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "<b>Run the full pipeline</b>: temporal validation \u2192 quality & preprocessing \u2192 EDA \u2192 "
                "data mining \u2192 feature engineering/selection \u2192 model portfolio incl. neural networks \u2192 "
                "scoring \u2192 Top-5 shortlist (isolated worker process pool, independently for each target)."
            )
        )
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
        if self._job_handle is not None and not self._job_handle.done_event.is_set():
            return
        s = self.state
        self.main.setup_page.collect()
        if s.df is None:
            if _is_interactive():
                QMessageBox.information(self, "Run analysis", "Load a dataset on the Setup tab first.")
            return
        if not s.targets:
            if _is_interactive():
                QMessageBox.information(self, "Run analysis", "Tick at least one TARGET column on the Setup tab.")
            return
        if not s.time_col:
            if _is_interactive():
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

        cfg = s.effective_config()
        info = {
            "name": s.dataset_name,
            "rows": len(s.df),
            "cols": s.df.shape[1],
            "description": f"{s.dataset_name} - {len(s.df)} rows; time column '{s.time_col}'; targets {s.targets}.",
        }
        # Persist Run row on UI thread only (SHL-5: workers never open SQLite).
        rid = store.new_run(s.dataset_id, list(s.targets), cfg)
        self._active_run_id = rid
        artifact_dirs = {t: str(store.artifact_dir(rid, t)) for t in s.targets}
        self._progress_events.clear()

        mgr = get_pool_manager()
        self._job_handle = mgr.submit_analysis(
            df=df,
            time_col=s.time_col,
            targets=list(s.targets),
            config=cfg,
            dataset_sha=s.dataset_sha,
            dataset_info=info,
            artifact_dir_by_target=artifact_dirs,
            on_progress=lambda t, p, m: self._progress_events.append((t, p, m)),
        )
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status.setText("Running in worker process pool…")
        self.main.log_msg(f"Analysis started in worker pool for {s.targets} (run #{rid})")
        self._poll_timer.start()

    def cancel(self):
        if self._job_handle is not None and not self._job_handle.done_event.is_set():
            self._job_handle.cancel()
            self.status.setText("Cancelling worker job…")

    def _poll_pool(self) -> None:
        while self._progress_events:
            target, pct, msg = self._progress_events.popleft()
            self._progress(target, pct, msg)

        handle = self._job_handle
        if handle is None or not handle.done_event.is_set():
            return

        self._poll_timer.stop()
        rid = self._active_run_id
        self._job_handle = None
        self._active_run_id = None

        if handle.status == "cancelled":
            from ..core.pipeline import RunResult, collect_versions

            cancelled_run = RunResult(
                seed=0,
                config=self.state.effective_config(),
                versions=collect_versions(),
                auto=[],
                targets={},
                created="",
                dataset={},
                cancelled=True,
            )
            if rid is not None:
                store.save_run(rid, cancelled_run, {})
            self._done(cancelled_run, rid or 0)
            return

        if handle.status == "failed" or handle.result_value is None:
            self._failed(handle.error_message or "Worker analysis failed.")
            return

        run, bundles = handle.result_value
        if rid is not None:
            store.save_run(rid, run, bundles)
        self._done(run, rid or 0)

    def _progress(self, target, pct, msg):
        if target in self.bars:
            self.bars[target][0].setValue(int(pct))
            self.bars[target][1].setText(msg)
        self.main.statusBar().showMessage(f"{target}: {msg}")

    def _failed(self, msg):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status.setText("Failed.")
        if _is_interactive():
            QMessageBox.critical(self, "Analysis failed", msg)

    def _done(self, run, rid):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status.setText("Cancelled." if run.cancelled else f"Completed (run #{rid}).")
        self.state.run, self.state.run_id, self.state.locked = run, rid, {}
        rows = []
        for t, tr in run.targets.items():
            best = tr.shortlist[0] if tr.shortlist else None
            rows.append(
                {
                    "target": t,
                    "status": tr.status,
                    "granularity": tr.granularity,
                    "n_obs": tr.n_obs,
                    "quality": tr.quality.get("score"),
                    "best_model": best["name"] if best else "",
                    "cvrmse_%": best["metrics"]["cvrmse"] if best else None,
                    "r2": best["metrics"]["r2"] if best else None,
                    "shortlisted": len(tr.shortlist),
                    "notice": tr.notice or tr.error,
                }
            )
        fill_table(self.summary, pd.DataFrame(rows))
        self.state.run_changed.emit()
        self.main.log_msg(
            f"Analysis finished (run #{rid}, cancelled={run.cancelled}). Open 'Baseline Selection' to choose a baseline per target."
        )
        if not run.cancelled:
            if _is_interactive():
                QMessageBox.information(
                    self,
                    "Analysis finished",
                    "Analysis complete. Review the results and choose one baseline per target on the 'Baseline Selection' tab.",
                )
            self.main.show_page("selection")
