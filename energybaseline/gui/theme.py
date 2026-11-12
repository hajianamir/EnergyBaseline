"""Light / dark themes (Qt Fusion + palette + small stylesheet)."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_QSS = """
QToolTip { padding: 4px; }
QGroupBox { font-weight: 600; border: 1px solid %(border)s; border-radius: 6px; margin-top: 10px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton { padding: 5px 12px; border-radius: 4px; border: 1px solid %(border)s; }
QPushButton:hover { background: %(hover)s; }
QPushButton#primary { background: %(accent)s; color: white; border: none; font-weight: 600; }
QPushButton#primary:disabled { background: %(border)s; color: #888; }
QFrame#kpi { border: 1px solid %(border)s; border-radius: 8px; background: %(card)s; }
QLabel#kpiValue { font-size: 20px; font-weight: 700; color: %(accent)s; }
QLabel#kpiTitle { color: %(muted)s; font-size: 11px; }
QLabel#banner { background: #fff3cd; color: #5c4400; border: 1px solid #e0b100; padding: 6px; border-radius: 4px; }
QHeaderView::section { padding: 4px; font-weight: 600; }
"""


def apply_theme(app: QApplication, name: str = "light") -> None:
    app.setStyle("Fusion")
    p = QPalette()
    if name == "dark":
        base, win, text, accent, border, hover, card, muted = "#1e232a", "#2a3039", "#e6eaf0", "#3aa0d8", "#46505e", "#394150", "#252b33", "#9aa5b4"
        p.setColor(QPalette.ColorRole.Window, QColor(win))
        p.setColor(QPalette.ColorRole.WindowText, QColor(text))
        p.setColor(QPalette.ColorRole.Base, QColor(base))
        p.setColor(QPalette.ColorRole.AlternateBase, QColor(win))
        p.setColor(QPalette.ColorRole.Text, QColor(text))
        p.setColor(QPalette.ColorRole.Button, QColor(win))
        p.setColor(QPalette.ColorRole.ButtonText, QColor(text))
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(base))
        p.setColor(QPalette.ColorRole.ToolTipText, QColor(text))
        p.setColor(QPalette.ColorRole.Highlight, QColor(accent))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        app.setPalette(p)
    else:
        base, win, text, accent, border, hover, card, muted = "#ffffff", "#f3f5f8", "#1b2430", "#0b6e99", "#c9d3dc", "#e8eef4", "#ffffff", "#667085"
        app.setPalette(app.style().standardPalette())
    app.setStyleSheet(_QSS % dict(border=border, hover=hover, accent=accent, card=card, muted=muted))
    app.setProperty("eb_theme", name)
