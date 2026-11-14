"""Static Agg / interactive-compatible figures with audited units and palettes."""

from __future__ import annotations
import io
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib import rc_context
from scipy.special import ndtri

OKABE_ITO = ("#000000", "#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7")
KINDS = {
    "actual_predicted",
    "prediction_intervals",
    "residual_acf",
    "residual_qq",
    "cusum",
    "driver_response",
    "eda",
    "correlation",
}


def chart_spec(artifact_id, kind, *, unit, alt, title, n):
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "unit": unit or "unspecified unit",
        "alt": alt,
        "title": title,
        "n": n,
        "palette": list(OKABE_ITO),
        "interval_label": "Empirical prediction interval; see calibration evidence"
        if kind == "prediction_intervals"
        else "not applicable",
    }


def validate_chart(chart, artifacts):
    aid = chart["artifact_id"]
    if aid not in artifacts or artifacts[aid]["kind"] != "table" or chart["kind"] not in KINDS:
        raise ValueError("Invalid chart source or kind")
    if not chart["alt"].strip() or not chart["unit"].strip() or not chart["title"].strip():
        raise ValueError("Charts require alt text, units and title")
    if not chart["palette"] or not set(chart["palette"]) <= set(OKABE_ITO):
        raise ValueError("Unapproved chart palette")
    if chart["n"] != len(artifacts[aid]["value"]):
        raise ValueError("Chart sample size disagrees with source")


def figure_for(model, chart_id):
    spec = model.data["charts"][chart_id]
    rows = model.table(spec["artifact_id"])
    fig = Figure(figsize=(7, 2.7), layout="constrained")
    ax = fig.add_subplot(111)
    colors = spec["palette"]
    kind = spec["kind"]

    def values(key):
        return np.array([np.nan if r.get(key) is None else r[key] for r in rows], dtype=float)

    x = np.arange(len(rows))
    actual = values("actual")
    pred = values("predicted")
    res = values("residual")
    if kind in ("actual_predicted", "prediction_intervals"):
        ax.plot(x, actual, color=colors[5], label="Actual")
        ax.plot(x, pred, color=colors[6], label="Predicted")
        if kind == "prediction_intervals":
            ax.fill_between(
                x, values("lower"), values("upper"), color=colors[2], alpha=0.3, label="Calibrated interval"
            )
        ax.legend(fontsize=7)
        ax.set_xlabel("Observation")
    elif kind == "residual_acf":
        finite = res[np.isfinite(res)]
        finite = finite - finite.mean() if len(finite) else finite
        denom = float(finite @ finite)
        lags = np.arange(min(len(finite), 31))
        ac = [float(finite[k:] @ finite[: len(finite) - k] / denom) if denom > 0 else 0.0 for k in lags]
        ax.bar(lags, ac, color=colors[5])
        ax.set_xlabel("Lag (observations)")
    elif kind == "residual_qq":
        finite = np.sort(res[np.isfinite(res)])
        n = len(finite)
        ax.scatter(ndtri((np.arange(n) + 0.5) / max(n, 1)), finite, color=colors[5], s=9)
        ax.set_xlabel("Normal theoretical quantile")
    elif kind == "cusum":
        ax.plot(x, values("cusum"), color=colors[3])
        ax.set_xlabel("Observation")
    elif kind == "driver_response":
        ax.plot(values("driver"), values("response"), color=colors[6])
        ax.set_xlabel(rows[0].get("driver_unit", "unspecified unit") if rows else "Driver")
    elif kind == "eda":
        ax.hist(actual[np.isfinite(actual)], bins="auto", color=colors[5])
        ax.set_xlabel(spec["unit"])
    elif kind == "correlation":
        finite = np.isfinite(actual) & np.isfinite(pred)
        if finite.sum() > 1 and np.std(actual[finite]) > 0 and np.std(pred[finite]) > 0:
            matrix = np.corrcoef(actual[finite], pred[finite])
            ax.imshow(matrix, cmap="viridis", vmin=-1, vmax=1)
            ax.set_xticks([0, 1], ["Actual", "Predicted"])
            ax.set_yticks([0, 1], ["Actual", "Predicted"])
        else:
            ax.text(
                0.5, 0.5, "Correlation unavailable: insufficient paired variation", ha="center", transform=ax.transAxes
            )
        ax.set_xlabel("Persisted series")
    ax.set_title(f"{spec['title']} | n={spec['n']}", fontsize=9)
    ax.set_ylabel("Correlation / dimensionless" if kind in ("correlation", "residual_acf") else spec["unit"])
    return fig


def render_chart(model, chart_id, *, format="png"):
    if format not in ("png", "svg"):
        raise ValueError("Unsupported chart format")
    with rc_context({"svg.hashsalt": chart_id, "font.family": "DejaVu Sans"}):
        fig = figure_for(model, chart_id)
        FigureCanvasAgg(fig)
        stream = io.BytesIO()
        fig.savefig(
            stream,
            format=format,
            dpi=180,
            metadata={"Date": None} if format == "svg" else {"Software": "EnergyBaseline"},
        )
        fig.clear()
    return stream.getvalue()
