"""Results explorer (QAbstractTableModel + fetchMore + QSortFilterProxyModel, UX-5/UX-8) & Top-5 Selection."""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QImage, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import report, store
from ..mining.downsample import lttb_downsample, m4_downsample
from .widgets import FuncWorker, MplCanvas, PagedTableView, fill_table, make_table

log = logging.getLogger("energybaseline.gui")

REGISTER_COLUMNS: tuple[str, ...] = (
    "family_id",
    "family_name",
    "model_name",
    "requirement_tier",
    "status",
    "status_badge",
    "composite_score",
    "in_mcs",
    "r2",
    "cvrmse",
    "nmbe",
    "mae",
    "rmse",
    "wape",
    "n_obs",
    "n_params",
    "fit_seconds",
    "formula_summary",
    "reason",
)


def _synthesize_register_rows(tr: Any) -> list[dict[str, Any]]:
    """Build unified UX-5 Model Evaluation Register rows from TargetResult."""
    explicit = getattr(tr, "model_evaluation_register", None)
    if explicit:
        return [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in explicit]

    rows: list[dict[str, Any]] = []
    short_names = {str(s.get("name", "")) for s in getattr(tr, "shortlist", [])}
    mcs_names: set[str] = set()
    for tc in getattr(tr, "test_catalog", []):
        if tc.get("test_id") == "T10":
            det = tc.get("details") or {}
            mcs_names.update(str(x) for x in (det.get("mcs_superior_set") or []))

    for lb in getattr(tr, "leaderboard", []):
        mname = str(lb.get("model", ""))
        st = str(lb.get("status", "qualified"))
        if mname in short_names:
            badge = "SHORTLISTED"
        elif st == "qualified_not_shortlisted":
            badge = "QUALIFIED_NOT_SHORTLISTED"
        elif st in ("ok", "qualified", "shortlisted"):
            badge = "QUALIFIED"
        else:
            badge = st.upper()
        rows.append(
            {
                "family_id": str(lb.get("family_id") or f"T{lb.get('tier', 1)}"),
                "family_name": str(lb.get("family", "-")),
                "model_name": mname,
                "status_badge": badge,
                "composite_score": lb.get("score"),
                "in_mcs": (mname in mcs_names) if mcs_names else (mname in short_names),
                "r2": lb.get("r2"),
                "cvrmse": lb.get("cvrmse"),
                "nmbe": lb.get("nmbe"),
                "mae": lb.get("mae"),
                "rmse": lb.get("rmse"),
                "wape": lb.get("wape"),
                "n_params": lb.get("n_params"),
                "fit_seconds": lb.get("seconds"),
                "reason": str(lb.get("reason", "")),
            }
        )

    seen = {r["model_name"] for r in rows}
    for reg in getattr(tr, "register", []):
        mname = str(reg.get("model", ""))
        if mname in seen:
            continue
        st = str(reg.get("status", "skipped"))
        rows.append(
            {
                "family_id": str(reg.get("family_id", "-")),
                "family_name": str(reg.get("family", "-")),
                "model_name": mname,
                "status_badge": st.upper(),
                "composite_score": None,
                "in_mcs": False,
                "r2": None,
                "cvrmse": None,
                "nmbe": None,
                "mae": None,
                "rmse": None,
                "wape": None,
                "n_params": 0,
                "fit_seconds": 0.0,
                "reason": str(reg.get("reason", "")),
            }
        )
    return rows


