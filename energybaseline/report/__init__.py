"""Shared report state, offline renderers and audit verifier."""

from .model import ReportModel
from .pdf import render_pdf
from .html import render_html
from .excel import render_excel, export_companions
from .archive import export_archive, verify_archive

__all__ = [
    "ReportModel",
    "render_pdf",
    "render_html",
    "render_excel",
    "export_companions",
    "export_archive",
    "verify_archive",
]
