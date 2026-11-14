"""ReportLab Platypus renderer with invariant metadata and linked page-aware TOC."""

from __future__ import annotations
from functools import partial
from html import escape
import io
from pathlib import Path
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.platypus.tableofcontents import TableOfContents
from .depth import Depth, selected_sections, visible_rows
from .template import FIELDS
from .banner import disclosure_banner
from .charts import render_chart
from .html import cell_text


class ReportDocument(BaseDocTemplate):
    def __init__(self, stream, *, executive=False):
        super().__init__(
            stream, pagesize=(595.28, 841.89), leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40, invariant=1
        )
        self.addPageTemplates(
            PageTemplate(id="report", frames=[Frame(40, 40, 515, 762, id="body")], onPage=self.page_header)
        )
        self.executive = executive
        self.page_count = 0

    def page_header(self, canvas, doc):
        canvas.bookmarkPage("contents") if doc.page == 1 else None
        canvas.setFont("Helvetica", 8)
        canvas.drawString(40, 20, "EnergyBaseline | " + str(doc.page))
        canvas.linkAbsolute("Back to contents", "contents", (400, 15, 555, 30), thickness=0)

    def afterFlowable(self, flowable):
        if hasattr(flowable, "section_id"):
            key = flowable.section_id
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.getPlainText(), key, 0)
            self.notify("TOCEntry", (0, flowable.getPlainText(), self.page, key))

    def afterPage(self):
        self.page_count = max(self.page_count, self.page)
        if self.executive and self.page > 10:
            raise ValueError("Executive report exceeds page budget; reduce summary content")


def render_pdf(model, path, *, depth="standard"):
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    body.fontSize = 8
    body.leading = 10
    story = [Paragraph(escape(model.data["title"]), styles["Title"]), Paragraph("Contents", styles["Heading1"])]
    toc = TableOfContents()
    toc.levelStyles = [styles["BodyText"]]
    story.extend([toc, Spacer(1, 12)])
    for d in disclosure_banner(model):
        story.append(Paragraph(escape(d["category"] + ": " + "; ".join(d["details"])), body))
    story.append(Paragraph("Key Metrics", styles["Heading2"]))
    metric_rows = [["Artifact ID", "Value", "Metric", "Unit"]] + [
        ["[artifact:" + m["artifact_id"] + "]", cell_text(m["value"]), m["label"], m["unit"]] for m in model.metrics
    ]

    def table(rows, widths=None):
        if not rows:
            return
        wrapped = [[Paragraph(escape(cell_text(v)), body) for v in row] for row in rows]
        t = Table(
            wrapped,
            colWidths=widths or [510 / max(1, len(rows[0]))] * len(rows[0]),
            repeatRows=1,
            hAlign="LEFT",
            splitInRow=1,
        )
        t.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDDDDD")),
                ]
            )
        )
        story.extend([t, Spacer(1, 8)])

    table(metric_rows, [155, 170, 120, 65])
    for s in selected_sections(model, depth):
        heading = Paragraph(escape(s["title"]), styles["Heading1"])
        heading.section_id = s["id"]
        story.append(heading)
        for field in FIELDS:
            story.append(Paragraph("<b>" + field + "</b>: " + escape(s["template"][field]), body))
        for sentence in s["sentences"]:
            story.append(Paragraph(escape(sentence), body))
        for aid in s["tables"]:
            rows = visible_rows(model.table(aid), depth)
            cols = list(dict.fromkeys(k for r in rows for k in r))
            if cols:
                # Nested cells remain complete in JSON/Excel; wrap long tokens to fit a PDF page.
                table([cols] + [[r.get(c) for c in cols] for r in rows])
        for cid in s["charts"]:
            png = render_chart(model, cid)
            size = ImageReader(io.BytesIO(png)).getSize()
            w = 490
            h = w * size[1] / size[0]
            story.extend(
                [Image(io.BytesIO(png), width=w, height=h), Paragraph(escape(model.data["charts"][cid]["alt"]), body)]
            )
    doc = ReportDocument(stream, executive=Depth(depth) == Depth.EXECUTIVE)
    doc.multiBuild(story, canvasmaker=partial(Canvas, invariant=1, pageCompression=0, initialFontName="Helvetica"))
    Path(path).write_bytes(stream.getvalue())
    return {"engine": "reportlab", "pages": doc.page_count, "report_id": model.report_id}
