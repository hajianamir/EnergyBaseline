"""Single-file offline HTML; inert escaped content and embedded chart assets only."""

from __future__ import annotations
import base64
from html import escape
from .depth import selected_sections, visible_rows
from .banner import disclosure_banner
from .charts import render_chart
from .provenance import canonical
from .template import FIELDS


def cell_text(value):
    return canonical(value) if isinstance(value, (dict, list)) else "Unavailable" if value is None else str(value)


def render_html(model, *, depth="standard"):
    sections = selected_sections(model, depth)
    out = [
        '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'">',
        "<style>body{font:14px sans-serif;color:#222;margin:2em}table{border-collapse:collapse}td,th{border:1px solid #888;padding:.3em}img{max-width:100%}.banner{border:2px solid #D55E00;padding:1em}</style></head><body>",
        "<h1>" + escape(model.data["title"]) + '</h1><nav id="contents"><h2>Contents</h2>',
    ]
    out.extend(f'<a href="#{s["id"]}">{escape(s["title"])}</a><br>' for s in sections)
    out.append('</nav><div class="banner">')
    for item in disclosure_banner(model):
        out.append(escape(item["category"] + ": " + "; ".join(item["details"])) + "<br>")
    out.append(
        "</div><h2>Key Metrics</h2><table><tr><th>Artifact ID</th><th>Metric</th><th>Value</th><th>Unit</th></tr>"
    )
    for m in model.metrics:
        out.append(
            "<tr>"
            + "".join("<td>" + escape(cell_text(m[k])) + "</td>" for k in ("artifact_id", "label", "value", "unit"))
            + "</tr>"
        )
    out.append("</table>")
    for section in sections:
        out.append(
            f'<section id="{section["id"]}"><h2>{escape(section["title"])}</h2><a href="#contents">Back to contents</a>'
        )
        for key in FIELDS:
            out.append("<h3>" + key + "</h3><p>" + escape(section["template"][key]) + "</p>")
        for sentence in section["sentences"]:
            out.append("<p>" + escape(sentence) + "</p>")
        for aid in section["tables"]:
            rows = model.table(aid)
            cols = list(dict.fromkeys(k for r in rows for k in r))
            out.append(
                "<h4>"
                + escape(aid)
                + "</h4><table><tr>"
                + "".join("<th>" + escape(c) + "</th>" for c in cols)
                + "</tr>"
            )
            for row in visible_rows(rows, depth):
                out.append("<tr>" + "".join("<td>" + escape(cell_text(row.get(c))) + "</td>" for c in cols) + "</tr>")
            out.append("</table>")
        for cid in section["charts"]:
            chart = model.data["charts"][cid]
            out.append(
                '<figure><img alt="'
                + escape(chart["alt"], quote=True)
                + '" src="data:image/png;base64,'
                + base64.b64encode(render_chart(model, cid)).decode()
                + '"><figcaption>'
                + escape(chart["alt"])
                + "</figcaption></figure>"
            )
        out.append("</section>")
    out.append("</body></html>")
    return "".join(out)
