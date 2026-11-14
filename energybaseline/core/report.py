"""Compatibility boundary: convert analysis state once, then use JSON-only report renderers."""

from __future__ import annotations
from pathlib import Path
import pandas as pd
import numpy as np
from matplotlib.figure import Figure
from .report_adapter import model_from_analysis, assemble_report, json_value
from ..report import render_pdf, render_excel, export_companions
from ..report.html import render_html as render_model_html
from ..report.provenance import provenance_manifest
from ..config import ensure_dirs


def render_html(run, selections, audit, data_dictionary=None, embed=True):
    model = model_from_analysis(run, selections, audit, data_dictionary)
    return render_model_html(model, depth="full"), {}


def export_excel(run, selections, audit, path):
    render_excel(model_from_analysis(run, selections, audit), path, depth="full")


def generate_all(run, selections, audit, data_dictionary=None, formats=("pdf", "xlsx", "html"), outdir=None):
    model = model_from_analysis(run, selections, audit, data_dictionary)
    directory = Path(outdir) if outdir else ensure_dirs()["reports"] / f"run_{run.run_id}"
    directory.mkdir(parents=True, exist_ok=True)
    output = export_companions(model, directory)
    if "html" in formats:
        path = directory / "energy_baseline_report.html"
        path.write_text(render_model_html(model, depth="full"), encoding="utf-8")
        output["html"] = str(path)
    if "xlsx" in formats:
        path = directory / "energy_baseline_report.xlsx"
        render_excel(model, path, depth="full")
        output["xlsx"] = str(path)
    if "pdf" in formats:
        path = directory / "energy_baseline_report.pdf"
        render_pdf(model, path, depth="full")
        output.update(pdf=str(path), pdf_engine="reportlab")
    return output


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
    a1.clear()
    a2.clear()
    if df is None or df.empty:
        return
    a1.fill_between(df.index, df["lower"], df["upper"], color="#9ec5d8", alpha=0.35, label="95 % interval")
    a1.plot(df.index, df["baseline"], color="#e4572e", lw=1, label="baseline")
    if df["actual"].notna().any():
        a1.plot(df.index, df["actual"], color="#0b4f6c", lw=1, label="actual")
    a1.set_title(f"{target}: actual vs baseline", fontsize=9)
    a1.legend(fontsize=7)
    if df["actual"].notna().any():
        a2.bar(
            df.index,
            df["deviation"],
            color=np.where(df["exceedance"], "#b42318", "#7aa6c2"),
            width=(df.index[1] - df.index[0]) * 0.8 if len(df) > 1 else 0.8,
        )
        a2.axhline(0, color="k", lw=0.6)
        a = df.attrs.get("alert", {})
        mode, val = a.get("mode"), a.get("value", 0)
        band = None
        if mode == "percent":
            bb = df["baseline"].abs() * val / 100.0
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


def _dashboard_model(results, kpis):
    import hashlib
    from ..report.provenance import canonical

    state = {
        "targets": [
            {
                "name": name,
                "series": json_value(frame.rename(columns={"baseline": "predicted"})),
                "monitoring": [kpis.get(name, {})],
                "n_obs": len(frame),
            }
            for name, frame in results.items()
        ]
    }
    digest = hashlib.sha256(canonical(state).encode()).hexdigest()
    return assemble_report(state, provenance=provenance_manifest(digest, {}))


def export_dashboard_pdf(results, kpi_by_target, path):
    render_pdf(_dashboard_model(results, kpi_by_target), path, depth="standard")


def export_dashboard_excel(results, kpi_by_target, path):
    render_excel(_dashboard_model(results, kpi_by_target), path, depth="full")
