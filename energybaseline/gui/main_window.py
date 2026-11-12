"""Main window: tabbed MDI area + fallback banner + static command allowlist + dockable panels."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMdiArea,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import get_resource_dir
from ..core import store
from ..db.session import db_status
from .advanced import AdvancedDialog, AutoPanel
from .commands import CommandRegistry
from .page_monitor import MonitorPage
from .page_status import StatusPage
from .pages_results import ResultsPage, SelectionPage
from .pages_setup import AnalysisPage, SetupPage
from .theme import apply_theme

log = logging.getLogger("energybaseline.gui")


class _Emitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.emitter = _Emitter()

    def emit(self, record):
        try:
            self.emitter.message.emit(self.format(record))
        except Exception:
            pass


class FallbackBanner(QFrame):
    """Top-of-window warning/critical banner reflecting active database fallback rung (P1.6, DB-3/8)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("fallbackBanner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 7, 14, 7)
        self.level = "none"
        self.label = QLabel("")
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.setVisible(False)

    def refresh_from_db(self) -> str:
        st = db_status()
        lvl = str(st.get("banner_level") or "none")
        txt = str(st.get("banner_text") or "")
        self.level = lvl
        if lvl == "critical" and txt:
            self.setStyleSheet(
                "QFrame#fallbackBanner { background-color: #991B1B; border-bottom: 2px solid #FCA5A5; }"
                "QFrame#fallbackBanner QLabel { color: #FFFFFF; font-weight: 700; }"
            )
            self.label.setText(txt)
            self.setVisible(True)
        elif lvl == "warning" and txt:
            self.setStyleSheet(
                "QFrame#fallbackBanner { background-color: #92400E; border-bottom: 2px solid #FCD34D; }"
                "QFrame#fallbackBanner QLabel { color: #FEF3C7; font-weight: 600; }"
            )
            self.label.setText(txt)
            self.setVisible(True)
        else:
            self.label.setText("")
            self.setVisible(False)
        return lvl


class MainWindow(QMainWindow):
    def __init__(self, state, boot_selftest: dict | None = None):
        super().__init__()
        boot_selftest = boot_selftest or {}
        self.state = state
        self.setWindowTitle(f"EnergyBaseline {__version__} - local-first energy baseline analytics")
        scr = QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else None
        init_w = min(1480, max(640, avail.width() - 20)) if avail is not None else 1480
        init_h = min(920, max(480, avail.height() - 20)) if avail is not None else 920
        self.setMinimumSize(min(640, init_w), min(480, init_h))
        self.resize(init_w, init_h)

        # Central container: fallback banner + MDI area
        central = QWidget(self)
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(0, 0, 0, 0)
        central_lay.setSpacing(0)

        self.banner = FallbackBanner(central)
        self.banner.refresh_from_db()
        central_lay.addWidget(self.banner)

        self.mdi = QMdiArea(central)
        self.mdi.setViewMode(QMdiArea.ViewMode.TabbedView)
        self.mdi.setDocumentMode(True)
        self.mdi.setTabsClosable(False)
        central_lay.addWidget(self.mdi, 1)
        self.setCentralWidget(central)

        # live log dock
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_dock = QDockWidget("Live log", self)
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        handler = QtLogHandler()
        handler.emitter.message.connect(self.log_view.appendPlainText)
        from ..config import setup_logging

        setup_logging(handler)
        self.auto_panel = AutoPanel()
        self.auto_dock = QDockWidget("AUTO Resolutions (read-only)", self)
        self.auto_dock.setWidget(self.auto_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.auto_dock)
        self.tabifyDockWidget(self.log_dock, self.auto_dock)
        self.log_dock.raise_()

        # pages
        self.setup_page = SetupPage(state, self)
        self.analysis_page = AnalysisPage(state, self)
        self.results_page = ResultsPage(state, self)
        self.selection_page = SelectionPage(state, self)
        self.monitor_page = MonitorPage(state, self)
        self.status_page = StatusPage(state, self, boot_selftest)
        self.subs = {}
        for key, title, w in (
            ("setup", "1 \u00b7 Data & Temporal Setup", self.setup_page),
            ("analysis", "2 \u00b7 Analysis", self.analysis_page),
            ("results", "3 \u00b7 Results", self.results_page),
            ("selection", "4 \u00b7 Baseline Selection (Top-5)", self.selection_page),
            ("monitor", "5 \u00b7 Prediction & Monitoring", self.monitor_page),
            ("status", "Status", self.status_page),
        ):
            sw = self.mdi.addSubWindow(w)
            sw.setWindowTitle(title)
            sw.setWindowFlags(Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
            self.subs[key] = sw
            sw.showMaximized()

        # Resolve and validate compile-time static command allowlist at startup (P1.5 / SCR-7).
        self.commands = CommandRegistry(
            {
                "nav.setup": lambda: self._activate_sub("setup"),
                "nav.analysis": lambda: self._activate_sub("analysis"),
                "nav.results": lambda: self._activate_sub("results"),
                "nav.selection": lambda: self._activate_sub("selection"),
                "nav.monitor": lambda: self._activate_sub("monitor"),
                "nav.status": lambda: self._activate_sub("status"),
                "file.open_dataset": lambda: self.setup_page.open_file(),
                "file.manual_entry": self.setup_page.manual_entry,
                "file.load_run": self.load_run,
                "file.exit": self.close,
                "analysis.run": self.analysis_page.start,
                "analysis.cancel": self.analysis_page.cancel,
                "advanced.settings": self.advanced,
                "advanced.show_auto_panel": lambda: (self.auto_dock.show(), self.auto_dock.raise_()),
                "view.theme_light": lambda: apply_theme(QApplication.instance(), "light"),
                "view.theme_dark": lambda: apply_theme(QApplication.instance(), "dark"),
                "view.show_log": lambda: (self.log_dock.show(), self.log_dock.raise_()),
                "status.refresh": lambda: self.status_page.refresh(run_selftest=True),
                "help.guide": self.guide,
                "help.about": self.about,
            }
        )

        self.show_page("setup")
        state.run_changed.connect(self._on_run)
        self._menus()
        self.statusBar().showMessage(
            f"Database self-test: {'OK' if boot_selftest.get('ok') else 'FAILED'} \u00b7 "
            f"Rung: {boot_selftest.get('rung', 'sqlite')} \u00b7 offline, local-first"
        )
        log.info("GUI started")

    # ------------------------------------------------------------------
    def log_msg(self, msg: str):
        log.info(msg)

    def _activate_sub(self, key: str) -> None:
        self.mdi.setActiveSubWindow(self.subs[key])
        if key == "status":
            self.banner.refresh_from_db()
            self.status_page.refresh(run_selftest=False)

    def show_page(self, key: str):
        cmd_id = f"nav.{key}"
        if self.commands.is_allowed(cmd_id):
            self.commands.dispatch(cmd_id)
        else:
            self._activate_sub(key)

    def _on_run(self):
        if self.state.run:
            self.auto_panel.set_records(self.state.run.auto)
        self.banner.refresh_from_db()
        self.status_page.refresh(run_selftest=False)

    def _menus(self):
        mb = self.menuBar()

        def act(menu, text, command_id: str, shortcut: str | None = None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(lambda _=False, cid=command_id: self.commands.dispatch(cid))
            menu.addAction(a)
            return a

        m = mb.addMenu("&File")
        act(m, "Open dataset\u2026", "file.open_dataset", "Ctrl+O")
        act(m, "Manual data entry\u2026", "file.manual_entry")
        act(m, "Load previous analysis run\u2026", "file.load_run")
        m.addSeparator()
        act(m, "Exit", "file.exit", "Ctrl+Q")

        m = mb.addMenu("&Analysis")
        act(m, "Run analysis", "analysis.run", "F5")
        act(m, "Cancel", "analysis.cancel")

        m = mb.addMenu("A&dvanced")
        act(m, "Settings (AUTO for every option)\u2026", "advanced.settings")
        act(m, "Show AUTO Resolutions panel", "advanced.show_auto_panel")

        m = mb.addMenu("&View")
        act(m, "Light theme", "view.theme_light")
        act(m, "Dark theme", "view.theme_dark")
        m.addSeparator()
        for key, title in (
            ("setup", "Setup"),
            ("analysis", "Analysis"),
            ("results", "Results"),
            ("selection", "Baseline Selection"),
            ("monitor", "Prediction & Monitoring"),
            ("status", "Status"),
        ):
            act(m, title, f"nav.{key}")
        act(m, "Live log", "view.show_log")

        m = mb.addMenu("&Help")
        act(m, "User guide", "help.guide")
        act(m, "About", "help.about")

    def advanced(self):
        AdvancedDialog(self.state, self).exec()
        if self.state.run:
            self.auto_panel.set_records(self.state.run.auto)

    def load_run(self):
        runs = [r for r in store.list_runs() if r["status"] in ("completed", "cancelled")]
        if not runs:
            QMessageBox.information(self, "Previous runs", "No completed runs are stored yet.")
            return
        labels = [f"#{r['id']} \u00b7 {r['started']} \u00b7 {', '.join(r['targets'])}" for r in runs]
        pick, ok = QInputDialog.getItem(self, "Load previous run", "Run:", labels, 0, False)
        if not ok:
            return
        r = runs[labels.index(pick)]
        try:
            run = store.load_run(r["id"])
        except Exception as exc:
            QMessageBox.critical(self, "Load run", f"Could not load the run: {exc}")
            return
        self.state.run, self.state.run_id, self.state.locked = run, r["id"], {}
        self.state.run_changed.emit()
        self.show_page("selection")

    def guide(self):
        p = get_resource_dir() / "docs" / "USER_GUIDE.md"
        if p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        else:
            QMessageBox.information(self, "User guide", "docs/USER_GUIDE.md was not found next to the application.")

    def about(self):
        QMessageBox.about(
            self,
            "About EnergyBaseline",
            f"<b>EnergyBaseline {__version__}</b><br>Offline, local-first desktop application for multi-target "
            "energy baseline analytics (Python \u00b7 PySide6 \u00b7 SQLite).<br>"
            "All data stay on this computer. External SQL is an optional input of the Prediction module only.",
        )
