"""Phase 0 GUI smoke test (D0.9): create and verify a QApplication via pytest-qt."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication


def test_qapplication_smoke(qapp: QApplication) -> None:
    assert isinstance(qapp, QApplication)
    if sys.platform == "win32":
        assert qapp.platformName().lower() == "windows"
    qapp.processEvents()
    qapp.closeAllWindows()
