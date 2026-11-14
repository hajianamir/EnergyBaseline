"""Actual Qt table values agree with exported PDF/Excel facts; real QtAgg interaction."""

from PySide6.QtWidgets import QFileDialog
from energybaseline.gui.report_preview import ReportPreview
from energybaseline.report import render_pdf, render_excel, verify_archive
from energybaseline.report.agreement import pdf_metrics, excel_metrics, verify_metric_agreement
from tests.fixtures.phase8 import report_fixture


def test_three_renderer_agreement_twenty_metrics_and_one_click_archive(qapp, tmp_path, monkeypatch):
    model = report_fixture()
    preview = ReportPreview()
    preview.set_report(model)
    pdf = tmp_path / "report.pdf"
    xlsx = tmp_path / "report.xlsx"
    render_pdf(model, pdf)
    render_excel(model, xlsx)
    assert len(preview.metric_values()) == 20
    assert verify_metric_agreement(model, preview.metric_values(), pdf_metrics(pdf), excel_metrics(xlsx))
    preview.depth.setCurrentText("full")
    assert preview.chart_choice.count() > 0
    preview.show_chart(preview.chart_choice.currentText())
    static = tmp_path / "chart.png"
    preview.save_static_chart(preview.chart_choice.currentText(), static)
    assert static.read_bytes().startswith(b"\x89PNG")
    archive = tmp_path / "audit.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(archive), "Audit ZIP"))
    preview.archive_button.click()
    assert verify_archive(archive)["ok"]
    preview.close()
    preview.deleteLater()
