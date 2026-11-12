"""External SQL connection manager (credentials stored encrypted)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import sqlsource as sq
from ..core.secrets import backend_name
from ..core import store


class SqlDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("External SQL connections (optional - the only feature that may use the network)")
        self.resize(900, 600)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.lst = QListWidget()
        self.lst.currentRowChanged.connect(self._select)
        left.addWidget(QLabel("Saved connections"))
        left.addWidget(self.lst, 1)
        nb = QPushButton("New connection")
        nb.clicked.connect(self._new)
        left.addWidget(nb)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit()
        self.driver = QComboBox()
        self.driver.addItems(sq.DRIVERS)
        self.driver.currentTextChanged.connect(self._driver_changed)
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText("default")
        self.db = QLineEdit()
        dbrow = QHBoxLayout()
        dbrow.addWidget(self.db, 1)
        br = QPushButton("…")
        br.clicked.connect(self._browse)
        dbrow.addWidget(br)
        self.user = QLineEdit()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssl = QCheckBox("Use SSL / TLS")
        self.ca = QLineEdit()
        self.ca.setPlaceholderText("optional CA certificate path")
        self.odbc_driver = QLineEdit("ODBC Driver 18 for SQL Server")
        self.odbc_str = QLineEdit()
        self.odbc_str.setPlaceholderText("generic ODBC: full connection string or DSN=...")
        form.addRow("Name", self.name)
        form.addRow("Driver", self.driver)
        form.addRow("Host", self.host)
        form.addRow("Port", self.port)
        w = QWidget()
        w.setLayout(dbrow)
        form.addRow("Database / file", w)
        form.addRow("User", self.user)
        form.addRow("Password", self.pw)
        form.addRow("", self.ssl)
        form.addRow("SSL CA", self.ca)
        form.addRow("ODBC driver (SQL Server)", self.odbc_driver)
        form.addRow("ODBC string (generic)", self.odbc_str)
        right.addLayout(form)
        right.addWidget(QLabel("Query templates (one per line: <b>name :: SELECT …</b>) - stored encrypted with the connection"))
        self.queries = QPlainTextEdit()
        self.queries.setMaximumHeight(110)
        right.addWidget(self.queries)
        self.msg = QLabel(f"Password storage: {backend_name()} - never plaintext.")
        self.msg.setWordWrap(True)
        right.addWidget(self.msg)
        row = QHBoxLayout()
        for text, fn in (("Test connection", self._test), ("Save", self._save), ("Delete", self._delete), ("Close", self.accept)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        right.addLayout(row)
        lay.addLayout(right, 2)
        self.profiles: list[sq.ConnectionProfile] = []
        self.reload()

    def reload(self, select: str | None = None):
        try:
            self.profiles = sq.load_profiles()
        except Exception as exc:
            self.profiles = []
            self.msg.setText(f"Could not load saved connections: {exc}")
        self.lst.clear()
        for p in self.profiles:
            self.lst.addItem(p.name)
        if select:
            for i, p in enumerate(self.profiles):
                if p.name == select:
                    self.lst.setCurrentRow(i)

    def _driver_changed(self, d):
        self.port.setValue(sq.DEFAULT_PORT.get(d) or 0)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "SQLite database file", "", "SQLite (*.db *.sqlite *.sqlite3);;All files (*)")
        if p:
            self.db.setText(p)

    def _new(self):
        self.lst.clearSelection()
        for w in (self.name, self.host, self.db, self.user, self.pw, self.ca, self.odbc_str, self.queries):
            w.clear()
        self.ssl.setChecked(False)

    def _select(self, i):
        if i < 0 or i >= len(self.profiles):
            return
        p = self.profiles[i]
        self.name.setText(p.name)
        self.driver.setCurrentText(p.driver)
        self.host.setText(p.host)
        self.port.setValue(p.port or 0)
        self.db.setText(p.database)
        self.user.setText(p.username)
        self.pw.setText(p.password)
        self.ssl.setChecked(p.ssl)
        self.ca.setText(p.ssl_ca)
        self.odbc_driver.setText(p.odbc_driver)
        self.odbc_str.setText(p.odbc_string)
        self.queries.setPlainText("\n".join(f"{k} :: {v}" for k, v in p.queries.items()))

    def current(self) -> sq.ConnectionProfile:
        qs = {}
        for ln in self.queries.toPlainText().splitlines():
            if "::" in ln:
                k, v = ln.split("::", 1)
                qs[k.strip()] = v.strip()
        return sq.ConnectionProfile(self.name.text().strip() or "unnamed", self.driver.currentText(), self.host.text().strip(), self.port.value() or None,
                                    self.db.text().strip(), self.user.text().strip(), self.pw.text(), self.ssl.isChecked(), self.ca.text().strip(),
                                    self.odbc_driver.text().strip(), self.odbc_str.text().strip(), qs)

    def _test(self):
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, msg = sq.test_connection(self.current())
        finally:
            self.unsetCursor()
        self.msg.setText(("✔ " if ok else "✖ ") + msg)

    def _save(self):
        p = self.current()
        try:
            for q in p.queries.values():
                sq.validate_select(q)
            sq.save_profile(p)
        except Exception as exc:
            QMessageBox.warning(self, "Save", str(exc))
            return
        store.audit(f"SQL connection saved: {p.name} ({p.driver})", "sql")
        self.reload(p.name)
        self.msg.setText("Saved (password and query templates encrypted).")

    def _delete(self):
        n = self.name.text().strip()
        if n:
            sq.delete_profile(n)
            self.reload()
            self._new()
