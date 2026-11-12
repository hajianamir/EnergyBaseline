"""Status page: DB self-test, paths, versions, audit log."""
from __future__ import annotations

import json

import pandas as pd
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from ..config import get_paths
from ..core import store
from ..core.pipeline import collect_versions
from ..core.secrets import backend_name
from ..db.session import db_status, self_test
from .widgets import fill_table, make_table


class StatusPage(QWidget):
    def __init__(self, state, main, boot_selftest: dict):
        super().__init__()
        self.main, self.boot = main, boot_selftest
        lay = QVBoxLayout(self)
        self.info = QTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(300)
        lay.addWidget(self.info)
        h = QHBoxLayout()
        b = QPushButton("Re-run database self-test")
        b.clicked.connect(self.refresh)
        h.addWidget(b)
        h.addWidget(QLabel("Audit log (latest entries):"))
        h.addStretch(1)
        lay.addLayout(h)
        self.audit = make_table(False)
        lay.addWidget(self.audit, 1)
        self.refresh()

    def refresh(self):
        st = self_test()
        db = db_status()
        paths = get_paths()
        v = collect_versions()
        html = (f"<h3>Status</h3><p><b>Database self-test:</b> <code>{json.dumps({k: st[k] for k in ('ok', 'url', 'fallback', 'writable')})}</code> "
                f"{'✔' if st['ok'] else '✖ ' + str(st.get('error', ''))}</p>"
                f"<p>Resolved URL: <code>{db.get('url')}</code> · SQLite fallback used: <b>{db.get('fallback')}</b> · external DB failed at boot: {db.get('external_failed')}</p>"
                f"<p><b>Where is my data stored?</b><br>" + "<br>".join(f"{k}: <code>{p}</code>" for k, p in paths.items()) + "</p>"
                f"<p>Credential encryption backend: {backend_name()}<br>Neural-network device: {v['nn_device']}<br>"
                f"Versions: " + ", ".join(f"{k} {x}" for k, x in v.items() if k not in ('platform', 'nn_device')) + "</p>")
        self.info.setHtml(html)
        fill_table(self.audit, pd.DataFrame(store.audit_entries(None, 300)[::-1]))
