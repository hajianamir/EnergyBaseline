"""Report generation: interim HTML -> PDF fallback (Qt text engine / matplotlib; ReportLab lands in Phase 8), multi-sheet Excel, dashboard exports."""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, select_autoescape
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ..config import ensure_dirs, get_paths
from ..db.session import db_status
from .pipeline import RunResult, TargetResult
from .report_template import REPORT_TEMPLATE
from .scoring import WEIGHTS

log = logging.getLogger("energybaseline.report")


# ------------------------------------------------------------------ charts
def _png(fig: Figure) -> bytes:
    buf = io.BytesIO()
    FigureCanvasAgg(fig)
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    return buf.getvalue()


def make_charts(tr: TargetResult, sel: dict | None) -> dict[str, bytes]:
    ch: dict[str, bytes] = {}
    if tr.status != "ok" or tr.series is None:
        return ch
    s = tr.series
    fig = Figure(figsize=(8, 2.6))
    ax = fig.add_subplot(111)
    ax.plot(s.index, s["actual"], lw=0.7, label="actual", color="#0b4f6c")
    ax.set_title(f"{tr.target}: measured series (baseline period)")
    ax.legend(loc="upper right", fontsize=7)
    ch["series"] = _png(fig)
    prof = tr.eda.get("profiles", {})
    fig = Figure(figsize=(8, 2.6))
    if "hour_of_day" in prof:
        ax = fig.add_subplot(111)
        prof["hour_of_day"].plot(ax=ax)
        ax.set_title("Average load profile by hour (weekday vs weekend)")
    elif "day_of_week" in prof:
        ax = fig.add_subplot(111)
        prof["day_of_week"]["mean"].plot(kind="bar", ax=ax, color="#146c94")
        ax.set_title("Average by day of week (0=Mon)")
    else:
        ax = fig.add_subplot(111)
        prof["month"]["mean"].plot(kind="bar", ax=ax, color="#146c94")
        ax.set_title("Average by month")
    ch["profile"] = _png(fig)
    pick = sel or (tr.shortlist[0] if tr.shortlist else None)
    if pick is not None:
        fig = Figure(figsize=(8, 2.8))
        ax = fig.add_subplot(111)
        ax.plot(s.index, s["actual"], lw=0.6, label="actual", color="#0b4f6c")
        ax.plot(s.index, s["baseline_fit"], lw=0.6, label="baseline (rank-1 model fit)", color="#e4572e")
        ax.legend(fontsize=7)
        ax.set_title("Actual vs baseline (rank-1 model, in-sample fit)")
        ch["fit"] = _png(fig)
        res = (s["actual"] - s["baseline_oof"]).dropna()
        fig = Figure(figsize=(8, 2.6))
        a1, a2 = fig.add_subplot(121), fig.add_subplot(122)
        a1.hist(res, bins=40, color="#146c94")
        a1.set_title("Out-of-fold residuals")
        a2.scatter(s["baseline_oof"].dropna(), res, s=3, alpha=0.4)
        a2.axhline(0, color="k", lw=0.5)
        a2.set_title("Residual vs predicted")
        ch["resid"] = _png(fig)
        imp = (pick.get("importance") or {}).get("permutation")
        if imp:
            top = list(imp.items())[:10][::-1]
            fig = Figure(figsize=(8, 2.8))
            ax = fig.add_subplot(111)
            ax.barh([k for k, _ in top], [v for _, v in top], color="#146c94")
            ax.set_title(f"Permutation importance (RMSE increase) - {pick['name']}")
            ch["importance"] = _png(fig)
    return ch


def _compare_chart(run: RunResult) -> bytes | None:
    ok = [(t, r.shortlist[0]) for t, r in run.targets.items() if r.status == "ok" and r.shortlist]
    if len(ok) < 2:
        return None
    fig = Figure(figsize=(8, 2.8))
    ax = fig.add_subplot(111)
    ax.bar([t for t, _ in ok], [s["metrics"]["cvrmse"] for _, s in ok], color="#146c94")
    ax.set_ylabel("CV(RMSE) %")
    ax.set_title("Best-model CV(RMSE) per target")
    return _png(fig)