class ResultsPage(QWidget):
    """Results explorer backed exclusively by PagedTableView (QAbstractTableModel + fetchMore + QSortFilterProxyModel)."""

    def __init__(self, state, main):
        super().__init__()
        self.state, self.main = state, main
        self.setAccessibleName("Results Explorer Page")
        self.setAccessibleDescription("Explore baseline metrics, Model Evaluation Register, EDA, mining, and statistical tests.")

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        target_lbl = QLabel("Target:")
        top.addWidget(target_lbl)
        self.combo = QComboBox()
        self.combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.combo.setAccessibleName("Target selector")
        self.combo.setAccessibleDescription("Select target energy meter variable to inspect results.")
        target_lbl.setBuddy(self.combo)
        self.combo.currentTextChanged.connect(self.render)
        top.addWidget(self.combo, 1)

        filter_lbl = QLabel("Filter tables:")
        top.addWidget(filter_lbl)
        self.register_filter = QLineEdit()
        self.register_filter.setPlaceholderText("Filter register / leaderboard (Ctrl+F)…")
        self.register_filter.setClearButtonEnabled(True)
        self.register_filter.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.register_filter.setAccessibleName("Table search filter")
        self.register_filter.setAccessibleDescription("Case-insensitive filter applied across Model Evaluation Register and leaderboard tables.")
        filter_lbl.setBuddy(self.register_filter)
        self.register_filter.textChanged.connect(self._apply_global_table_filter)
        top.addWidget(self.register_filter, 1)
        lay.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.tabs.setAccessibleName("Results categories tab bar")
        self.tabs.setAccessibleDescription("Switch between summary, Model Evaluation Register, leaderboard, diagnostics, EDA, mining, and report.")
        lay.addWidget(self.tabs, 1)

        self.summary = QTextBrowser()
        self.summary.setAccessibleName("Target analysis summary")
        self.summary.setAccessibleDescription("Temporal, data quality, threshold, and distribution summary.")

        # UX-5: Dedicated complete Model Evaluation Register (F1..F10 + candidate portfolio)
        reg_wrap = QWidget()
        reg_lay = QVBoxLayout(reg_wrap)
        reg_hdr = QLabel(
            "<b>Model Evaluation Register (F1–F10 &amp; Candidate Portfolio)</b> — "
            "every candidate family, accuracy metrics, composite score, Model Confidence Set (MCS) membership, and status badge."
        )
        reg_hdr.setWordWrap(True)
        reg_lay.addWidget(reg_hdr)
        self.eval_register_tbl: PagedTableView = make_table(
            accessible_name="Model Evaluation Register table",
            accessible_description="Complete register of candidate baseline families with metrics, composite score, MCS membership, and status badge.",
        )
        reg_lay.addWidget(self.eval_register_tbl, 1)

        self.leader: PagedTableView = make_table(
            accessible_name="Model Leaderboard table",
            accessible_description="Ranked candidate models ordered by composite score.",
        )
        self.rationale_tbl: PagedTableView = make_table(
            accessible_name="Decision rationale per criterion table",
            accessible_description="Criterion-by-criterion comparison between winner and runner-up.",
        )
        self.compare_tbl: PagedTableView = make_table(
            accessible_name="Top-3 comparative diagnostics table",
            accessible_description="Side-by-side residual and validation diagnostics for top 3 models.",
        )
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.addWidget(QLabel("<b>Decision rationale per criterion (winner vs runner-up)</b>"))
        rl.addWidget(self.rationale_tbl)
        rl.addWidget(QLabel("<b>Comparative diagnostics (Top-3 side by side)</b>"))
        rl.addWidget(self.compare_tbl)

        self.qualified_other_tbl: PagedTableView = make_table(
            accessible_name="Qualified non-shortlisted models register table",
            accessible_description="Models meeting ASHRAE/IPMVP qualification thresholds but ranked outside Top-5 (never mixed with rejected).",
        )
        self.register: PagedTableView = make_table(
            accessible_name="Rejected and skipped models register table",
            accessible_description="Models excluded, skipped, or failing ASHRAE/ISO thresholds with explicit reasons.",
        )
        self.excl: PagedTableView = make_table(
            accessible_name="Feature exclusions and reasons table",
            accessible_description="Variables excluded before or during feature screening and documented reasons.",
        )

        ew = QWidget()
        el = QVBoxLayout(ew)
        self.eda_canvas = MplCanvas((8, 5), accessible_name="Exploratory data analysis charts")
        self.rel_tbl: PagedTableView = make_table(
            accessible_name="Predictor-target relationships table",
            accessible_description="Descriptive Pearson, Spearman, and mutual information associations.",
        )
        el.addWidget(self.eda_canvas, 3)
        el.addWidget(self.rel_tbl, 2)

        self.mining: PagedTableView = make_table(
            accessible_name="Data mining and regime discovery table",
            accessible_description="Clustering validity indices, bootstrap ARI stability, PCA, and anomaly results.",
        )

        dw = QWidget()
        dl = QVBoxLayout(dw)
        self.diag_canvas = MplCanvas((8, 5), accessible_name="Baseline diagnostics and residual charts")
        dl.addWidget(self.diag_canvas)

        self.catalog_tbl: PagedTableView = make_table(
            accessible_name="Statistical Test Catalog T1 to T14 table",
            accessible_description="Pre-model, post-model OOF, and causal group statistical tests with raw and adjusted p-values.",
        )
        self.rep_tab = self._report_tab()

        self._all_tables: tuple[PagedTableView, ...] = (
            self.eval_register_tbl,
            self.leader,
            self.rationale_tbl,
            self.compare_tbl,
            self.register,
            self.excl,
            self.rel_tbl,
            self.mining,
            self.catalog_tbl,
        )

        for idx_tab, (w, n) in enumerate(
            (
                (self.summary, "Summary & Temporal"),
                (reg_wrap, "Model Evaluation Register"),
                (self.leader, "Model Leaderboard"),
                (rw, "Decision Rationale"),
                (self.qualified_other_tbl, "Qualified (Not Top-5)"),
                (self.register, "Rejected Models Register"),
                (self.excl, "Exclusions & Reasons"),
                (ew, "EDA"),
                (self.mining, "Data Mining"),
                (dw, "Diagnostics & Interpretability"),
                (self.catalog_tbl, "Test Catalog (T1-T14)"),
                (self.rep_tab, "Report"),
            )
        ):
            self.tabs.addTab(w, n)
            if n == "Model Evaluation Register":
                self._register_tab_index = idx_tab

        # UX-8: Explicit keyboard tab order + primary action shortcuts
        QWidget.setTabOrder(self.combo, self.register_filter)
        QWidget.setTabOrder(self.register_filter, self.tabs)
        QWidget.setTabOrder(self.tabs, self.eval_register_tbl)
        QWidget.setTabOrder(self.eval_register_tbl, self.gen_btn)
        QWidget.setTabOrder(self.gen_btn, self.prev_btn)
        QWidget.setTabOrder(self.prev_btn, self.open_btn)

        self._sc_filter = QShortcut(QKeySequence("Ctrl+F"), self)
        self._sc_filter.activated.connect(self.register_filter.setFocus)
        self._sc_register = QShortcut(QKeySequence("Alt+R"), self)
        self._sc_register.activated.connect(lambda: self.tabs.setCurrentIndex(self._register_tab_index))
        self._sc_preview = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        self._sc_preview.activated.connect(self.preview)
        self._sc_generate = QShortcut(QKeySequence("Ctrl+Shift+G"), self)
        self._sc_generate.activated.connect(self.generate)

        state.run_changed.connect(self.reload)
        state.selection_changed.connect(self._update_preview_hint)

    @property
    def data_tables(self) -> tuple[PagedTableView, ...]:
        return self._all_tables

    def _apply_global_table_filter(self, text: str) -> None:
        for tbl in (self.eval_register_tbl, self.leader, self.qualified_other_tbl, self.register, self.catalog_tbl):
            tbl.set_filter_text(text)

    def populate_model_evaluation_register(self, entries: Sequence[Any]) -> pd.DataFrame:
        """Populate the UX-5 Model Evaluation Register table from ModelEvaluationEntry objects or dicts."""
        rows = [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in entries]
        if rows:
            df = pd.DataFrame(rows)
            ordered = [c for c in REGISTER_COLUMNS if c in df.columns] + [
                c for c in df.columns if c not in REGISTER_COLUMNS
            ]
            df = df[ordered]
        else:
            df = pd.DataFrame(columns=list(REGISTER_COLUMNS))
        fill_table(self.eval_register_tbl, df)
        return df

    def _report_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        h = QHBoxLayout()
        self.gen_btn = QPushButton("Generate PDF + Excel + HTML report")
        self.gen_btn.setObjectName("primary")
        self.gen_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.gen_btn.setAccessibleName("Generate compliance report button")
        self.gen_btn.setAccessibleDescription("Export PDF, Excel, and HTML baseline reports (Ctrl+Shift+G).")
        self.gen_btn.clicked.connect(self.generate)

        self.prev_btn = QPushButton("Preview report")
        self.prev_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.prev_btn.setAccessibleName("Preview report button")
        self.prev_btn.setAccessibleDescription("Render HTML preview inside the report browser (Ctrl+Shift+P).")
        self.prev_btn.clicked.connect(self.preview)

        self.open_btn = QPushButton("Open reports folder")
        self.open_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.open_btn.setAccessibleName("Open reports folder button")
        self.open_btn.setAccessibleDescription("Open local directory containing generated report files.")
        self.open_btn.clicked.connect(self.open_folder)

        for b in (self.gen_btn, self.prev_btn, self.open_btn):
            h.addWidget(b)
        self.rep_status = QLabel("")
        self.rep_status.setAccessibleName("Report generation status")
        h.addWidget(self.rep_status, 1)
        layout.addLayout(h)
        self.browser = QTextBrowser()
        self.browser.setAccessibleName("Report preview browser")
        layout.addWidget(self.browser, 1)
        return w

    def _update_preview_hint(self):
        self.rep_status.setText("Selections changed - regenerate the report to include them.")

    def reload(self):
        run = self.state.run
        self.combo.blockSignals(True)
        self.combo.clear()
        if run:
            self.combo.addItems(list(run.targets))
        self.combo.blockSignals(False)
        self.render()

    def render(self, *_):
        run, t = self.state.run, self.combo.currentText()
        if not run or t not in run.targets:
            return
        tr = run.targets[t]
        if tr.status != "ok":
            self.summary.setHtml(f"<h3 style='color:#b42318'>{t}: analysis failed</h3><p>{tr.error}</p>")
            return
        tp = tr.temporal
        html = (
            f"<h3>{t}</h3><p><b>Temporal:</b> declared {tp.get('declared')}, detected {tp.get('detected')}, applied {tp.get('applied')}; "
            f"regularity {tp.get('regularity', 0):.0%}; rows {tp.get('n_input_rows')} -> {tp.get('n_output_rows')}; "
            f"duplicates {tp.get('n_duplicates')}; gap intervals {tp.get('n_gaps_filled_as_missing')}</p>"
            f"<p>Baseline window {tp.get('baseline_window')}; reporting window {tp.get('reporting_window')}; timezone {tp.get('timezone')}</p>"
            + "".join(f"<p style='color:#b45309'>Warning: {m}</p>" for m in tp.get("messages", []))
            + f"<p><b>Quality score:</b> {tr.quality.get('score')}/100 ({tr.quality})</p>"
            f"<p><b>Outliers:</b> method {tr.outliers.get('method')}, treatment {tr.outliers.get('treatment')}, target rows flagged {tr.outliers.get('target_flagged')}</p>"
            f"<p><b>Thresholds:</b> CV(RMSE) <= {tr.thresholds.get('max_cvrmse')} %, |NMBE| <= {tr.thresholds.get('max_nmbe')} %, R2 >= {tr.thresholds.get('min_r2')}; "
            f"scoring profile <b>{tr.scoring.get('profile')}</b></p><p><b>Features kept ({len(tr.selected_features)}):</b> {', '.join(tr.selected_features)}</p>"
            + "".join(f"<p>* {n}</p>" for n in tr.notes)
            + (f"<p style='color:#b45309'><b>{tr.notice}</b></p>" if tr.notice else "")
            + "<h4>Distribution</h4><p>"
            + json.dumps(tr.eda.get("distribution", {}), default=str)
            + "</p><h4>Engineering plausibility</h4>"
            + "".join(f"<p>* {p}</p>" for p in tr.eda.get("plausibility", []))
        )
        self.summary.setHtml(html)

        self.populate_model_evaluation_register(_synthesize_register_rows(tr))

        lb = pd.DataFrame(tr.leaderboard)
        if not lb.empty:
            show = [c for c in lb.columns if not c.startswith("c_") and c not in ("reason",)] + (
                ["reason"] if "reason" in lb.columns else []
            )
            fill_table(self.leader, lb[show])
        else:
            fill_table(self.leader, lb)
        fill_table(self.rationale_tbl, pd.DataFrame(tr.rationale))
        fill_table(self.compare_tbl, pd.DataFrame(tr.comparative))
        fill_table(self.qualified_other_tbl, pd.DataFrame(getattr(tr, "qualified_not_shortlisted_register", [])))
        fill_table(self.register, pd.DataFrame(tr.register))
        fill_table(self.excl, pd.DataFrame(tr.exclusions))
        fill_table(self.rel_tbl, tr.eda.get("relationships"))
        fill_table(
            self.mining,
            pd.DataFrame(
                [
                    {
                        "method": m["method"],
                        "status": m["status"],
                        "parameters": json.dumps(m["params"], default=str),
                        "results": json.dumps(m["results"], default=str)[:300],
                        "interpretation": m["interpretation"],
                        "downstream": m["downstream"],
                    }
                    for m in tr.mining
                ]
            ),
        )
        fill_table(
            self.catalog_tbl,
            pd.DataFrame(
                [
                    {
                        "test_id": tc["test_id"],
                        "stage": tc["stage"],
                        "name": tc["name"],
                        "statistic": tc["statistic"],
                        "p_raw": tc["p_raw"],
                        "p_adj": tc["p_adj"],
                        "effect_size": tc["effect_size"],
                        "ci_method": (tc.get("ci") or {}).get("method"),
                        "inference": tc.get("inference_label"),
                        "decision": tc["decision"],
                        "downstream_action": tc["downstream_action"],
                    }
                    for tc in getattr(tr, "test_catalog", [])
                ]
            ),
        )
        self._draw_eda(tr)
        self._draw_diag(tr)

    @staticmethod
    def _plot_downsampled(ax: Any, idx: pd.Index, values: pd.Series | np.ndarray, *, label: str | None = None, lw: float = 0.6) -> None:
        yv = np.asarray(values, dtype=float)
        if yv.size > 2000:
            ds = m4_downsample(np.arange(yv.size, dtype=float), yv, pixel_width=500)
            ax.plot(idx[ds.indices], yv[ds.indices], lw=lw, label=label)
        else:
            ax.plot(idx, yv, lw=lw, label=label)

    def _draw_eda(self, tr):
        fig = self.eda_canvas.fig
        fig.clear()
        if tr.series is not None:
            a = fig.add_subplot(221)
            self._plot_downsampled(a, tr.series.index, tr.series["actual"], lw=0.6)
            a.set_title("Target (baseline period)", fontsize=8)
            prof = tr.eda.get("profiles", {})
            b = fig.add_subplot(222)
            if "hour_of_day" in prof:
                prof["hour_of_day"].plot(ax=b)
                b.set_title("Hourly load profile", fontsize=8)
            elif "day_of_week" in prof:
                prof["day_of_week"]["mean"].plot(kind="bar", ax=b)
                b.set_title("Mean by day of week", fontsize=8)
            elif "month" in prof:
                prof["month"]["mean"].plot(kind="bar", ax=b)
                b.set_title("Mean by month", fontsize=8)
            c = fig.add_subplot(223)
            act = tr.series["actual"].dropna().to_numpy(dtype=float)
            if act.size > 50000:
                ds_h = lttb_downsample(np.arange(act.size, dtype=float), act, threshold=10000)
                c.hist(ds_h.y, bins=40)
            else:
                c.hist(act, bins=40)
            c.set_title("Distribution", fontsize=8)
            rel = tr.eda.get("relationships")
            d = fig.add_subplot(224)
            if rel is not None and len(rel):
                r = rel.head(8)[::-1]
                d.barh(r["variable"], r["pearson"])
                d.set_title("Pearson r with target", fontsize=8)
            for ax in fig.axes:
                ax.tick_params(labelsize=7)
        self.eda_canvas.draw_idle()

    def _draw_diag(self, tr):
        fig = self.diag_canvas.fig
        fig.clear()
        if tr.series is not None and tr.shortlist:
            s = tr.series
            a = fig.add_subplot(221)
            self._plot_downsampled(a, s.index, s["actual"], lw=0.5, label="actual")
            self._plot_downsampled(a, s.index, s["baseline_fit"], lw=0.5, label="baseline fit")
            a.legend(fontsize=6)
            a.set_title(f"Rank-1: {tr.shortlist[0]['name']}", fontsize=8)
            res = (s["actual"] - s["baseline_oof"]).dropna()
            b = fig.add_subplot(222)
            b.hist(res, bins=40)
            b.set_title("Out-of-fold residuals", fontsize=8)
            imp = (tr.shortlist[0].get("importance") or {}).get("permutation") or {}
            c = fig.add_subplot(223)
            if imp:
                items = list(imp.items())[:8][::-1]
                c.barh([k for k, _ in items], [v for _, v in items])
                c.set_title("Permutation importance", fontsize=8)
            d = fig.add_subplot(224)
            hist = next((sl["history"] for sl in tr.shortlist if sl.get("history")), None)
            pdp = (tr.shortlist[0].get("importance") or {}).get("pdp") or {}
            if hist:
                d.plot(hist["train_loss"], label="train")
                d.plot(hist["val_loss"], label="val")
                d.legend(fontsize=6)
                d.set_title("NN training curves", fontsize=8)
            elif pdp:
                k, v = next(iter(pdp.items()))
                d.plot(v["x"], v["y"], marker="o")
                d.set_title(f"Partial dependence: {k}", fontsize=8)
            for ax in fig.axes:
                ax.tick_params(labelsize=7)
        self.diag_canvas.draw_idle()

    # ------------------------------------------------------------ report
    def _selections(self):
        return store.selection_history(self.state.run_id)

    def generate(self):
        run = self.state.run
        if not run:
            QMessageBox.information(self, "Report", "Run an analysis first.")
            return
        self.gen_btn.setEnabled(False)
        self.rep_status.setText("Generating report (PDF, Excel, HTML)...")
        sel, aud, dd = self._selections(), store.audit_entries(self.state.run_id), self.state.data_dictionary
        self._gen_args = (run, sel, aud, dd)
        self.worker = FuncWorker(report.generate_all, run, sel, aud, dd, ("xlsx", "html"))
        self.worker.ok.connect(self._gen_ok)
        self.worker.fail.connect(self._gen_fail)
        self.worker.start()

    def _gen_ok(self, out):
        try:
            run, sel, aud, dd = self._gen_args
            out.update(report.generate_all(run, sel, aud, dd, ("pdf",)))
        except Exception as exc:
            QMessageBox.warning(self, "Report", f"PDF export failed: {exc}")
        self.gen_btn.setEnabled(True)
        self.rep_status.setText("Saved: " + " | ".join(f"{k}: {v}" for k, v in out.items() if k != "pdf_engine"))
        store.audit("Report generated: " + json.dumps(out), "report", run_id=self.state.run_id)
        self.main.log_msg("Report generated: " + out.get("pdf", ""))

    def _gen_fail(self, msg):
        self.gen_btn.setEnabled(True)
        self.rep_status.setText("Report failed.")
        QMessageBox.critical(self, "Report", msg)

    def preview(self):
        run = self.state.run
        if not run:
            return
        html, charts = report.render_html(
            run, self._selections(), store.audit_entries(self.state.run_id), self.state.data_dictionary, embed=False
        )
        doc = self.browser.document()
        doc.clear()
        for name, png in charts.items():
            doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), QImage.fromData(png))
        self.browser.setHtml(html)

    def open_folder(self):
        from ..config import ensure_dirs

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_dirs()["reports"])))


