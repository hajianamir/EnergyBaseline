"""openpyxl workbook and exact machine-readable CSV/JSON companions."""

from __future__ import annotations
import csv
from datetime import datetime
import io
from pathlib import Path
import re
import zipfile
from openpyxl import Workbook
from .depth import selected_sections, visible_rows
from .html import cell_text
from .safety import excel_literal
from .banner import disclosure_banner


def table_projection(model):
    return {k: v["value"] for k, v in model.data["artifacts"].items() if v["kind"] == "table"}


def render_excel(model, path, *, depth="standard"):
    wb = Workbook()
    toc = wb.active
    toc.title = "Contents"
    wb.properties.created = wb.properties.modified = datetime(2000, 1, 1)
    metrics = wb.create_sheet("Metrics")
    metrics.append(["artifact_id", "label", "value", "unit"])
    for m in model.metrics:
        metrics.append([m["artifact_id"], m["label"], m["value"], m["unit"]])
        for cell in metrics[metrics.max_row]:
            excel_literal(cell, cell.value)
    data = model.data
    used = set(wb.sheetnames)

    def sheet(name, rows):
        title = re.sub(r"[\[\]:*?/\\]", "_", name)[:31] or "Table"
        suffix = 1
        base = title
        while title in used:
            suffix += 1
            title = base[:26] + f"_{suffix}"
        used.add(title)
        ws = wb.create_sheet(title)
        cols = list(dict.fromkeys(k for row in rows for k in row))
        ws.append(cols or ["Evidence unavailable"])
        for row in rows:
            ws.append(
                [
                    row.get(c)
                    if isinstance(row.get(c), (int, float, bool)) or row.get(c) is None
                    else cell_text(row[c])
                    for c in cols
                ]
            )
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str) and len(cell.value) > 32767:
                    raise ValueError("Excel cell exceeds storage limit; use JSON companion")
                excel_literal(cell, cell.value)
        return ws

    toc.append(["Section", "Link"])
    for section in selected_sections(model, depth):
        records = [{"Field": k, "Content": v} for k, v in section["template"].items()]
        records += [{"Field": "Narrative", "Content": s} for s in section["sentences"]]
        ws = sheet(section["title"], records)
        toc.append([section["title"], "Open section"])
        toc.cell(toc.max_row, 2).hyperlink = f"#'{ws.title}'!A1"
        ws.cell(1, len(ws[1]) + 1, "Contents").hyperlink = "#'Contents'!A1"
        for aid in section["tables"]:
            record = data["artifacts"][aid]
            sheet(record["source"], visible_rows(record["value"], depth))
    sheet("Disclosures", disclosure_banner(model))
    buffer = io.BytesIO()
    wb.save(buffer)
    wb.close()
    # Canonical ZIP metadata prevents wall-clock entry timestamps in companion workbooks.
    with (
        zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as src,
        zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as dst,
    ):
        for name in sorted(src.namelist()):
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(info, src.read(name))


def export_companions(model, directory):
    from .provenance import canonical

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    model.save(directory / "report-model.json")
    (directory / "tables.json").write_text(canonical(table_projection(model)) + "\n", encoding="utf-8")
    with (directory / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["artifact_id", "label", "value", "unit"])
        for metric in model.metrics:

            def safe_csv(v):
                text = "" if v is None else str(v)
                return "'" + text if isinstance(v, str) and text.startswith(("=", "+", "-", "@", "\t", "\r")) else text

            writer.writerow([safe_csv(metric[k]) for k in ("artifact_id", "label", "value", "unit")])
    return {
        "model": str(directory / "report-model.json"),
        "tables": str(directory / "tables.json"),
        "csv": str(directory / "metrics.csv"),
    }