# ------------------------------------------------------------------ helpers
def _tbl(df: pd.DataFrame, max_rows: int = 200) -> str:
    return df.head(max_rows).to_html(index=False, border=0, classes="t", float_format=lambda v: f"{v:.4g}", na_rep="")


def interpret_term(term: str, coef: float, target: str) -> str:
    d = "increases" if coef > 0 else "decreases"
    if term == "(intercept)":
        return f"Base level of {target} when all drivers are zero / at reference level."
    if term.startswith("HDD"):
        return f"Heating sensitivity: {target} {d} by {abs(coef):.4g} per heating degree-unit below the balance point."
    if term.startswith("CDD"):
        return f"Cooling sensitivity: {target} {d} by {abs(coef):.4g} per cooling degree-unit above the balance point."
    if term.startswith("is_weekend"):
        return f"Weekend effect: {target} {d} by {abs(coef):.4g} on weekends relative to weekdays."
    if term.startswith(("sin_", "cos_")):
        return "Fourier harmonic coefficient describing a smooth daily / weekly / annual cycle."
    if term.startswith(("TOW[", "DOW[")):
        return f"Time-of-week level shift of {coef:+.4g} relative to the reference period."
    if term.startswith("max(0"):
        return f"Slope of a piecewise-linear segment: {target} {d} by {abs(coef):.4g} per unit beyond the change point."
    if term.startswith("ix:"):
        return "Interaction term (joint effect of two drivers)."
    if term.startswith("B["):
        return "B-spline basis coefficient (non-linear additive effect of the named driver)."
    return f"A one-unit increase of '{term}' {d} {target} by {abs(coef):.4g}, other terms constant."


def _pick_selected(run: RunResult, selections: list[dict], t: str) -> dict | None:
    for s in reversed(selections):
        if s["target"] == t and s["active"]:
            for sl in run.targets[t].shortlist:
                if sl["name"] == s["model"]:
                    return sl
    return None


