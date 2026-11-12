"""GUI bootstrap: logging -> DB (migrations + self-test, never fatal) -> Qt application."""
from __future__ import annotations

import logging
import os
import sys
import traceback

from ..config import ensure_dirs, get_resource_dir, setup_logging

log = logging.getLogger("energybaseline.app")


def main() -> int:
    os.environ.setdefault("QT_API", "pyside6")
    ensure_dirs()
    setup_logging()
    from ..db.session import init_database
    boot = init_database()  # never raises; falls back to SQLite
    log.info("boot self-test: %s", boot)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("EnergyBaseline")
    ico = get_resource_dir() / "assets" / "energybaseline.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    from .theme import apply_theme
    apply_theme(app, "light")

    def hook(et, ev, tb):
        txt = "".join(traceback.format_exception(et, ev, tb))
        log.error("unhandled exception:\n%s", txt)
        try:
            QMessageBox.critical(None, "Unexpected error", f"{ev}\n\nDetails were written to logs/energybaseline.log. The application keeps running.")
        except Exception:
            pass
    sys.excepthook = hook
    from .main_window import MainWindow
    from .state import AppState
    win = MainWindow(AppState(), boot)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
