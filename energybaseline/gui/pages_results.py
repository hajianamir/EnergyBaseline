"""Results explorer and the Top-5 Baseline Selection screen."""
from __future__ import annotations

import json
import logging

import pandas as pd
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont, QImage, QTextDocument
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QScrollArea, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from ..core import report, store
from .widgets import FuncWorker, MplCanvas, fill_table, make_table

log = logging.getLogger("energybaseline.gui")


class ResultsPage(QWidget):
    def __init__(self, state, main):
        super().__init__()
        self.state, self.main = state, main
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Target:"))
        self.combo = QComboBox()
        self.combo.currentTextChanged.connect(self.render)
        top.addWidget(self.combo, 1)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.summary = QTextBrowser()
        self.leader = make_table()
        self.rationale_tbl, self.compare_tbl = make_table(), make_table()
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.addWidget(QLabel("<b>Decision rationale per criterion (winner vs runner-up)</b>"))
        rl.addWidget(self.rationale_tbl)
        rl.addWidget(QLabel("<b>Comparative diagnostics (Top-3 side by side)</b>"))
        rl.addWidget(self.compare_tbl)
        self.register = make_table()
        self.excl = make_table()
        ew = QWidget()
        el = QVBoxLayout(ew)
        self.eda_canvas = MplCanvas((8, 5))
        self.rel_tbl = make_table()
        el.addWidget(self.eda_canvas, 3)
        el.addWidget(self.rel_tbl, 2)
        self.mining = make_table()
        dw = QWidget()
        dl = QVBoxLayout(dw)
        self.diag_canvas = MplCanvas((8, 5))
        dl.addWidget(self.diag_canvas)
        self.catalog_tbl = make_table()
        self.rep_tab = self._report_tab()
        for w, n in ((self.summary, "Summary & Temporal"), (self.leader, "Model Leaderboard"), (rw, "Decision Rationale"), (self.register, "Rejected Models Register"),
                     (self.excl, "Exclusions & Reasons"), (ew, "EDA"), (self.mining, "Data Mining"), (dw, "Diagnostics & Interpretability"), (self.catalog_tbl, "Test Catalog (T1-T14)"), (self.rep_tab, "Report")):
            self.tabs.addTab(w, n)
        state.run_changed.connect(self.reload)
        state.selection_changed.connect(self._update_preview_hint)

    def _report_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        h = QHBoxLayout()
        self.gen_btn = QPushButton("Generate PDF + Excel + HTML report")
        self.gen_btn.setObjectName("primary")
        self.gen_btn.clicked.connect(self.generate)
        self.prev_btn = QPushButton("Preview report")
        self.prev_btn.clicked.connect(self.preview)
        self.open_btn = QPushButton("Open reports folder")
        self.open_btn.clicked.connect(self.open_folder)
        for b in (self.gen_btn, self.prev_btn, self.open_btn):
            h.addWidget(b)
        self.rep_status = QLabel("")
        h.addWidget(self.rep_status, 1)
        l.addLayout(h)
        self.browser = QTextBrowser()
        l.addWidget(self.browser, 1)
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
        html = (f"<h3>{t}</h3><p><b>Temporal:</b> declared {tp.get('declared')}, detected {tp.get('detected')}, applied {tp.get('applied')}; "
                f"regularity {tp.get('regularity', 0):.0%}; rows {tp.get('n_input_rows')} → {tp.get('n_output_rows')}; duplicates {tp.get('n_duplicates')}; gap intervals {tp.get('n_gaps_filled_as_missing')}</p>"
                f"<p>Baseline window {tp.get('baseline_window')}; reporting window {tp.get('reporting_window')}; timezone {tp.get('timezone')}</p>"
                + "".join(f"<p style='color:#b45309'>⚠ {m}</p>" for m in tp.get("messages", []))
                + f"<p><b>Quality score:</b> {tr.quality.get('score')}/100 ({tr.quality})</p>"
                f"<p><b>Outliers:</b> method {tr.outliers.get('method')}, treatment {tr.outliers.get('treatment')}, target rows flagged {tr.outliers.get('target_flagged')}</p>"
                f"<p><b>Thresholds:</b> CV(RMSE) ≤ {tr.thresholds.get('max_cvrmse')} %, |NMBE| ≤ {tr.thresholds.get('max_nmbe')} %, R² ≥ {tr.thresholds.get('min_r2')}; "
                f"scoring profile <b>{tr.scoring.get('profile')}</b></p><p><b>Features kept ({len(tr.selected_features)}):</b> {', '.join(tr.selected_features)}</p>"
                + "".join(f"<p>• {n}</p>" for n in tr.notes) + (f"<p style='color:#b45309'><b>{tr.notice}</b></p>" if tr.notice else "")
                + "<h4>Distribution</h4><p>" + json.dumps(tr.eda.get("distribution", {}), default=str) + "</p><h4>Engineering plausibility</h4>"
                + "".join(f"<p>• {p}</p>" for p in tr.eda.get("plausibility", [])))
        self.summary.setHtml(html)
        lb = pd.DataFrame(tr.leaderboard)
        show = [c for c in lb.columns if not c.startswith("c_") and c not in ("reason",)] + ["reason"]
        fill_table(self.leader, lb[show])
        fill_table(self.rationale_tbl, pd.DataFrame(tr.rationale))
        fill_table(self.compare_tbl, pd.DataFrame(tr.comparative))
        fill_table(self.register, pd.DataFrame(tr.register))
        fill_table(self.excl, pd.DataFrame(tr.exclusions))
        fill_table(self.rel_tbl, tr.eda.get("relationships"))
        fill_table(self.mining, pd.DataFrame([{"method": m["method"], "status": m["status"], "parameters": json.dumps(m["params"], default=str), "results": json.dumps(m["results"], default=str)[:300],
                                               "interpretation": m["interpretation"], "downstream": m["downstream"]} for m in tr.mining]))
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

    def _draw_eda(self, tr):
        fig = self.eda_canvas.fig
        fig.clear()
        if tr.series is not None:
            a = fig.add_subplot(221)
            a.plot(tr.series.index, tr.series["actual"], lw=0.6)
            a.set_title("Target (baseline period)", fontsize=8)
            prof = tr.eda.get("profiles", {})
            b = fig.add_subplot(222)
            if "hour_of_day" in prof:
                prof["hour_of_day"].plot(ax=b)
                b.set_title("Hourly load profile", fontsize=8)
            elif "day_of_week" in prof:
                prof["day_of_week"]["mean"].plot(kind="bar", ax=b)
                b.set_title("Mean by day of week", fontsize=8)
            else:
                prof["month"]["mean"].plot(kind="bar", ax=b)
                b.set_title("Mean by month", fontsize=8)
            c = fig.add_subplot(223)
            c.hist(tr.series["actual"], bins=40)
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
            a.plot(s.index, s["actual"], lw=0.5, label="actual")
            a.plot(s.index, s["baseline_fit"], lw=0.5, label="baseline fit")
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
        self.rep_status.setText("Generating report (PDF, Excel, HTML)…")
        sel, aud, dd = self._selections(), store.audit_entries(self.state.run_id), self.state.data_dictionary
        self._gen_args = (run, sel, aud, dd)
        self.worker = FuncWorker(report.generate_all, run, sel, aud, dd, ("xlsx", "html"))
        self.worker.ok.connect(self._gen_ok)
        self.worker.fail.connect(self._gen_fail)
        self.worker.start()

    def _gen_ok(self, out):
        try:  # PDF on the GUI thread (Qt text engine fallback must not run in a worker thread)
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
        html, charts = report.render_html(run, self._selections(), store.audit_entries(self.state.run_id), self.state.data_dictionary, embed=False)
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
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("<b>Baseline Selection</b> - choose ONE model per target. The baseline is locked only after you click "
                               "<i>Lock baseline</i>; this unlocks Prediction &amp; Monitoring for that target. You can change the selection later (history is kept)."))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.vbox = QVBoxLayout(self.inner)
        self.scroll.setWidget(self.inner)
        outer.addWidget(self.scroll, 1)
        self.groups: dict[str, QButtonGroup] = {}
        self.notes: dict[str, QLineEdit] = {}
        self.status: dict[str, QLabel] = {}
        state.run_changed.connect(self.rebuild)

    def rebuild(self):
        while self.vbox.count():
            it = self.vbox.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.groups, self.notes, self.status = {}, {}, {}
        run = self.state.run
        if not run:
            return
        self.state.locked = {}
        for t, tr in run.targets.items():
            gb = QGroupBox(f"Target: {t}")
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
            grp = QButtonGroup(gb)
            for s in tr.shortlist:
                card = QFrame()
                card.setFrameShape(QFrame.Shape.StyledPanel)
                cl = QVBoxLayout(card)
                rb = QRadioButton(f"#{s['rank']}  {s['name']}   [{s['family']}]   score {s['score']:.1f}/100")
                f = QFont()
                f.setBold(True)
                rb.setFont(f)
                rb.setProperty("model", s["name"])
                grp.addButton(rb)
                cl.addWidget(rb)
                m = s["metrics"]
                u = s["uncertainty"]
                cl.addWidget(QLabel(f"R² {m['r2']:.3f} · CV(RMSE) {m['cvrmse']:.2f} % · NMBE {m['nmbe']:+.2f} % · MAE {m['mae']:.4g} · MAPE {m['mape']:.2f} % · sMAPE {m['smape']:.2f} %   |   "
                                    f"95 % interval: {u['lower']:+.4g} … {u['upper']:+.4g} ({u['basis']})   |   parameters: {s['n_params']}"))
                why = QLabel("Why shortlisted: " + s["reason"] + (" " + "; ".join(s["failures"]) if s["failures"] else ""))
                why.setWordWrap(True)
                cl.addWidget(why)
                te = QTextEdit()
                te.setReadOnly(True)
                te.setFont(QFont("Consolas", 9))
                te.setPlainText(s["formula"]["text"])
                te.setMinimumHeight(110)
                te.setMaximumHeight(190)
                cl.addWidget(te)
                gl.addWidget(card)
            rej = [r for r in tr.register if r["status"] == "rejected"]
            gl.addWidget(QLabel(f"{len(rej)} further model(s) were rejected - every reason is listed in <i>Results → Rejected Models Register</i> and in the report."))
            row = QHBoxLayout()
            note = QLineEdit()
            note.setPlaceholderText("Optional note for the audit log (why this model?)")
            row.addWidget(note, 1)
            btn = QPushButton("Lock baseline")
            btn.setObjectName("primary")
            btn.clicked.connect(lambda _=False, tt=t: self.lock(tt))
            row.addWidget(btn)
            gl.addLayout(row)
            st = QLabel("Not locked - Prediction & Monitoring is locked for this target.")
            gl.addWidget(st)
            self.groups[t], self.notes[t], self.status[t] = grp, note, st
            self.vbox.addWidget(gb)
            # restore a previously locked selection (same run)
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
        prev = self.state.locked.get(target)
        if prev and prev["model"] != model:
            if QMessageBox.question(self, "Change baseline", f"Replace the locked baseline '{prev['model']}' by '{model}'? The previous selection is kept in the history.") != QMessageBox.StandardButton.Yes:
                return
        sel = store.lock_baseline(self.state.run_id, target, model, self.notes[target].text().strip())
        self.state.locked[target] = {"model": model, "run_id": self.state.run_id, "at": sel["at"], "target": target}
        self.status[target].setText(f"<b>Locked:</b> {model} (at {sel['at']}). Prediction & Monitoring is now unlocked for '{target}'.")
        self.main.log_msg(f"Baseline locked for {target}: {model}")
        self.state.selection_changed.emit()