def build_context(run: RunResult, selections: list[dict], audit: list[dict], data_dictionary: pd.DataFrame | None = None) -> tuple[dict, dict[str, bytes]]:
    charts_all: dict[str, bytes] = {}
    targets = []
    for name, tr in run.targets.items():
        sel = _pick_selected(run, selections, name)
        best = tr.shortlist[0] if tr.shortlist else None
        pick = sel or best
        ch = make_charts(tr, pick)
        cref = {}
        for k, v in ch.items():
            key = f"chart_{re.sub(r'[^A-Za-z0-9]', '_', name)}_{k}.png"
            charts_all[key] = v
            cref[k] = key
        d: dict = {"name": name, "status": tr.status, "error": tr.error, "gran": tr.granularity, "n_obs": tr.n_obs, "notice": tr.notice,
                   "best_name": best["name"] if best else "-", "best_score": best["score"] if best else None, "best_family": best["family"] if best else "-",
                   "cvrmse": f"{best['metrics']['cvrmse']:.2f}" if best else "-", "nmbe": f"{best['metrics']['nmbe']:+.2f}" if best else "-", "r2": f"{best['metrics']['r2']:.3f}" if best else "-",
                   "selected": sel["name"] if sel else "", "temporal": tr.temporal, "charts": cref}
        if tr.status == "ok":
            imp = (best.get("importance") or {}).get("permutation") if best else None
            agg = tr.temporal.get("aggregation") or {}
            lb = pd.DataFrame(tr.leaderboard)
            cols = ["rank", "model", "family", "score", "r2", "cvrmse", "nmbe", "mae", "mape", "smape", "n_params", "aic", "bic", "robustness", "qualified", "status"]
            d.update({
                "agg_text": ("; ".join(f"{c}: {m}" for c, m in list(agg.items())[:8]) if agg else "none (data already on the declared grid)"),
                "quality": tr.quality, "outliers": tr.outliers, "notes": tr.notes, "exclusions": tr.exclusions, "eda": tr.eda,
                "mining": [{**m, "params": json.dumps(m["params"], default=str), "results": json.dumps(m["results"], default=str)[:400]} for m in tr.mining],
                "feature_info": tr.feature_info, "selected_features": tr.selected_features, "required_columns": tr.required_columns,
                "leaderboard_html": _tbl(lb[[c for c in cols if c in lb]]), "scoring": tr.scoring, "thresholds": tr.thresholds,
                "weights_text": ", ".join(f"{k} {v:.2f}" for k, v in WEIGHTS[tr.scoring.get("profile", "balanced")].items()),
                "shortlist": tr.shortlist, "rationale_html": _tbl(pd.DataFrame(tr.rationale)), "comparative_html": _tbl(pd.DataFrame(tr.comparative)),
                "register": tr.register, "quality_score": tr.quality.get("score"), "n_qualified": int(sum(1 for r in tr.leaderboard if r["qualified"])), "n_models": len(tr.leaderboard),
                "drivers": ", ".join(list(imp)[:4]) if imp else "n/a", "rel_html": _tbl(tr.eda["relationships"]),
                "validation_text": f"{run.config.get('cv_scheme', 'AUTO')} scheme resolved in the AUTO log; thresholds ASHRAE 14 / IPMVP-based.",
                "sel": pick, "limitations": ([tr.notice] if tr.notice else []) + [n for n in tr.notes if n.startswith("WARNING")],
                "test_catalog": tr.test_catalog,
            })
            if pick:
                terms = pick["formula"].get("terms") or []
                rows = [{"term": "(intercept)", "coef": f"{pick['formula'].get('intercept', 0):.6g}", "text": interpret_term("(intercept)", 0, name)}] if pick["formula"].get("kind") == "equation" else []
                rows += [{"term": n, "coef": f"{c:.6g}", "text": interpret_term(n, c, name)} for n, c in sorted(terms, key=lambda x: -abs(x[1])) if abs(c) > 1e-12][:25]
                d["sel_terms"] = rows
        targets.append(d)
    cmp_png = _compare_chart(run)
    comp = None
    if cmp_png:
        charts_all["comparison.png"] = cmp_png
        comp = "comparison.png"
    paths = get_paths()
    st = db_status()
    ctx = {
        "created": run.created, "versions": run.versions, "run_id": run.run_id, "n_targets": len(run.targets), "dataset": run.dataset, "seed": run.seed,
        "targets": targets, "charts": {"comparison": comp}, "auto": run.auto, "selections": selections, "audit": audit[-300:],
        "data_dictionary": data_dictionary.round(4).fillna("").to_dict("records") if data_dictionary is not None else [],
        "config_json": json.dumps(run.config, indent=2, default=str),
        "storage": {"db_url": st.get("url"), "fallback": st.get("fallback"), "data": str(paths["data"]), "models": str(paths["models"]), "reports": str(paths["reports"]), "logs": str(paths["logs"])},
    }
    return ctx, charts_all


def render_html(run: RunResult, selections: list[dict], audit: list[dict], data_dictionary=None, embed: bool = True) -> tuple[str, dict[str, bytes]]:
    env = Environment(autoescape=select_autoescape(["html"], default=True))
    ctx, charts = build_context(run, selections, audit, data_dictionary)
    html = env.from_string(REPORT_TEMPLATE).render(**ctx)
    if embed:
        for name, png in charts.items():
            html = html.replace(f'src="{name}"', 'src="data:image/png;base64,' + base64.b64encode(png).decode() + '"')
    return html, charts


def html_to_pdf(html: str, charts: dict[str, bytes], path: Path) -> str:
    """Interim PDF fallback via Qt's native text engine (or matplotlib fallback); ReportLab lands in Phase 8."""
    try:
        from PySide6.QtCore import QMarginsF, QUrl
        from PySide6.QtGui import QGuiApplication, QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument
    except Exception as exc:
        log.info("Qt PDF writer unavailable (%s) -> using built-in matplotlib PDF writer", exc)
        _plain_pdf(html, charts, path)
        return "matplotlib"
    app = QGuiApplication.instance() or QGuiApplication([])
    _ = app
    doc = QTextDocument()
    for name, png in charts.items():
        img = QImage.fromData(png)
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), img)
    doc.setHtml(html)
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    w.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
    doc.print_(w)
    return "qt"


