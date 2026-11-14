"""Setup (data + column metadata schema + targets + temporal configuration) and Analysis (process-pool run) pages."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
import hashlib
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import Qt, QTimer

from ..config.runtime_env import is_interactive_ui
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import ingest, store
from ..core.auto import AutoLog
from ..core.cache import CachedComplianceRunner
from ..core.integrity import IntegrityViolationError
from ..core.pool import PoolJobHandle, get_pool_manager
from ..core.provenance import ProvenancePhase
from ..core.schema import (
    AUTO,
    Availability,
    ColumnRole,
    ColumnSpec,
    DatasetSchema,
    Inclusion,
    OutlierActionPolicy,
    QuantityType,
    SchemaIntegrityError,
    SchemaValidationError,
    suggest_dataset_schema,
)
from ..core.tcp import (
    ALPHA_PRESETS,
    CONFIDENCE_PRESETS,
    DecisionRule,
    FamilyDefinition,
    MultipleTestingCorrection,
    PreregistrationLockedError,
    TEST_CATALOG_SPECS,
    TCPValidationError,
    TestConfigPanel,
    compute_impact_preview,
)
from ..core.temporal import TemporalError, prepare_temporal
from ..core.vsg import VALID_PRESETS, VariableSelectionGate, VSGValidationError
from ..io.paths import PathGuardError, resolve_picked_path
from .widgets import PasteTable, fill_table, make_form_grid, make_table

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
AGG = ["AUTO", "mean", "sum", "median", "min", "max", "first", "last", "time_weighted"]
DST = ["AUTO", "wall_clock", "utc", "drop_ambiguous"]
OUTLIER_POLICY_OPTS = [
    OutlierActionPolicy.FLAG_ONLY.value,
    OutlierActionPolicy.ASK_USER.value,
    OutlierActionPolicy.AUTO_APPLY.value,
    AUTO,
]
ROLE_OPTS = [r.value for r in ColumnRole]
QTYPE_OPTS = ["(none)"] + [q.value for q in QuantityType]
INCLUSION_OPTS = [i.value for i in Inclusion]
AVAILABILITY_OPTS = [a.value for a in Availability]


def _is_interactive() -> bool:
    return is_interactive_ui()


def format_limitations_badges(provenance_payload: dict[str, Any] | None) -> list[str]:
    """UI-only Limitations formatter mapping ``phase == 'post_test'`` to ``post_hoc`` disclosure badges (VSG-6).

    Presentation labels stay strictly inside ``energybaseline.gui`` and never enter serialized
    provenance or stored database records.
    """
    if not provenance_payload:
        return []
    badges: list[str] = []
    for rec in provenance_payload.get("records", []):
        if str(rec.get("phase", "")) == ProvenancePhase.POST_TEST.value:
            ent = f"{rec.get('entity_type', 'item')}:{rec.get('entity_id', '')}"
            rsn = str(rec.get("reason", "") or "modified after testing")
            badges.append(f"Limitations [post_hoc]: {ent} ({rsn})")
    return badges


class ImpactPreviewPanel(QGroupBox):
    """Live Impact Preview showing variables, tests, family sizes, and Fast vs Full runtime estimates (VSG-3)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Impact Preview (Variables · Hypothesis Families · Fast vs Full)", parent)
        self.last_preview: dict[str, Any] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        self.lbl_variables = QLabel("Variables: load a dataset to preview screening counts.")
        self.lbl_variables.setWordWrap(True)
        self.lbl_tests = QLabel("Inferential tests: —")
        self.lbl_tests.setWordWrap(True)
        self.lbl_families = QLabel("Hypothesis families: —")
        self.lbl_families.setWordWrap(True)
        self.lbl_estimates = QLabel("Fast vs Full runtime estimate: —")
        self.lbl_estimates.setWordWrap(True)
        for w in (self.lbl_variables, self.lbl_tests, self.lbl_families, self.lbl_estimates):
            lay.addWidget(w)

    def refresh_from_models(
        self,
        vsg: VariableSelectionGate | None,
        tcp: TestConfigPanel | None = None,
        *,
        n_rows: int | None = None,
    ) -> dict[str, Any]:
        if vsg is None:
            self.last_preview = {}
            self.lbl_variables.setText("Variables: load a dataset to preview screening counts.")
            self.lbl_tests.setText("Inferential tests: —")
            self.lbl_families.setText("Hypothesis families: —")
            self.lbl_estimates.setText("Fast vs Full runtime estimate: —")
            return {}

        prev = compute_impact_preview(vsg, tcp, n_rows=n_rows)
        self.last_preview = prev
        parts = [
            f"{t}: {counts['included']} included / {counts['excluded']} excluded / {counts['collinear_grouped']} grouped"
            for t, counts in prev["per_target_variables"].items()
        ]
        self.lbl_variables.setText("Variables — " + "; ".join(parts))
        self.lbl_tests.setText(
            f"Tests — total {prev['total_tests']} "
            f"(pre-model {prev['pre_model_tests']}, post-model {prev['post_model_tests']}, "
            f"causal-group {prev['causal_group_tests']})"
        )
        fs = prev["family_sizes"]
        self.lbl_families.setText(
            f"Families ({prev['family_definition']}) — {prev['family_count']} families; "
            f"size min={fs['min']}, median={fs['median']:.1f}, max={fs['max']}"
        )
        ff = prev["fast_vs_full"]
        self.lbl_estimates.setText(
            f"Estimate (n={prev['n_rows']} rows) — Fast: {ff['fast_estimated_seconds']:.2f} s "
            f"vs Full: {ff['full_estimated_seconds']:.2f} s ({ff['speedup_ratio']:.1f}x faster)"
        )
        return prev


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
        self._syncing_schema = False
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, fn in (
            ("Open CSV / TSV / Excel / Parquet / Access…", lambda: self.open_file()),
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

        # Left column: tabbed Preview/Report + Column Metadata Schema Editor
        self.left_tabs = QTabWidget()
        preview_tab = QWidget()
        ll = QVBoxLayout(preview_tab)
        ll.addWidget(QLabel("<b>Data preview</b> (paged view, raw as imported)"))
        self.preview = make_table(False, accessible_name="Imported dataset preview table")
        ll.addWidget(self.preview, 3)
        ll.addWidget(QLabel("<b>Data dictionary / import validation</b>"))
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        ll.addWidget(self.report, 2)
        self.left_tabs.addTab(preview_tab, "Data Preview && Dictionary")

        schema_tab = QWidget()
        sl = QVBoxLayout(schema_tab)
        sbar = QHBoxLayout()
        self.btn_save_schema = QPushButton("Save Schema JSON…")
        self.btn_save_schema.clicked.connect(lambda: self.save_schema_file())
        self.btn_load_schema = QPushButton("Load Schema JSON…")
        self.btn_load_schema.clicked.connect(lambda: self.load_schema_file())
        self.btn_persist_schema = QPushButton("Validate && Persist Schema")
        self.btn_persist_schema.clicked.connect(self._on_persist_schema_clicked)
        sbar.addWidget(self.btn_save_schema)
        sbar.addWidget(self.btn_load_schema)
        sbar.addWidget(self.btn_persist_schema)
        sbar.addStretch(1)
        self.schema_status = QLabel("No schema loaded.")
        sbar.addWidget(self.schema_status)
        sl.addLayout(sbar)
        self.schema_tbl = make_form_grid(False, accessible_name="Column schema metadata editor")
        self.schema_tbl.setColumnCount(7)
        self.schema_tbl.setHorizontalHeaderLabels(
            ["Column", "Role", "Quantity Type", "Inclusion", "Unit", "Availability", "Source"]
        )
        self.schema_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.schema_tbl.itemChanged.connect(self._on_schema_item_changed)
        sl.addWidget(self.schema_tbl, 1)
        self.left_tabs.addTab(schema_tab, "Column Metadata (Schema)")

        # Tab 3: Variable Selection Gate (VSG-1..6) wrapped in QScrollArea for High-DPI safety
        vsg_scroll = QScrollArea()
        vsg_scroll.setWidgetResizable(True)
        vsg_tab = QWidget()
        vl = QVBoxLayout(vsg_tab)
        vbar = QHBoxLayout()
        vbar.addWidget(QLabel("Target:"))
        self.vsg_target_combo = QComboBox()
        self.vsg_target_combo.currentTextChanged.connect(self._render_vsg_tab)
        vbar.addWidget(self.vsg_target_combo)
        self.vsg_preset_combo = self._combo(sorted(VALID_PRESETS))
        self.vsg_preset_combo.setCurrentText("auto_recommended")
        vbar.addWidget(self.vsg_preset_combo)
        self.btn_apply_preset = QPushButton("Apply Preset")
        self.btn_apply_preset.clicked.connect(self._on_apply_vsg_preset)
        vbar.addWidget(self.btn_apply_preset)
        self.btn_copy_vsg = QPushButton("Copy to Sibling Targets")
        self.btn_copy_vsg.clicked.connect(self._on_copy_vsg_across_targets)
        vbar.addWidget(self.btn_copy_vsg)
        self.btn_accept_vsg = QPushButton("Accept VSG Gate")
        self.btn_accept_vsg.clicked.connect(self.accept_vsg_gate)
        vbar.addWidget(self.btn_accept_vsg)
        vl.addLayout(vbar)
        self.vsg_status = QLabel("VSG status: not initialized (load a dataset).")
        self.vsg_status.setWordWrap(True)
        vl.addWidget(self.vsg_status)
        self.impact_preview = ImpactPreviewPanel(vsg_tab)
        vl.addWidget(self.impact_preview)
        self.vsg_tbl = make_table(False)
        vl.addWidget(self.vsg_tbl, 2)
        vl.addWidget(QLabel("<b>Non-disableable Integrity Conflicts</b>"))
        self.integrity_tbl = make_table(False)
        vl.addWidget(self.integrity_tbl, 1)
        vsg_scroll.setWidget(vsg_tab)
        self.left_tabs.addTab(vsg_scroll, "Variable Selection Gate (VSG)")

        # Tab 4: Test Control Panel (TCP-1..5) + Zero-Recompute What-If + UI Limitations
        tcp_scroll = QScrollArea()
        tcp_scroll.setWidgetResizable(True)
        tcp_tab = QWidget()
        tl = QVBoxLayout(tcp_tab)
        tf = QFormLayout()
        self.tcp_alpha_combo = self._combo([str(a) for a in ALPHA_PRESETS], True)
        self.tcp_alpha_combo.setCurrentText("0.05")
        self.tcp_alpha_combo.currentTextChanged.connect(self._on_tcp_controls_edited)
        self.tcp_alpha_combo.setToolTip(
            "Significance level α: probability threshold below which a null hypothesis "
            "is rejected. Lower α (e.g. 0.01) reduces false positives but lowers power; "
            "higher α (e.g. 0.10) increases power but raises false-positive risk. "
            "Used by T1–T14 pre/post-model tests to compute adjusted p-values."
        )
        tf.addRow("Significance alpha (0.001–0.20)", self.tcp_alpha_combo)
        self.lbl_alpha_warning = QLabel("")
        self.lbl_alpha_warning.setStyleSheet("color: #B45309; font-weight: 600;")
        self.lbl_alpha_warning.setVisible(False)
        tf.addRow("", self.lbl_alpha_warning)
        self.tcp_conf_combo = self._combo(list(CONFIDENCE_PRESETS), True)
        self.tcp_conf_combo.setCurrentText("95")
        self.tcp_conf_combo.currentTextChanged.connect(self._on_tcp_controls_edited)
        self.tcp_conf_combo.setToolTip(
            "Confidence level (1 − α): coverage probability for confidence/prediction intervals. "
            "95 % means the interval contains the true value in 95 of 100 repeated samples. "
            "Used for uncertainty bands around the baseline and for MCS/test decisions."
        )
        tf.addRow("Confidence level (%)", self.tcp_conf_combo)
        self.tcp_corr_combo = self._combo([c.value for c in MultipleTestingCorrection])
        self.tcp_corr_combo.currentTextChanged.connect(self._on_tcp_controls_edited)
        tf.addRow("Multiple-testing correction", self.tcp_corr_combo)
        self.tcp_fam_combo = self._combo([f.value for f in FamilyDefinition])
        self.tcp_fam_combo.currentTextChanged.connect(self._on_tcp_controls_edited)
        tf.addRow("Family definition", self.tcp_fam_combo)
        self.tcp_rule_combo = self._combo([d.value for d in DecisionRule])
        self.tcp_rule_combo.currentTextChanged.connect(self._on_tcp_controls_edited)
        tf.addRow("Decision rule", self.tcp_rule_combo)
        tl.addLayout(tf)

        tbar = QHBoxLayout()
        self.btn_accept_tcp = QPushButton("Accept && Lock Pre-Registration")
        self.btn_accept_tcp.clicked.connect(self.accept_and_lock_tcp)
        self.btn_what_if = QPushButton("What-If Re-evaluate (0 Engine Calls)")
        self.btn_what_if.clicked.connect(self.run_what_if_ui)
        self.btn_amend_tcp = QPushButton("Unlock for Post-Test Amendment")
        self.btn_amend_tcp.clicked.connect(lambda: self.unlock_tcp_for_amendment("Engineer requested amendment"))
        tbar.addWidget(self.btn_accept_tcp)
        tbar.addWidget(self.btn_what_if)
        tbar.addWidget(self.btn_amend_tcp)
        tl.addLayout(tbar)

        # Test catalog panel: user can see every T1–T14 test, its stage, gate role,
        # and whether it is disableable. Hard-gate tests are read-only.
        g_tc = QGroupBox("Statistical test catalog (T1–T14) — tests applied to data and baseline")
        f_tc = QFormLayout(g_tc)
        self.tc_checks: dict[str, QCheckBox] = {}
        for tid, spec in sorted(TEST_CATALOG_SPECS.items()):
            row = QHBoxLayout()
            cb = QCheckBox(f"{tid} · {spec.name}")
            cb.setChecked(True)
            is_hard = spec.default_gate_role == "hard_gate"
            if is_hard:
                cb.setEnabled(False)
                cb.setToolTip("Non-disableable (hard gate — always runs).")
            else:
                cb.setToolTip(
                    f"Stage: {spec.stage} · family: {spec.family} · gate: {spec.default_gate_role} · "
                    f"per-variable: {spec.per_variable}"
                )
            row.addWidget(cb)
            row.addStretch(1)
            lbl = QLabel(spec.description or "")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #667085; font-size: 11px;")
            row.addWidget(lbl, 1)
            f_tc.addRow(row)
            self.tc_checks[tid] = cb
        tl.addWidget(g_tc)

        self.tcp_status = QLabel("TCP status: awaiting VSG acceptance.")
        self.tcp_status.setWordWrap(True)
        tl.addWidget(self.tcp_status)
        self.lbl_limitations = QLabel("")
        self.lbl_limitations.setWordWrap(True)
        self.lbl_limitations.setStyleSheet("color: #991B1B; font-weight: 600;")
        self.lbl_limitations.setVisible(False)
        tl.addWidget(self.lbl_limitations)
        self.tcp_results_tbl = make_table(False)
        tl.addWidget(self.tcp_results_tbl, 1)
        tcp_scroll.setWidget(tcp_tab)
        self.left_tabs.addTab(tcp_scroll, "Test Control Panel (TCP)")

        split.addWidget(self.left_tabs)

        # Right column: roles + temporal + outlier policy
        right = QWidget()
        rl = QVBoxLayout(right)
        g1 = QGroupBox("Columns and targets")
        f1 = QFormLayout(g1)
        self.time_combo = QComboBox()
        self.time_combo.currentTextChanged.connect(self._on_legacy_time_changed)
        f1.addRow("Timestamp column", self.time_combo)
        self.units = QLineEdit()
        self.units.setPlaceholderText("optional unit normalisation, e.g. Elec=MWh, Temp=degF")
        self.units.editingFinished.connect(self._on_legacy_units_edited)
        f1.addRow("Declared units", self.units)
        self.target_list = QListWidget()
        self.target_list.itemChanged.connect(self._targets_changed)
        f1.addRow("TARGET column(s) - one baseline per target", self.target_list)
        rl.addWidget(g1, 2)

        g2 = QGroupBox("Temporal & outlier policy (AUTO detects and asks you to confirm)")
        f2 = QFormLayout(g2)
        self.sampling = self._combo(SAMPLING, True)
        self.agg = self._combo(AGG)
        self.tz = QLineEdit()
        self.tz.setPlaceholderText("AUTO (naive wall-clock) or IANA name, e.g. Europe/Berlin")
        self.dst = self._combo(DST)
        self.outlier_policy = self._combo(OUTLIER_POLICY_OPTS)
        self.b0, self.b1 = QLineEdit(), QLineEdit()
        self.r0, self.r1 = QLineEdit(), QLineEdit()
        for w in (self.b0, self.b1, self.r0, self.r1):
            w.setPlaceholderText("AUTO / YYYY-MM-DD")
        f2.addRow("Sampling period", self.sampling)
        f2.addRow("Aggregation (if data finer)", self.agg)
        f2.addRow("Timezone", self.tz)
        f2.addRow("DST handling", self.dst)
        f2.addRow("Outlier policy", self.outlier_policy)
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
        self.override_tbl = make_form_grid(False, accessible_name="Per-target sampling override grid")
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
                "Data files (*.csv *.tsv *.txt *.xlsx *.xls *.parquet *.pq *.mdb *.accdb);;All files (*)",
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

        self._syncing_schema = True
        try:
            self.time_combo.clear()
            self.time_combo.addItems(["(none)"] + list(df.columns))
            if s.time_col and self.time_combo.findText(s.time_col) >= 0:
                self.time_combo.setCurrentText(s.time_col)
            else:
                self.time_combo.setCurrentText("(none)")
            self.target_list.blockSignals(True)
            self.target_list.clear()
            for c in df.columns:
                if c != s.time_col and pd.api.types.is_numeric_dtype(df[c]):
                    it = QListWidgetItem(c)
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(Qt.CheckState.Unchecked)
                    self.target_list.addItem(it)
            # Auto-select a plausible target ONLY when the user has not already
            # chosen one (preserves explicit user choice across reloads).
            already_checked = any(
                self.target_list.item(i).checkState() == Qt.CheckState.Checked
                for i in range(self.target_list.count())
            )
            if not already_checked:
                for i in range(self.target_list.count()):
                    if any(
                        k in self.target_list.item(i).text().lower()
                        for k in ("kwh", "energy", "elec", "gas", "consum", "m3")
                    ):
                        self.target_list.item(i).setCheckState(Qt.CheckState.Checked)
                        break
            self.target_list.blockSignals(False)
            self._update_overrides_table()
        finally:
            self._syncing_schema = False

        s.targets = self.checked_targets()
        suggested = suggest_dataset_schema(
            df,
            time_col=s.time_col,
            targets=self.checked_targets() or None,
            dataset_name=name,
            dataset_sha256=sha,
        )
        self.populate_schema_table(suggested)
        summary = ingest.build_ingestion_summary(df, source_name=name, source_sha256=sha, time_col=s.time_col)
        s.dataset_schema = suggested
        s.schema_id = store.save_dataset_schema(suggested, dataset_id=s.dataset_id, ingestion_summary=summary)
        self.schema_status.setText(f"Schema #{s.schema_id} (SHA {suggested.compute_sha256()[:10]}…)")

        fill_table(self.preview, df)
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
        self.rebuild_compliance_models()
        s.data_changed.emit()
        self.main.log_msg(f"Dataset loaded: {name}")

    # ------------------------------------------------------------ VSG & TCP compliance UI
    def rebuild_compliance_models(self) -> None:
        """Initialize or refresh ``state.vsg``, ``state.tcp``, and ``ImpactPreviewPanel`` from active dataset."""
        s = self.state
        if s.df is None or not s.targets:
            s.vsg = None
            s.tcp = None
            s.compliance_runner = None
            self.impact_preview.refresh_from_models(None)
            return

        vsg = VariableSelectionGate(
            s.df,
            targets=s.targets,
            time_col=s.time_col,
            schema=s.dataset_schema,
            dataset_sha256=s.dataset_sha,
            config=s.config,
        )
        tcp = TestConfigPanel(vsg, config=s.config)
        s.vsg = vsg
        s.tcp = tcp
        s.compliance_runner = CachedComplianceRunner(seed=42)

        self.vsg_target_combo.blockSignals(True)
        self.vsg_target_combo.clear()
        self.vsg_target_combo.addItems(list(vsg.targets))
        self.vsg_target_combo.blockSignals(False)

        self._sync_tcp_widgets_from_model()
        self._render_vsg_tab()
        self._refresh_limitations_banner()
        if self.main is not None and hasattr(self.main, "auto_panel"):
            self.main.auto_panel.set_records(vsg.auto_log.as_dicts())

    def _sync_tcp_widgets_from_model(self) -> None:
        tcp = self.state.tcp
        if tcp is None:
            return
        for w in (
            self.tcp_alpha_combo,
            self.tcp_conf_combo,
            self.tcp_corr_combo,
            self.tcp_fam_combo,
            self.tcp_rule_combo,
        ):
            w.blockSignals(True)
        try:
            self.tcp_alpha_combo.setCurrentText(f"{tcp.alpha:g}")
            self.tcp_conf_combo.setCurrentText(
                tcp.confidence_preset if tcp.confidence_preset != "custom" else f"{tcp.confidence_level * 100:g}"
            )
            self.tcp_corr_combo.setCurrentText(tcp.correction)
            self.tcp_fam_combo.setCurrentText(tcp.family_definition)
            self.tcp_rule_combo.setCurrentText(tcp.decision_rule)
        finally:
            for w in (
                self.tcp_alpha_combo,
                self.tcp_conf_combo,
                self.tcp_corr_combo,
                self.tcp_fam_combo,
                self.tcp_rule_combo,
            ):
                w.blockSignals(False)
        self._update_alpha_warning_ui(tcp.alpha)

    def _update_alpha_warning_ui(self, alpha_val: float) -> None:
        if alpha_val > 0.10 + 1e-12:
            self.lbl_alpha_warning.setText(
                f"Warning: alpha={alpha_val:.3f} exceeds 0.10 (elevated false-positive risk)."
            )
            self.lbl_alpha_warning.setVisible(True)
        else:
            self.lbl_alpha_warning.setText("")
            self.lbl_alpha_warning.setVisible(False)

    def _refresh_limitations_banner(self) -> None:
        vsg = self.state.vsg
        badges = format_limitations_badges(vsg.provenance.to_dict() if vsg is not None else None)
        if badges:
            self.lbl_limitations.setText(" · ".join(badges))
            self.lbl_limitations.setVisible(True)
        else:
            self.lbl_limitations.setText("")
            self.lbl_limitations.setVisible(False)

    def _render_vsg_tab(self, *_args: Any) -> None:
        vsg = self.state.vsg
        tcp = self.state.tcp
        if vsg is None:
            return
        tgt = self.vsg_target_combo.currentText() or vsg.targets[0]
        if tgt not in vsg.profiles_by_target:
            tgt = vsg.targets[0]

        self.impact_preview.refresh_from_models(vsg, tcp)
        rows: list[dict[str, Any]] = []
        for col, prof in vsg.profiles_by_target[tgt].items():
            dec = vsg.decisions_by_target[tgt][col]
            rows.append(
                {
                    "variable": col,
                    "disposition": dec.resolved_disposition,
                    "mode": dec.mode,
                    "missing_%": prof.missing_pct,
                    "unique": prof.unique_count,
                    "pearson_r": prof.pearson_r,
                    "mutual_info": prof.mutual_info,
                    "vif": prof.vif,
                    "leakage": prof.leakage_suspicion,
                    "availability": prof.availability,
                    "histogram": prof.mini_histogram.get("sparkline", ""),
                    "reason": dec.reason,
                }
            )
        fill_table(self.vsg_tbl, pd.DataFrame(rows))
        c_rows = vsg.integrity_by_target[tgt].to_conflict_rows()
        fill_table(
            self.integrity_tbl,
            pd.DataFrame(
                c_rows,
                columns=["rule_id", "variable", "severity", "message", "remediation"],
            ),
        )
        acc_txt = "ACCEPTED" if vsg.is_accepted() else "PENDING ACCEPTANCE"
        self.vsg_status.setText(f"VSG [{acc_txt}] · SHA-256: {vsg.vsg_sha256[:16]}…")

    def _on_apply_vsg_preset(self) -> None:
        vsg = self.state.vsg
        if vsg is None:
            return
        tgt = self.vsg_target_combo.currentText() or vsg.targets[0]
        preset = self.vsg_preset_combo.currentText()
        phase = self.state.tcp.phase if self.state.tcp is not None else ProvenancePhase.PRE_TEST.value
        vsg.apply_preset(tgt, preset, phase=phase)
        self._render_vsg_tab()
        self._refresh_limitations_banner()

    def _on_copy_vsg_across_targets(self) -> None:
        vsg = self.state.vsg
        if vsg is None or len(vsg.targets) < 2:
            return
        src = self.vsg_target_combo.currentText() or vsg.targets[0]
        phase = self.state.tcp.phase if self.state.tcp is not None else ProvenancePhase.PRE_TEST.value
        for dst in vsg.targets:
            if dst != src:
                vsg.copy_target_decisions(src, dst, phase=phase)
        self._render_vsg_tab()
        self._refresh_limitations_banner()

    def accept_vsg_gate(self) -> bool:
        vsg = self.state.vsg
        if vsg is None:
            return False
        try:
            phase = self.state.tcp.phase if self.state.tcp is not None else ProvenancePhase.PRE_TEST.value
            vsg.accept(phase=phase)
            self.state.vsg_id = store.save_vsg_state(vsg.to_dict(), dataset_id=self.state.dataset_id)
            self._render_vsg_tab()
            self._refresh_limitations_banner()
            self.tcp_status.setText("VSG accepted. Ready to accept & lock TCP.")
            return True
        except (IntegrityViolationError, VSGValidationError) as exc:
            self.vsg_status.setText(f"VSG blocked: {exc}")
            if _is_interactive():
                QMessageBox.critical(self, "VSG Integrity Conflict", str(exc))
            return False

    def _on_tcp_controls_edited(self, *_args: Any) -> None:
        tcp = self.state.tcp
        if tcp is None or tcp.is_locked:
            return
        try:
            alpha_f = float(self.tcp_alpha_combo.currentText().strip())
            tcp.set_alpha(alpha_f)
            self._update_alpha_warning_ui(tcp.alpha)
            conf_txt = self.tcp_conf_combo.currentText().strip()
            if conf_txt in ("80", "90", "95", "99"):
                tcp.set_confidence(conf_txt)
            else:
                tcp.set_confidence("custom", float(conf_txt))
            tcp.set_correction(self.tcp_corr_combo.currentText())
            tcp.set_family_definition(self.tcp_fam_combo.currentText())
            tcp.set_decision_rule(self.tcp_rule_combo.currentText())
            self.impact_preview.refresh_from_models(self.state.vsg, tcp)
            self._refresh_limitations_banner()
        except (TCPValidationError, ValueError) as exc:
            self.tcp_status.setText(f"TCP validation warning: {exc}")

    def accept_and_lock_tcp(self) -> str:
        s = self.state
        if s.vsg is None or s.tcp is None:
            return ""
        if not s.vsg.is_accepted() and not self.accept_vsg_gate():
            return ""
        try:
            digest = s.tcp.lock_preregistration()
            s.tcp_id = store.save_tcp_config(
                s.tcp.to_dict(),
                dataset_id=s.dataset_id,
                vsg_id=s.vsg_id,
            )
            store.save_provenance_ledger(s.vsg.provenance.to_dict(), dataset_id=s.dataset_id)
            if s.df is not None and s.compliance_runner is not None:
                eval_rows = s.compliance_runner.execute_tests(s.df, s.vsg, s.tcp)
                fill_table(self.tcp_results_tbl, pd.DataFrame([r.to_dict() for r in eval_rows]))
            self.tcp_status.setText(f"TCP LOCKED · Pre-registration SHA-256: {digest[:16]}…")
            self._refresh_limitations_banner()
            return digest
        except (TCPValidationError, IntegrityViolationError) as exc:
            self.tcp_status.setText(f"TCP lock failed: {exc}")
            return ""

    def unlock_tcp_for_amendment(self, reason: str = "Post-test amendment") -> None:
        tcp = self.state.tcp
        if tcp is None:
            return
        tcp.unlock_for_amendment(reason=reason)
        self.tcp_status.setText("TCP unlocked for post-test amendment (Limitations disclosure active).")
        self._refresh_limitations_banner()

    def run_what_if_ui(self) -> dict[str, Any]:
        s = self.state
        if s.tcp is None or s.compliance_runner is None:
            return {}
        try:
            alpha_f = float(self.tcp_alpha_combo.currentText().strip())
            conf_txt = self.tcp_conf_combo.currentText().strip()
            c_preset = conf_txt if conf_txt in ("80", "90", "95", "99") else "custom"
            c_custom = None if c_preset != "custom" else float(conf_txt)
            probe = s.tcp.clone_for_what_if(
                alpha=alpha_f,
                confidence_preset=c_preset,
                confidence_custom=c_custom,
                correction=self.tcp_corr_combo.currentText(),
                family_definition=self.tcp_fam_combo.currentText(),
                decision_rule=self.tcp_rule_combo.currentText(),
            )
            self._update_alpha_warning_ui(probe.alpha)
            res = s.compliance_runner.what_if(probe)
            fill_table(self.tcp_results_tbl, pd.DataFrame(res["evaluated_results"]))
            self.tcp_status.setText(
                f"What-If evaluated: {res['flip_count']} flip(s), "
                f"engine_calls_delta={res['engine_calls_delta']}"
            )
            return res
        except (PreregistrationLockedError, TCPValidationError, RuntimeError, ValueError) as exc:
            self.tcp_status.setText(f"What-If error: {exc}")
            return {}

    # ------------------------------------------------------------ schema editor
    def populate_schema_table(self, schema: DatasetSchema) -> None:
        """Populate ``self.schema_tbl`` from ``schema`` without feedback loops."""
        self._syncing_schema = True
        try:
            self.schema_tbl.setRowCount(len(schema.columns))
            for r, spec in enumerate(schema.columns):
                it_name = QTableWidgetItem(spec.name)
                it_name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.schema_tbl.setItem(r, 0, it_name)

                cb_role = self._combo(ROLE_OPTS)
                cb_role.setCurrentText(spec.role.value)
                cb_role.currentTextChanged.connect(lambda _v, row=r: self._mark_row_explicit(row, sync_legacy=True))
                self.schema_tbl.setCellWidget(r, 1, cb_role)

                cb_qt = self._combo(QTYPE_OPTS)
                cb_qt.setCurrentText(spec.quantity_type.value if spec.quantity_type is not None else "(none)")
                cb_qt.currentTextChanged.connect(lambda _v, row=r: self._mark_row_explicit(row, sync_legacy=False))
                self.schema_tbl.setCellWidget(r, 2, cb_qt)

                cb_inc = self._combo(INCLUSION_OPTS)
                cb_inc.setCurrentText(spec.inclusion.value)
                cb_inc.currentTextChanged.connect(lambda _v, row=r: self._mark_row_explicit(row, sync_legacy=False))
                self.schema_tbl.setCellWidget(r, 3, cb_inc)

                it_unit = QTableWidgetItem(spec.unit)
                it_unit.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
                self.schema_tbl.setItem(r, 4, it_unit)

                cb_av = self._combo(AVAILABILITY_OPTS, True)
                cb_av.setCurrentText(spec.availability)
                cb_av.currentTextChanged.connect(lambda _v, row=r: self._mark_row_explicit(row, sync_legacy=False))
                self.schema_tbl.setCellWidget(r, 5, cb_av)

                src_txt = "heuristic" if spec.suggested_by_heuristic else "explicit"
                it_src = QTableWidgetItem(src_txt)
                it_src.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.schema_tbl.setItem(r, 6, it_src)

            if schema.timezone:
                self.tz.setText(schema.timezone)
            if schema.outlier_policy.value in OUTLIER_POLICY_OPTS:
                self.outlier_policy.setCurrentText(schema.outlier_policy.value)
        finally:
            self._syncing_schema = False

    def _mark_row_explicit(self, row: int, *, sync_legacy: bool) -> None:
        if self._syncing_schema:
            return
        self._syncing_schema = True
        try:
            cb_role = self.schema_tbl.cellWidget(row, 1)
            cb_qt = self.schema_tbl.cellWidget(row, 2)
            if isinstance(cb_role, QComboBox) and isinstance(cb_qt, QComboBox):
                role_txt = cb_role.currentText()
                if role_txt in (ColumnRole.TIME.value, ColumnRole.IGNORE.value, ColumnRole.GROUP.value):
                    cb_qt.setCurrentText("(none)")
                elif cb_qt.currentText() == "(none)":
                    cb_qt.setCurrentText(
                        QuantityType.FLOW.value if role_txt == ColumnRole.TARGET.value else QuantityType.QUALITY.value
                    )
            src_it = self.schema_tbl.item(row, 6)
            if src_it is not None:
                src_it.setText("explicit")
            if sync_legacy:
                self._sync_schema_to_legacy_controls_unlocked()
        finally:
            self._syncing_schema = False

    def _on_schema_item_changed(self, item: QTableWidgetItem) -> None:
        if self._syncing_schema:
            return
        if item.column() == 4:
            self._mark_row_explicit(item.row(), sync_legacy=True)

    def _on_legacy_time_changed(self, new_time_col: str) -> None:
        if self._syncing_schema or not new_time_col or self.schema_tbl.rowCount() == 0:
            return
        self._syncing_schema = True
        try:
            for r in range(self.schema_tbl.rowCount()):
                nm = self.schema_tbl.item(r, 0).text() if self.schema_tbl.item(r, 0) else ""
                cb_role = self.schema_tbl.cellWidget(r, 1)
                cb_qt = self.schema_tbl.cellWidget(r, 2)
                if not isinstance(cb_role, QComboBox) or not isinstance(cb_qt, QComboBox):
                    continue
                if nm == new_time_col:
                    cb_role.setCurrentText(ColumnRole.TIME.value)
                    cb_qt.setCurrentText("(none)")
                elif cb_role.currentText() == ColumnRole.TIME.value:
                    cb_role.setCurrentText(ColumnRole.PREDICTOR.value)
                    cb_qt.setCurrentText(QuantityType.QUALITY.value)
        finally:
            self._syncing_schema = False

    def _on_legacy_units_edited(self) -> None:
        if self._syncing_schema or self.schema_tbl.rowCount() == 0:
            return
        units = self._parse_units_line()
        self._syncing_schema = True
        try:
            for r in range(self.schema_tbl.rowCount()):
                nm = self.schema_tbl.item(r, 0).text() if self.schema_tbl.item(r, 0) else ""
                if nm in units and self.schema_tbl.item(r, 4) is not None:
                    self.schema_tbl.item(r, 4).setText(units[nm])
        finally:
            self._syncing_schema = False

    def _sync_schema_to_legacy_controls_unlocked(self) -> None:
        time_col: str | None = None
        targets: set[str] = set()
        unit_pairs: list[str] = []
        for r in range(self.schema_tbl.rowCount()):
            nm = self.schema_tbl.item(r, 0).text() if self.schema_tbl.item(r, 0) else ""
            cb_role = self.schema_tbl.cellWidget(r, 1)
            u_item = self.schema_tbl.item(r, 4)
            role_txt = cb_role.currentText() if isinstance(cb_role, QComboBox) else ""
            if role_txt == ColumnRole.TIME.value and time_col is None:
                time_col = nm
            elif role_txt == ColumnRole.TARGET.value:
                targets.add(nm)
            u_txt = u_item.text().strip() if u_item else ""
            if u_txt:
                unit_pairs.append(f"{nm}={u_txt}")

        if time_col and self.time_combo.findText(time_col) >= 0:
            self.time_combo.setCurrentText(time_col)
        self.target_list.blockSignals(True)
        for i in range(self.target_list.count()):
            it = self.target_list.item(i)
            it.setCheckState(Qt.CheckState.Checked if it.text() in targets else Qt.CheckState.Unchecked)
        self.target_list.blockSignals(False)
        self._update_overrides_table()
        self.units.setText(", ".join(unit_pairs))

    def set_column_metadata(
        self,
        col_name: str,
        *,
        role: str | ColumnRole | None = None,
        quantity_type: str | QuantityType | None = None,
        inclusion: str | Inclusion | None = None,
        unit: str | None = None,
        availability: str | Availability | None = None,
    ) -> None:
        """Programmatically edit column metadata on ``self.schema_tbl`` and sync legacy controls."""
        target_row = -1
        for r in range(self.schema_tbl.rowCount()):
            it = self.schema_tbl.item(r, 0)
            if it is not None and it.text() == col_name:
                target_row = r
                break
        if target_row < 0:
            raise KeyError(f"Column '{col_name}' not found in schema_tbl.")

        self._syncing_schema = True
        try:
            if role is not None:
                r_val = role.value if isinstance(role, ColumnRole) else str(role)
                cb_role = self.schema_tbl.cellWidget(target_row, 1)
                if isinstance(cb_role, QComboBox):
                    cb_role.setCurrentText(r_val)
                    cb_qt = self.schema_tbl.cellWidget(target_row, 2)
                    if isinstance(cb_qt, QComboBox) and quantity_type is None:
                        if r_val in (ColumnRole.TIME.value, ColumnRole.IGNORE.value, ColumnRole.GROUP.value):
                            cb_qt.setCurrentText("(none)")
                        elif cb_qt.currentText() == "(none)":
                            cb_qt.setCurrentText(
                                QuantityType.FLOW.value
                                if r_val == ColumnRole.TARGET.value
                                else QuantityType.QUALITY.value
                            )
            if quantity_type is not None:
                qt_val = quantity_type.value if isinstance(quantity_type, QuantityType) else str(quantity_type)
                cb_qt = self.schema_tbl.cellWidget(target_row, 2)
                if isinstance(cb_qt, QComboBox):
                    cb_qt.setCurrentText("(none)" if qt_val in ("", "None", "none", "(none)") else qt_val)
            if inclusion is not None:
                inc_val = inclusion.value if isinstance(inclusion, Inclusion) else str(inclusion)
                cb_inc = self.schema_tbl.cellWidget(target_row, 3)
                if isinstance(cb_inc, QComboBox):
                    cb_inc.setCurrentText(inc_val)
            if unit is not None:
                u_it = self.schema_tbl.item(target_row, 4)
                if u_it is not None:
                    u_it.setText(unit)
            if availability is not None:
                av_val = availability.value if isinstance(availability, Availability) else str(availability)
                cb_av = self.schema_tbl.cellWidget(target_row, 5)
                if isinstance(cb_av, QComboBox):
                    cb_av.setCurrentText(av_val)
            src_it = self.schema_tbl.item(target_row, 6)
            if src_it is not None:
                src_it.setText("explicit")
            self._sync_schema_to_legacy_controls_unlocked()
        finally:
            self._syncing_schema = False

    def build_schema_from_ui(self) -> DatasetSchema:
        """Construct and validate a ``DatasetSchema`` from ``self.schema_tbl`` (DAT-7)."""
        specs: list[ColumnSpec] = []
        line_units = self._parse_units_line()
        for r in range(self.schema_tbl.rowCount()):
            nm = self.schema_tbl.item(r, 0).text().strip() if self.schema_tbl.item(r, 0) else ""
            cb_role = self.schema_tbl.cellWidget(r, 1)
            cb_qt = self.schema_tbl.cellWidget(r, 2)
            cb_inc = self.schema_tbl.cellWidget(r, 3)
            u_item = self.schema_tbl.item(r, 4)
            cb_av = self.schema_tbl.cellWidget(r, 5)
            src_item = self.schema_tbl.item(r, 6)

            role_txt = cb_role.currentText() if isinstance(cb_role, QComboBox) else ColumnRole.PREDICTOR.value
            qt_raw = cb_qt.currentText() if isinstance(cb_qt, QComboBox) else "(none)"
            qt_val: str | None = None if qt_raw in ("(none)", "", "none") else qt_raw
            inc_txt = cb_inc.currentText() if isinstance(cb_inc, QComboBox) else Inclusion.OBSERVED_ONLY.value
            unit_txt = line_units.get(nm, u_item.text().strip() if u_item else "")
            av_txt = cb_av.currentText().strip() if isinstance(cb_av, QComboBox) else Availability.ALWAYS.value
            is_heur = (src_item.text().strip() == "heuristic") if src_item else False

            specs.append(
                ColumnSpec(
                    name=nm,
                    role=role_txt,
                    quantity_type=qt_val,
                    inclusion=inc_txt,
                    unit=unit_txt,
                    availability=av_txt or Availability.ALWAYS.value,
                    suggested_by_heuristic=is_heur,
                )
            )

        pol_txt = self.outlier_policy.currentText()
        resolved_pol = OutlierActionPolicy.FLAG_ONLY.value if pol_txt == AUTO else pol_txt
        tz_txt = self.tz.text().strip()
        if tz_txt.upper() == AUTO:
            tz_txt = ""
        existing_nres = (
            list(self.state.dataset_schema.non_routine_events) if self.state.dataset_schema is not None else []
        )
        schema = DatasetSchema(
            columns=specs,
            dataset_name=self.state.dataset_name,
            dataset_sha256=self.state.dataset_sha,
            timezone=tz_txt,
            outlier_policy=resolved_pol,
            non_routine_events=existing_nres,
        )
        if self.state.df is not None:
            schema.validate(df_columns=list(self.state.df.columns))
        else:
            schema.validate()
        return schema

    def persist_schema_to_db(self) -> int:
        schema = self.build_schema_from_ui()
        summary: dict[str, Any] | None = None
        if self.state.df is not None:
            summary = ingest.build_ingestion_summary(
                self.state.df,
                source_name=self.state.dataset_name,
                source_sha256=self.state.dataset_sha,
                time_col=schema.time_column,
            )
        sid = store.save_dataset_schema(schema, dataset_id=self.state.dataset_id, ingestion_summary=summary)
        self.state.dataset_schema = schema
        self.state.schema_id = sid
        self.schema_status.setText(f"Schema #{sid} (SHA {schema.compute_sha256()[:10]}…)")
        return sid

    def _on_persist_schema_clicked(self) -> None:
        try:
            sid = self.persist_schema_to_db()
            if _is_interactive():
                QMessageBox.information(self, "Schema persisted", f"Saved DatasetSchema #{sid} to project database.")
        except (SchemaValidationError, SchemaIntegrityError) as exc:
            self.schema_status.setText(f"Invalid: {exc}")
            if _is_interactive():
                QMessageBox.warning(self, "Invalid schema", str(exc))

    def save_schema_file(
        self,
        target_path: str | Path | None = None,
        *,
        allowed_roots: Sequence[str | Path] | None = None,
    ) -> Path | None:
        raw = target_path
        if raw is None:
            picked, _ = QFileDialog.getSaveFileName(
                self, "Save DatasetSchema JSON", "dataset_schema.json", "JSON (*.json)"
            )
            if not picked:
                return None
            raw = picked
        try:
            dest = resolve_picked_path(raw, allowed_roots=allowed_roots, must_exist=False)
            schema = self.build_schema_from_ui()
            schema.save_json(dest)
            self.state.dataset_schema = schema
            return dest
        except (PathGuardError, SchemaValidationError, OSError) as exc:
            self.schema_status.setText(f"Save failed: {exc}")
            if _is_interactive():
                QMessageBox.critical(self, "Save schema failed", str(exc))
            return None

    def load_schema_file(
        self,
        picked_path: str | Path | None = None,
        *,
        allowed_roots: Sequence[str | Path] | None = None,
    ) -> bool:
        raw = picked_path
        if raw is None:
            picked, _ = QFileDialog.getOpenFileName(self, "Load DatasetSchema JSON", "", "JSON (*.json)")
            if not picked:
                return False
            raw = picked
        try:
            src = resolve_picked_path(raw, allowed_roots=allowed_roots, must_exist=True)
            schema = DatasetSchema.load_json(src, verify_sha256=True)
            if self.state.df is not None:
                schema.validate(df_columns=list(self.state.df.columns))
            self.populate_schema_table(schema)
            self._syncing_schema = True
            try:
                self._sync_schema_to_legacy_controls_unlocked()
            finally:
                self._syncing_schema = False
            self.state.dataset_schema = schema
            sid = store.save_dataset_schema(schema, dataset_id=self.state.dataset_id)
            self.state.schema_id = sid
            self.schema_status.setText(f"Schema #{sid} (SHA {schema.compute_sha256()[:10]}…)")
            return True
        except (PathGuardError, SchemaIntegrityError, SchemaValidationError, OSError) as exc:
            self.schema_status.setText(f"Load failed: {exc}")
            if _is_interactive():
                QMessageBox.critical(self, "Load schema failed", str(exc))
            return False

    # ------------------------------------------------------------ targets & settings
    def _update_overrides_table(self) -> None:
        t = self.checked_targets()
        self.override_tbl.setRowCount(len(t))
        for i, name in enumerate(t):
            it = QTableWidgetItem(name)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.override_tbl.setItem(i, 0, it)
            cb = self._combo(SAMPLING, True)
            self.override_tbl.setCellWidget(i, 1, cb)

    def _targets_changed(self, *_):
        self._update_overrides_table()
        if self._syncing_schema or self.schema_tbl.rowCount() == 0:
            return
        checked = set(self.checked_targets())
        self._syncing_schema = True
        try:
            for r in range(self.schema_tbl.rowCount()):
                nm = self.schema_tbl.item(r, 0).text() if self.schema_tbl.item(r, 0) else ""
                cb_role = self.schema_tbl.cellWidget(r, 1)
                cb_qt = self.schema_tbl.cellWidget(r, 2)
                if not isinstance(cb_role, QComboBox) or not isinstance(cb_qt, QComboBox):
                    continue
                cur_role = cb_role.currentText()
                if nm in checked and cur_role != ColumnRole.TARGET.value:
                    cb_role.setCurrentText(ColumnRole.TARGET.value)
                    if cb_qt.currentText() == "(none)":
                        cb_qt.setCurrentText(QuantityType.FLOW.value)
                elif nm not in checked and cur_role == ColumnRole.TARGET.value:
                    cb_role.setCurrentText(ColumnRole.PREDICTOR.value)
                    if cb_qt.currentText() == "(none)":
                        cb_qt.setCurrentText(QuantityType.QUALITY.value)
        finally:
            self._syncing_schema = False

    def checked_targets(self) -> list[str]:
        return [
            self.target_list.item(i).text()
            for i in range(self.target_list.count())
            if self.target_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _parse_units_line(self) -> dict[str, str]:
        units: dict[str, str] = {}
        for part in self.units.text().split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() and v.strip():
                    units[k.strip()] = v.strip()
        return units

    def collect(self) -> None:
        """Push the Setup choices into state (AUTO entries do NOT override Advanced settings)."""
        s = self.state
        tc = self.time_combo.currentText()
        s.time_col = None if tc in ("", "(none)") else tc
        s.targets = self.checked_targets()
        units = self._parse_units_line()
        cfg: dict[str, Any] = {}

        def put(key, val):
            val = (val or "").strip()
            if val and val.upper() != "AUTO":
                cfg[key] = val

        put("sampling_period", self.sampling.currentText())
        put("aggregation", self.agg.currentText())
        put("timezone", self.tz.text())
        put("dst_handling", self.dst.currentText())
        put("outlier_action_policy", self.outlier_policy.currentText())
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

        if self.schema_tbl.rowCount() > 0 and s.targets:
            schema = self.build_schema_from_ui()
            s.dataset_schema = schema
            s.time_col = schema.time_column
            s.targets = schema.target_columns
            merged_units = dict(schema.units_map())
            merged_units.update(units)
            units = merged_units
            cfg["dataset_schema"] = schema.to_dict()

        s.units = units
        s.setup_config = cfg

    def validate_temporal(self):
        s = self.state
        if s.df is None:
            if _is_interactive():
                QMessageBox.information(self, "Temporal setup", "Load a dataset first.")
            return
        try:
            self.collect()
        except SchemaValidationError as exc:
            if _is_interactive():
                QMessageBox.warning(self, "Schema validation", str(exc))
            return
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
                out, rep, g = prepare_temporal(
                    self.apply_units(s.df),
                    s.time_col,
                    auto,
                    schema=s.dataset_schema,
                )
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
        try:
            self.main.setup_page.collect()
        except SchemaValidationError as exc:
            if _is_interactive():
                QMessageBox.warning(self, "Run analysis", f"Schema validation failed: {exc}")
            return
        if s.df is None:
            if _is_interactive():
                QMessageBox.information(self, "Run analysis", "Load a dataset on the Setup tab first.")
            return
        if not s.targets:
            if _is_interactive():
                QMessageBox.information(self, "Run analysis", "Tick at least one TARGET column on the Setup tab.")
            return
        # time_col is optional: datasets without a timestamp column run as
        # non-temporal (row-order) baselines.
        if s.df is not None and s.time_col is None and _is_interactive():
            QMessageBox.information(
                self,
                "Run analysis",
                "No timestamp column selected — analysis will run in non-temporal (row-order) mode.",
            )
        if s.vsg is not None and not s.vsg.is_accepted():
            self.status.setText("Blocked: accept Variable Selection Gate (VSG) first.")
            if _is_interactive():
                QMessageBox.warning(
                    self,
                    "Compliance gate required",
                    "Accept the Variable Selection Gate (VSG) and Test Control Panel (TCP) on the Setup tab before running analysis.",
                )
            return
        if s.tcp is not None and not s.tcp.is_accepted():
            self.status.setText("Blocked: accept Test Control Panel (TCP) first.")
            if _is_interactive():
                QMessageBox.warning(
                    self,
                    "Compliance gate required",
                    "Accept the Test Control Panel (TCP) on the Setup tab before running analysis.",
                )
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
