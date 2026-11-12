"""Main window: tabbed MDI area + dockable panels (live log, AUTO Resolutions), menus, themes."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QDockWidget, QInputDialog, QMainWindow, QMdiArea, QMessageBox, QPlainTextEdit, QApplication

from .. import __version__
from ..config import get_resource_dir
from ..core import store
from .advanced import AdvancedDialog, AutoPanel
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


class MainWindow(QMainWindow):
    def __init__(self, state, boot_selftest: dict):
        super().__init__()
        self.state = state
        self.setWindowTitle(f"EnergyBaseline {__version__} - local-first energy baseline analytics")
        self.resize(1480, 920)
        self.mdi = QMdiArea()
        self.mdi.setViewMode(QMdiArea.ViewMode.TabbedView)
        self.mdi.setDocumentMode(True)
        self.mdi.setTabsClosable(False)
        self.setCentralWidget(self.mdi)
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
        for key, title, w in (("setup", "1 · Data & Temporal Setup", self.setup_page), ("analysis", "2 · Analysis", self.analysis_page),
                              ("results", "3 · Results", self.results_page), ("selection", "4 · Baseline Selection (Top-5)", self.selection_page),
                              ("monitor", "5 · Prediction & Monitoring", self.monitor_page), ("status", "Status", self.status_page)):
            sw = self.mdi.addSubWindow(w)
            sw.setWindowTitle(title)
            sw.setWindowFlags(Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
            self.subs[key] = sw
            sw.showMaximized()
        self.show_page("setup")
        state.run_changed.connect(self._on_run)
        self._menus()
        self.statusBar().showMessage(f"Database self-test: {'OK' if boot_selftest.get('ok') else 'FAILED'} · SQLite fallback: {boot_selftest.get('fallback')} · offline, local-first")
        log.info("GUI started")

    # ------------------------------------------------------------------
    def log_msg(self, msg: str):
        log.info(msg)

    def show_page(self, key: str):
        self.mdi.setActiveSubWindow(self.subs[key])

    def _on_run(self):
        if self.state.run:
            self.auto_panel.set_records(self.state.run.auto)

    def _menus(self):
        mb = self.menuBar()

        def act(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(lambda _=False: fn())
            menu.addAction(a)
            return a
        m = mb.addMenu("&File")
        act(m, "Open dataset…", self.setup_page.open_file, "Ctrl+O")
        act(m, "Manual data entry…", self.setup_page.manual_entry)
        act(m, "Load previous analysis run…", self.load_run)
        m.addSeparator()
        act(m, "Exit", self.close, "Ctrl+Q")
        m = mb.addMenu("&Analysis")
        act(m, "Run analysis", self.analysis_page.start, "F5")
        act(m, "Cancel", self.analysis_page.cancel)
        m = mb.addMenu("A&dvanced")
        act(m, "Settings (AUTO for every option)…", self.advanced)
        act(m, "Show AUTO Resolutions panel", lambda: (self.auto_dock.show(), self.auto_dock.raise_()))
        m = mb.addMenu("&View")
        act(m, "Light theme", lambda: apply_theme(QApplication.instance(), "light"))
        act(m, "Dark theme", lambda: apply_theme(QApplication.instance(), "dark"))
        m.addSeparator()
        for key, title in (("setup", "Setup"), ("analysis", "Analysis"), ("results", "Results"), ("selection", "Baseline Selection"), ("monitor", "Prediction & Monitoring"), ("status", "Status")):
            act(m, title, lambda k=key: self.show_page(k))
        act(m, "Live log", lambda: (self.log_dock.show(), self.log_dock.raise_()))
        m = mb.addMenu("&Help")
        act(m, "User guide", self.guide)
        act(m, "About", self.about)

    def advanced(self):
        AdvancedDialog(self.state, self).exec()
        if self.state.run:
            self.auto_panel.set_records(self.state.run.auto)

    def load_run(self):
        runs = [r for r in store.list_runs() if r["status"] in ("completed", "cancelled")]
        if not runs:
            QMessageBox.information(self, "Previous runs", "No completed runs are stored yet.")
            return
        labels = [f"#{r['id']} · {r['started']} · {', '.join(r['targets'])}" for r in runs]
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
        QMessageBox.about(self, "About EnergyBaseline", f"<b>EnergyBaseline {__version__}</b><br>Offline, local-first desktop application for multi-target energy baseline "
                          "analytics (Python · PySide6 · SQLite).<br>All data stay on this computer. External SQL is an optional input of the Prediction module only.")
