"""Phase 0 GUI smoke test (D0.9): create and cleanly close a QApplication."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication


def test_qapplication_smoke() -> None:
    created = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    if sys.platform == "win32":
        assert app.platformName().lower() == "windows"
    app.processEvents()
    app.closeAllWindows()
    if created:
        app.quit()
