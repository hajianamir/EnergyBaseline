"""JSON-only in-app report preview: linked content, Agg snapshots and QtAgg charts."""

from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTableView,
    QTextBrowser,
    QComboBox,
    QFileDialog,
    QPushButton,
    QMessageBox,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from ..report.depth import selected_sections
from ..report.charts import figure_for, render_chart
from ..report.html import render_html
from ..report.archive import export_archive


class ReportPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.report_model = None
        layout = QVBoxLayout(self)
        self.depth = QComboBox()
        self.depth.addItems(["executive", "standard", "full"])
        layout.addWidget(self.depth)
        self.metrics = QTableView()
        self.metric_model = QStandardItemModel(self)
        self.metrics.setModel(self.metric_model)
        layout.addWidget(self.metrics)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        layout.addWidget(self.browser)
        self.chart_choice = QComboBox()
        layout.addWidget(self.chart_choice)
        self.chart_host = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_host)
        layout.addWidget(self.chart_host)
        self.archive_button = QPushButton("Export audit archive")
        layout.addWidget(self.archive_button)
        self.archive_button.clicked.connect(self.choose_archive)
        self.depth.currentTextChanged.connect(self.refresh)
        self.chart_choice.currentTextChanged.connect(self.show_chart)

    def set_report(self, model):
        self.report_model = model
        self.refresh()

    def refresh(self, *args):
        if self.report_model is None:
            return
        self.metric_model.clear()
        self.metric_model.setHorizontalHeaderLabels(["Artifact ID", "Metric", "Value", "Unit"])
        for metric in self.report_model.metrics:
            row = []
            for key in ("artifact_id", "label", "value", "unit"):
                value = metric[key]
                item = QStandardItem("Unavailable" if value is None else str(value))
                item.setData(value, Qt.ItemDataRole.UserRole)
                item.setEditable(False)
                row.append(item)
            self.metric_model.appendRow(row)
        self.browser.setHtml(render_html(self.report_model, depth=self.depth.currentText()))
        selected = {cid for s in selected_sections(self.report_model, self.depth.currentText()) for cid in s["charts"]}
        self.chart_choice.blockSignals(True)
        self.chart_choice.clear()
        self.chart_choice.addItems(sorted(selected))
        self.chart_choice.blockSignals(False)
        self.show_chart(self.chart_choice.currentText())

    def show_chart(self, chart_id):
        while self.chart_layout.count():
            item = self.chart_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.report_model is not None and chart_id:
            canvas = FigureCanvasQTAgg(figure_for(self.report_model, chart_id))
            self.chart_layout.addWidget(NavigationToolbar2QT(canvas, self))
            self.chart_layout.addWidget(canvas)
            canvas.draw_idle()

    def metric_values(self):
        return {
            self.metric_model.item(r, 0).data(Qt.ItemDataRole.UserRole): self.metric_model.item(r, 2).data(
                Qt.ItemDataRole.UserRole
            )
            for r in range(self.metric_model.rowCount())
        }

    def save_static_chart(self, chart_id, path):
        Path(path).write_bytes(render_chart(self.report_model, chart_id))

    def choose_archive(self):
        if self.report_model is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export audit archive", "audit.zip", "Audit ZIP (*.zip)")
        if path:
            try:
                export_archive(self.report_model, path, depth="full")
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Audit export", str(exc))