def _plain_pdf(html: str, charts: dict[str, bytes], path: Path) -> None:
    """Dependency-free last-resort PDF: text pages + embedded charts (matplotlib only)."""
    import textwrap
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.image import imread
    body = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    body = re.sub(r"(?i)</(tr|p|h1|h2|h3|h4|li|pre|div)>", "\n", body)
    body = re.sub(r"(?i)</t[dh]>", " | ", body)
    body = re.sub(r"(?s)<[^>]+>", "", body)
    import html as _h
    lines: list[str] = []
    for ln in _h.unescape(body).splitlines():
        ln = ln.strip()
        if ln:
            lines += textwrap.wrap(ln, 130) or [""]
    per = 78
    with PdfPages(str(path)) as pdf:
        for i in range(0, len(lines), per):
            fig = Figure(figsize=(8.27, 11.69))
            FigureCanvasAgg(fig)
            fig.text(0.04, 0.98, "\n".join(lines[i:i + per]), va="top", family="monospace", fontsize=5.6)
            pdf.savefig(fig)
        for name, png in charts.items():
            fig = Figure(figsize=(8.27, 4))
            FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            ax.imshow(imread(io.BytesIO(png)))
            ax.axis("off")
            ax.set_title(name, fontsize=7)
            pdf.savefig(fig)


def export_excel(run: RunResult, selections: list[dict], audit: list[dict], path: Path) -> None:
    def nm(s: str) -> str:
        return re.sub(r"[\[\]:*?/\\]", "_", s)[:31]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        rows = [{"target": t, "status": r.status, "granularity": r.granularity, "n_obs": r.n_obs, "best_model": r.shortlist[0]["name"] if r.shortlist else "",
                 "cvrmse_%": r.shortlist[0]["metrics"]["cvrmse"] if r.shortlist else None, "nmbe_%": r.shortlist[0]["metrics"]["nmbe"] if r.shortlist else None,
                 "r2": r.shortlist[0]["metrics"]["r2"] if r.shortlist else None, "quality_score": r.quality.get("score"), "notice": r.notice, "error": r.error}
                for t, r in run.targets.items()]
        pd.DataFrame(rows).to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame([{"target": t, **{k: str(v) for k, v in tr.temporal.items()}} for t, tr in run.targets.items() if tr.temporal]).to_excel(xw, sheet_name="Temporal", index=False)
        for i, (t, tr) in enumerate(run.targets.items(), 1):
            if tr.status != "ok":
                continue
            k = f"{i}"
            pd.DataFrame(tr.leaderboard).to_excel(xw, sheet_name=nm(f"Leaderboard_{k}_{t}"), index=False)
            pd.DataFrame([{"rank": s["rank"], "model": s["name"], "family": s["family"], "score": s["score"], **s["metrics"], "lower95": s["uncertainty"]["lower"], "upper95": s["uncertainty"]["upper"],
                           "rationale": s["reason"], "formula": s["formula"]["text"], "artifact": s["artifact"]} for s in tr.shortlist]).to_excel(xw, sheet_name=nm(f"Top5_{k}_{t}"), index=False)
            full = [{"model": s["name"], "term": n, "coefficient": c} for s in tr.shortlist for n, c in (s["formula"].get("terms") or [])]
            if full:
                pd.DataFrame(full).to_excel(xw, sheet_name=nm(f"Coefficients_{k}"), index=False)
            pd.DataFrame(tr.register).to_excel(xw, sheet_name=nm(f"Rejected_{k}_{t}"), index=False)
            pd.DataFrame(tr.exclusions).to_excel(xw, sheet_name=nm(f"Exclusions_{k}"), index=False)
            pd.DataFrame(tr.rationale).to_excel(xw, sheet_name=nm(f"Rationale_{k}"), index=False)
            pd.DataFrame(tr.comparative).to_excel(xw, sheet_name=nm(f"Diagnostics_{k}"), index=False)
            if tr.test_catalog:
                pd.DataFrame(
                    [
                        {
                            "test_id": tc["test_id"],
                            "stage": tc["stage"],
                            "name": tc["name"],
                            "subtest": tc["subtest"],
                            "statistic": tc["statistic"],
                            "p_raw": tc["p_raw"],
                            "p_adj": tc["p_adj"],
                            "effect_size": tc["effect_size"],
                            "ci_low": (tc.get("ci") or {}).get("low"),
                            "ci_high": (tc.get("ci") or {}).get("high"),
                            "ci_method": (tc.get("ci") or {}).get("method"),
                            "inference_label": tc.get("inference_label"),
                            "decision": tc["decision"],
                            "downstream_action": tc["downstream_action"],
                        }
                        for tc in tr.test_catalog
                    ]
                ).to_excel(xw, sheet_name=nm(f"TestCatalog_{k}_{t}"), index=False)
            tr.eda["relationships"].to_excel(xw, sheet_name=nm(f"Relationships_{k}"), index=False)
            pd.DataFrame([{"method": m["method"], "status": m["status"], "params": json.dumps(m["params"], default=str), "results": json.dumps(m["results"], default=str),
                           "interpretation": m["interpretation"], "downstream": m["downstream"]} for m in tr.mining]).to_excel(xw, sheet_name=nm(f"Mining_{k}"), index=False)
        pd.DataFrame(run.auto).to_excel(xw, sheet_name="AUTO_Resolutions", index=False)
        pd.DataFrame(selections).to_excel(xw, sheet_name="User_Selections", index=False)
        pd.DataFrame([{"component": k, "version": v} for k, v in run.versions.items()] + [{"component": "seed", "version": run.seed}, {"component": "config", "version": json.dumps(run.config, default=str)}]).to_excel(xw, sheet_name="Reproducibility", index=False)
        pd.DataFrame(audit).to_excel(xw, sheet_name="Audit_Log", index=False)