class SelectionPage(QWidget):
    """Top-5 shortlist per target with explicit formulas; the user must pick one model per target."""

    def __init__(self, state, main):
        super().__init__()
        self.state, self.main = state, main
        self.setAccessibleName("Top-5 Baseline Selection Page")
        outer = QVBoxLayout(self)
        outer.addWidget(
            QLabel(
                "<b>Baseline Selection</b> - choose ONE model per target. The baseline is locked only after you click "
                "<i>Lock baseline</i>; this unlocks Prediction &amp; Monitoring for that target. You can change the selection later (history is kept)."
            )
        )
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAccessibleName("Baseline candidates scroll container")
        self.inner = QWidget()
        self.vbox = QVBoxLayout(self.inner)
        self.scroll.setWidget(self.inner)
        outer.addWidget(self.scroll, 1)
        self.groups: dict[str, QButtonGroup] = {}
        self.notes: dict[str, QLineEdit] = {}
        self.status: dict[str, QLabel] = {}
        self.ack_checks: dict[str, QCheckBox] = {}
        self.candidate_cards: dict[str, list[QFrame]] = {}
        state.run_changed.connect(self.rebuild)

    def is_shortlist_visible_before_lock(self, target: str) -> bool:
        """Verify Gate G6 invariant: Top-5 candidate cards are rendered and visible BEFORE baseline lock."""
        cards = self.candidate_cards.get(target, [])
        return bool(len(cards) > 0 and target not in self.state.locked)

    def rebuild(self):
        while self.vbox.count():
            it = self.vbox.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.groups, self.notes, self.status, self.ack_checks, self.candidate_cards = {}, {}, {}, {}, {}
        run = self.state.run
        if not run:
            return
        self.state.locked = {}
        for t, tr in run.targets.items():
            gb = QGroupBox(f"Target: {t}")
            gb.setAccessibleName(f"Baseline shortlist group for {t}")
            gl = QVBoxLayout(gb)
            if tr.status != "ok":
                gl.addWidget(QLabel(f"<span style='color:#b42318'>Analysis failed: {tr.error}</span>"))
                self.vbox.addWidget(gb)
                continue
            if tr.notice:
                n = QLabel(tr.notice)
                n.setObjectName("banner")
                n.setWordWrap(True)
                gl.addWidget(n)
            sep_regs = getattr(tr, "separated_registers", {}) or {}
            if sep_regs.get("requires_user_acknowledgement"):
                ack_cb = QCheckBox(
                    "SC-9 Acknowledgement: I understand zero models met strict ASHRAE 14 / IPMVP thresholds and "
                    "accept diagnostic-only baseline selection with documented justification."
                )
                ack_cb.setAccessibleName(f"SC-9 zero-qualified acknowledgement checkbox for {t}")
                gl.addWidget(ack_cb)
                self.ack_checks[t] = ack_cb
            grp = QButtonGroup(gb)
            cards_t: list[QFrame] = []
            for s in tr.shortlist:
                card = QFrame()
                card.setFrameShape(QFrame.Shape.StyledPanel)
                cl = QVBoxLayout(card)
                rb = QRadioButton(f"#{s['rank']}  {s['name']}   [{s['family']}]   score {s['score']:.1f}/100")
                rb.setAccessibleName(f"Select candidate {s['name']} for target {t}")
                f = QFont()
                f.setBold(True)
                rb.setFont(f)
                rb.setProperty("model", s["name"])
                rb.setProperty("rank", int(s["rank"]))
                grp.addButton(rb)
                cl.addWidget(rb)
                m = s["metrics"]
                u = s["uncertainty"]
                cl.addWidget(
                    QLabel(
                        f"R2 {m['r2']:.3f} | CV(RMSE) {m['cvrmse']:.2f} % | NMBE {m['nmbe']:+.2f} % | MAE {m['mae']:.4g} | MAPE {m['mape']:.2f} % | sMAPE {m['smape']:.2f} %   |   "
                        f"95 % interval: {u['lower']:+.4g} ... {u['upper']:+.4g} ({u['basis']})   |   parameters: {s['n_params']}"
                    )
                )
                why = QLabel("Why shortlisted: " + s["reason"] + (" " + "; ".join(s["failures"]) if s["failures"] else ""))
                why.setWordWrap(True)
                cl.addWidget(why)
                fml_dict = s["formula"]
                is_param_card = fml_dict.get("kind") == "equation"
                rep_hdr = QLabel(
                    "<b>Closed-form prediction equation (parametric baseline):</b>"
                    if is_param_card
                    else "<b>Architecture, hyperparameters &amp; approximate diagnostic surrogate (NOT model formula):</b>"
                )
                cl.addWidget(rep_hdr)
                surr_meta = fml_dict.get("surrogate")
                if not is_param_card and isinstance(surr_meta, dict):
                    surr_lbl = QLabel(
                        f"<span style='color:#92400e'><b>[{surr_meta.get('surrogate_banner', 'APPROXIMATE_DIAGNOSTIC_SURROGATE')}]</b> "
                        f"Fidelity R² = {float(surr_meta.get('fidelity_r2', 0.0)):.3f} — Diagnostic approximation ONLY; "
                        "never treated as the non-parametric model's formula.</span>"
                    )
                    surr_lbl.setWordWrap(True)
                    cl.addWidget(surr_lbl)
                te = QTextEdit()
                te.setReadOnly(True)
                te.setAccessibleName(
                    f"Closed-form formula for {s['name']}"
                    if is_param_card
                    else f"Architecture and labelled diagnostic surrogate for {s['name']}"
                )
                te.setFont(QFont("Consolas", 9))
                te.setPlainText(fml_dict["text"])
                te.setMinimumHeight(110)
                te.setMaximumHeight(190)
                cl.addWidget(te)
                gl.addWidget(card)
                cards_t.append(card)
            self.candidate_cards[t] = cards_t
            qual_other = getattr(tr, "qualified_not_shortlisted_register", []) or []
            rej = [r for r in tr.register if r["status"] == "rejected"]
            gl.addWidget(
                QLabel(
                    f"{len(qual_other)} additional model(s) qualified outside Top-5 (see <i>Qualified (Not Top-5)</i> tab); "
                    f"{len(rej)} model(s) were rejected (see <i>Rejected Models Register</i> tab)."
                )
            )
            row = QHBoxLayout()
            note = QLineEdit()
            note.setPlaceholderText("Audit rationale note (required >=15 chars when overriding Rank #1 or acknowledging SC-9)")
            note.setAccessibleName(f"Audit rationale note for {t}")
            row.addWidget(note, 1)
            btn = QPushButton("Lock baseline")
            btn.setObjectName("primary")
            btn.setAccessibleName(f"Lock baseline button for {t}")
            btn.clicked.connect(lambda _=False, tt=t: self.lock(tt))
            row.addWidget(btn)
            gl.addLayout(row)
            st = QLabel("Not locked - Prediction & Monitoring is locked for this target.")
            gl.addWidget(st)
            self.groups[t], self.notes[t], self.status[t] = grp, note, st
            self.vbox.addWidget(gb)
            prev = store.active_selection(self.state.run_id, t) if self.state.run_id else None
            if prev:
                for b in grp.buttons():
                    if b.property("model") == prev["model"]:
                        b.setChecked(True)
                self.state.locked[t] = prev
                st.setText(f"<b>Locked:</b> {prev['model']} (at {prev['at']})")
            elif grp.buttons():
                grp.buttons()[0].setChecked(True)
        self.vbox.addStretch(1)
        self.state.selection_changed.emit()

    def lock(self, target: str):
        grp = self.groups.get(target)
        btn = grp.checkedButton() if grp else None
        if not btn:
            QMessageBox.information(self, "Baseline selection", "Select one model first.")
            return
        model = btn.property("model")
        rank_sel = int(btn.property("rank") or 1)
        note_txt = self.notes[target].text().strip()
        ack_cb = self.ack_checks.get(target)
        if ack_cb is not None and not ack_cb.isChecked():
            QMessageBox.warning(
                self,
                "SC-9 Acknowledgement Required",
                "Zero models met strict ASHRAE 14 / IPMVP thresholds. Check the SC-9 acknowledgement box and enter an engineering rationale before locking.",
            )
            return
        if (rank_sel > 1 or ack_cb is not None) and len(note_txt) < 15:
            QMessageBox.warning(
                self,
                "SC-7 Engineering Justification Required",
                "Overriding the Rank #1 baseline (or locking under SC-9) requires a documented engineering justification of at least 15 characters in the audit note.",
            )
            return
        prev = self.state.locked.get(target)
        if prev and prev["model"] != model:
            if (
                QMessageBox.question(
                    self,
                    "Change baseline",
                    f"Append a new immutable baseline version '{model}' superseding '{prev['model']}'? Prior locked versions remain intact.",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        sel = store.lock_baseline(self.state.run_id, target, model, note_txt)
        self.state.locked[target] = {"model": model, "run_id": self.state.run_id, "at": sel["at"], "target": target}
        self.status[target].setText(
            f"<b>Locked:</b> {model} (at {sel['at']}). Prediction & Monitoring is now unlocked for '{target}'."
        )
        self.main.log_msg(f"Baseline locked for {target}: {model}")
        self.state.selection_changed.emit()
