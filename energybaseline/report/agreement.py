"""Read actual exported PDF/workbook values rather than a renderer side channel."""

import re
from openpyxl import load_workbook
from pypdf import PdfReader


def pdf_metrics(source):
    text = "\n".join(page.extract_text() for page in PdfReader(source).pages)
    return {
        aid: None if value == "Unavailable" else float(value)
        for aid, value in re.findall(r"\[artifact:([A-Za-z][A-Za-z0-9_.-]*)\]\s*([^\s]+)", text)
        if value == "Unavailable" or re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", value)
    }


def excel_metrics(source):
    wb = load_workbook(source, data_only=True)
    try:
        return {row[0]: row[2] for row in wb["Metrics"].iter_rows(min_row=2, values_only=True)}
    finally:
        wb.close()


def verify_metric_agreement(model, *outputs):
    import math

    expected = {m["artifact_id"]: m["value"] for m in model.metrics}
    for output in outputs:
        if set(output) != set(expected):
            raise ValueError("Renderer metric membership mismatch")
        for aid, value in expected.items():
            actual = output[aid]
            if (actual is None) != (value is None) or (
                value is not None and not math.isclose(actual, value, rel_tol=1e-12, abs_tol=1e-12)
            ):
                raise ValueError("Renderer metric disagreement")
    return True


def html_metrics(source):
    from html import unescape

    text = source.decode("utf-8") if isinstance(source, bytes) else source
    matches = re.findall(r"<tr><td>(metric\.[^<]+)</td><td>[^<]*</td><td>([^<]+)</td>", text)
    return {unescape(aid): None if value == "Unavailable" else float(value) for aid, value in matches}


def csv_metrics(source):
    import csv
    import io

    text = source.decode("utf-8") if isinstance(source, bytes) else source
    return {r["artifact_id"]: None if not r["value"] else float(r["value"]) for r in csv.DictReader(io.StringIO(text))}