def generate_all(run: RunResult, selections: list[dict], audit: list[dict], data_dictionary=None, formats=("pdf", "xlsx", "html"), outdir: Path | None = None) -> dict[str, str]:
    outdir = outdir or (ensure_dirs()["reports"] / f"run_{run.run_id}")
    outdir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    html, charts = render_html(run, selections, audit, data_dictionary, embed=True)
    if "html" in formats:
        p = outdir / "energy_baseline_report.html"
        p.write_text(html, encoding="utf-8")
        out["html"] = str(p)
    if "pdf" in formats:
        html_names, _ = render_html(run, selections, audit, data_dictionary, embed=False)
        p = outdir / "energy_baseline_report.pdf"
        out["pdf_engine"] = html_to_pdf(html_names, charts, p)
        out["pdf"] = str(p)
    if "xlsx" in formats:
        p = outdir / "energy_baseline_report.xlsx"
        export_excel(run, selections, audit, p)
        out["xlsx"] = str(p)
    return out


# ------------------------------------------------------------------ dashboard exports
def dashboard_figure(results: dict[str, pd.DataFrame], thresholds: dict[str, dict] | None = None) -> Figure:
    n = max(1, len(results))
    fig = Figure(figsize=(5.2 * n, 6.2))
    for i, (t, df) in enumerate(results.items()):
        a1 = fig.add_subplot(2, n, i + 1)
        a2 = fig.add_subplot(2, n, n + i + 1, sharex=a1)
        draw_dashboard_axes(a1, a2, t, df)
    fig.tight_layout()
    return fig


def draw_dashboard_axes(a1, a2, target: str, df: pd.DataFrame) -> None:
    a1.clear(); a2.clear()
    if df is None or df.empty:
        return
    a1.fill_between(df.index, df["lower"], df["upper"], color="#9ec5d8", alpha=0.35, label="95 % interval")
    a1.plot(df.index, df["baseline"], color="#e4572e", lw=1, label="baseline")
    if df["actual"].notna().any():
        a1.plot(df.index, df["actual"], color="#0b4f6c", lw=1, label="actual")
    a1.set_title(f"{target}: actual vs baseline", fontsize=9)
    a1.legend(fontsize=7)
    if df["actual"].notna().any():
        a2.bar(df.index, df["deviation"], color=np.where(df["exceedance"], "#b42318", "#7aa6c2"), width=(df.index[1] - df.index[0]) * 0.8 if len(df) > 1 else 0.8)
        a2.axhline(0, color="k", lw=0.6)
        a = df.attrs.get("alert", {})
        mode, val = a.get("mode"), a.get("value", 0)
        band = None
        if mode == "percent":
            bb = (df["baseline"].abs() * val / 100.0)
            a2.plot(df.index, bb, "--", color="#b42318", lw=0.8)
            a2.plot(df.index, -bb, "--", color="#b42318", lw=0.8)
        elif mode == "cvrmse_multiple":
            band = val * df.attrs.get("rmse", df["deviation"].std())
            a2.axhline(band, ls="--", color="#b42318", lw=0.8)
            a2.axhline(-band, ls="--", color="#b42318", lw=0.8)
        else:
            a2.plot(df.index, df["upper"] - df["baseline"], "--", color="#b42318", lw=0.8)
            a2.plot(df.index, df["lower"] - df["baseline"], "--", color="#b42318", lw=0.8)
    a2.set_title("Deviation (actual − baseline) with alert thresholds", fontsize=9)
    for a in (a1, a2):
        a.tick_params(labelsize=7)


def export_dashboard_pdf(results: dict[str, pd.DataFrame], kpi_by_target: dict[str, dict], path: str) -> None:
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(path) as pdf:
        fig = Figure(figsize=(8.27, 11.69))
        ax = fig.add_subplot(111)
        ax.axis("off")
        lines = ["Prediction & Monitoring Dashboard", ""]
        for t, k in kpi_by_target.items():
            lines.append(f"Target: {t}")
            if k.get("has_actual"):
                lines += [f"   Total deviation: {k['total_deviation']:.5g}  ({k['total_deviation_pct']:.2f} % of baseline)" if k["total_deviation_pct"] is not None else f"   Total deviation: {k['total_deviation']:.5g}",
                          f"   Mean deviation: {k['mean_deviation_pct']:.2f} %", f"   Exceedances: {k['exceedances']} of {k['rows']} ({k['exceedance_share_pct']:.1f} %)",
                          f"   Max deviation at {k['max_deviation_at']}: {k['max_deviation_value']:.5g}"]
            else:
                lines.append("   (no actual values supplied - baseline predictions only)")
            lines.append("")
        ax.text(0.02, 0.98, "\n".join(lines), va="top", family="monospace", fontsize=9)
        FigureCanvasAgg(fig)
        pdf.savefig(fig)
        fig2 = dashboard_figure(results)
        FigureCanvasAgg(fig2)
        pdf.savefig(fig2)
        for t, df in results.items():
            d = df.dropna(subset=["actual"]).copy()
            if d.empty:
                continue
            d["abs"] = d["deviation"].abs()
            top = d.sort_values("abs", ascending=False).head(25)[["baseline", "actual", "deviation", "deviation_pct", "alert"]].round(3)
            f3 = Figure(figsize=(8.27, 6))
            a = f3.add_subplot(111)
            a.axis("off")
            a.set_title(f"{t}: top deviating records", fontsize=10)
            tb = a.table(cellText=top.reset_index().astype(str).values, colLabels=["timestamp"] + list(top.columns), loc="upper center", cellLoc="left")
            tb.auto_set_font_size(False)
            tb.set_fontsize(6)
            FigureCanvasAgg(f3)
            pdf.savefig(f3)


def export_dashboard_excel(results: dict[str, pd.DataFrame], kpi_by_target: dict[str, dict], path: str) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame([{"target": t, **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in k_.items()}} for t, k_ in kpi_by_target.items()]).to_excel(xw, sheet_name="KPIs", index=False)
        for t, df in results.items():
            df.reset_index().rename(columns={"index": "timestamp"}).to_excel(xw, sheet_name=re.sub(r"[\[\]:*?/\\]", "_", f"Deviation_{t}")[:31], index=False)
