"""Live Status page (P1.7): DB status & fallback rung, pool/in-flight state, cache dir, last run artifacts, orphans."""

from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import get_base_dir, get_paths
from ..core import store
from ..core.pool import get_pool_manager
from ..core.secrets import backend_name
from ..db.session import db_status, self_test
from .widgets import fill_table, make_table


def _fast_versions() -> dict[str, str]:
    out = {"python": sys.version.split()[0], "nn_device": "cpu"}
    if "torch" in sys.modules:
        tmod = sys.modules["torch"]
        if getattr(tmod, "cuda", None) and tmod.cuda.is_available():
            out["nn_device"] = "cuda"
    for dist_name, label in (
        ("numpy", "numpy"),
        ("pandas", "pandas"),
        ("scipy", "scipy"),
        ("scikit-learn", "sklearn"),
        ("PySide6", "PySide6"),
        ("SQLAlchemy", "sqlalchemy"),
        ("torch", "torch"),
    ):
        try:
            out[label] = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return out


def _fmt_bytes(num_bytes: int) -> str:
    val = float(max(0, int(num_bytes)))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if val < 1024.0 or unit == "GiB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{int(num_bytes)} B"


class StatusPage(QWidget):
    """Live Phase 1 operational status view replacing the Phase 0 static text stub."""

    def __init__(self, state, main=None, boot_selftest: dict | None = None):
        super().__init__()
        self.state = state
        self.main = main
        self.boot = boot_selftest or {}

        lay = QVBoxLayout(self)
        hdr = QHBoxLayout()
        self.lbl_health = QLabel()
        hdr.addWidget(self.lbl_health, 1)
        self.btn_refresh = QPushButton("Re-run database self-test & refresh status")
        self.btn_refresh.clicked.connect(lambda: self.refresh(run_selftest=True))
        hdr.addWidget(self.btn_refresh)
        lay.addLayout(hdr)

        grid = QGridLayout()

        # Group 1: Database & Fallback Ladder
        db_box = QGroupBox("Database & Fallback Rung")
        db_lay = QVBoxLayout(db_box)
        self.lbl_db_rung = QLabel()
        self.lbl_db_url = QLabel()
        self.lbl_db_url.setWordWrap(True)
        self.lbl_db_pragmas = QLabel()
        self.lbl_db_pragmas.setWordWrap(True)
        self.lbl_db_backup = QLabel()
        self.lbl_db_backup.setWordWrap(True)
        for w in (self.lbl_db_rung, self.lbl_db_url, self.lbl_db_pragmas, self.lbl_db_backup):
            db_lay.addWidget(w)
        grid.addWidget(db_box, 0, 0)

        # Group 2: Process Pool, In-Flight Jobs & Orphan Health
        pool_box = QGroupBox("Worker Process Pool & Lifecycle Health")
        pool_lay = QVBoxLayout(pool_box)
        self.lbl_pool_state = QLabel()
        self.lbl_inflight = QLabel()
        self.lbl_inflight.setWordWrap(True)
        self.lbl_job_object = QLabel()
        self.lbl_job_object.setWordWrap(True)
        self.lbl_orphans = QLabel()
        for w in (self.lbl_pool_state, self.lbl_inflight, self.lbl_job_object, self.lbl_orphans):
            pool_lay.addWidget(w)
        grid.addWidget(pool_box, 0, 1)

        lay.addLayout(grid)

        # Group 3: Cache Directory, Runtime Environment & Last Run Artifacts
        art_box = QGroupBox("Cache Directory & Last Run Artifact Paths")
        art_lay = QVBoxLayout(art_box)
        self.lbl_paths = QLabel()
        self.lbl_paths.setWordWrap(True)
        self.lbl_last_run = QLabel()
        self.lbl_last_run.setWordWrap(True)
        self.artifact_list = QListWidget()
        self.artifact_list.setMaximumHeight(120)
        self.lbl_env = QLabel()
        self.lbl_env.setWordWrap(True)
        for w in (self.lbl_paths, self.lbl_last_run, self.artifact_list, self.lbl_env):
            art_lay.addWidget(w)
        lay.addWidget(art_box)

        lay.addWidget(QLabel("<b>Audit log (latest entries):</b>"))
        self.audit = make_table(False)
        lay.addWidget(self.audit, 1)

        self._last_selftest: dict[str, Any] = dict(self.boot)
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._tick)
        self.refresh(run_selftest=not bool(self._last_selftest))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.refresh(run_selftest=False)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event: Any) -> None:
        if self._timer.isActive():
            self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        if self.isVisible():
            self.refresh(run_selftest=False)

    def _collect_last_run_artifacts(self) -> tuple[int | None, list[str]]:
        rid: int | None = getattr(self.state, "run_id", None) if self.state is not None else None
        if rid is None:
            runs = store.list_runs()
            if runs:
                rid = int(runs[0]["id"])
        if rid is None:
            return None, []

        paths = get_paths()
        artifacts: list[str] = []
        for root_dir in (paths["runs"] / f"run_{rid}", paths["models"] / f"run_{rid}"):
            if root_dir.is_dir():
                for f in sorted(root_dir.rglob("*")):
                    if f.is_file():
                        artifacts.append(str(f.resolve()))
        return rid, artifacts

    def snapshot(self, *, run_selftest: bool = False) -> dict[str, Any]:
        """Structured live status dictionary consumed by both the UI widgets and automated tests."""
        if run_selftest or not self._last_selftest:
            self._last_selftest = self_test()
        st = self._last_selftest
        dbs = db_status()
        ps = get_pool_manager().status_snapshot()
        paths = get_paths()
        last_run_id, artifact_paths = self._collect_last_run_artifacts()

        healthy = bool(st.get("ok")) and dbs.get("banner_level") != "critical" and ps.get("orphan_count", 0) == 0
        return {
            "healthy": healthy,
            "selftest": dict(st),
            "db": {
                "rung": dbs.get("rung", "unknown"),
                "url": dbs.get("url", ""),
                "masked_url": dbs.get("masked_url") or dbs.get("url", ""),
                "fallback": bool(dbs.get("fallback", False)),
                "external_failed": bool(dbs.get("external_failed", False)),
                "banner_level": dbs.get("banner_level", "none"),
                "banner_text": dbs.get("banner_text", ""),
                "pragmas": dict(dbs.get("pragmas") or {}),
                "last_backup": dbs.get("last_backup"),
                "migration_restored": bool(dbs.get("migration_restored", False)),
            },
            "pool": ps,
            "storage": {
                "app_home": str(get_base_dir().resolve()),
                "data_dir": str(paths["data"]),
                "cache_dir": ps["cache_dir"],
                "cache_file_count": ps["cache_file_count"],
                "cache_size_bytes": ps["cache_size_bytes"],
                "last_run_id": last_run_id,
                "last_run_artifacts": artifact_paths,
            },
        }

    def refresh(self, *, run_selftest: bool = True) -> dict[str, Any]:
        snap = self.snapshot(run_selftest=run_selftest)
        st = snap["selftest"]
        dbs = snap["db"]
        ps = snap["pool"]
        storage = snap["storage"]
        v = _fast_versions()

        health_word = (
            "CRITICAL (In-Memory / Orphans)"
            if (dbs["banner_level"] == "critical" or ps["orphan_count"] > 0)
            else ("DEGRADED (Fallback Active)" if dbs["banner_level"] == "warning" else "HEALTHY")
        )
        self.lbl_health.setText(
            f"<b>Overall Health:</b> {health_word}  \u00b7  DB Self-Test: "
            f"<code>{json.dumps({k: st.get(k) for k in ('ok', 'rung', 'writable', 'fallback')})}</code>"
        )

        # DB Card
        self.lbl_db_rung.setText(
            f"Active Rung: {dbs['rung']}  |  Fallback Used: {dbs['fallback']}  |  Banner: {dbs['banner_level']}"
        )
        self.lbl_db_url.setText(f"Engine URL: {dbs['masked_url']}")
        if dbs["pragmas"]:
            ptxt = ", ".join(f"{k}={val}" for k, val in dbs["pragmas"].items())
            self.lbl_db_pragmas.setText(f"SQLite PRAGMAs: {ptxt}")
        else:
            self.lbl_db_pragmas.setText("SQLite PRAGMAs: N/A (external engine)")

        bkp = dbs.get("last_backup")
        if isinstance(bkp, dict) and bkp.get("backup_db"):
            self.lbl_db_backup.setText(
                f"Pre-Migration Backup: {Path(bkp['backup_db']).name} (sha256:{str(bkp.get('sha256', ''))[:12]}\u2026)"
            )
        else:
            self.lbl_db_backup.setText("Pre-Migration Backup: none recorded in this session")

        # Pool Card
        self.lbl_pool_state.setText(
            f"Pool State: {ps['state']}  |  Max Workers: {ps['max_workers']}  |  Protocol: v{ps['protocol_version']}"
        )
        if ps["active_jobs"]:
            jdesc = ", ".join(f"{j['job_id'][:8]} ({j['status']}, pid={j['worker_pid']})" for j in ps["active_jobs"])
            self.lbl_inflight.setText(f"In-Flight Jobs ({len(ps['active_jobs'])}): {jdesc}")
        else:
            self.lbl_inflight.setText("In-Flight Jobs: idle (0 active)")

        if ps["job_object_supported"]:
            jo_lbl = "ACTIVE (KILL_ON_JOB_CLOSE)" if ps["job_object_active"] else "inactive"
            self.lbl_job_object.setText(
                f"Windows Job Object: {jo_lbl}  |  Assigned Worker PIDs: {ps['job_object_assigned_pids']}"
            )
        else:
            self.lbl_job_object.setText(f"Process Tracking (POSIX spawn): alive worker PIDs {ps['alive_worker_pids']}")
        self.lbl_orphans.setText(
            f"Orphan Worker Processes: {ps['orphan_count']}  ({'OK' if ps['orphan_count'] == 0 else 'ALERT'})"
        )

        # Storage & Artifacts Card
        self.lbl_paths.setText(
            f"APP_HOME: {storage['app_home']}  |  Cache Directory: {storage['cache_dir']} "
            f"({storage['cache_file_count']} files, {_fmt_bytes(storage['cache_size_bytes'])})"
        )
        self.artifact_list.clear()
        if storage["last_run_id"] is not None:
            self.lbl_last_run.setText(
                f"Last Run: #{storage['last_run_id']} ({len(storage['last_run_artifacts'])} artifact files)"
            )
            for apath in storage["last_run_artifacts"]:
                self.artifact_list.addItem(apath)
        else:
            self.lbl_last_run.setText("Last Run: no stored runs yet")
            self.artifact_list.addItem("(No run artifacts generated yet)")

        self.lbl_env.setText(
            f"Credential backend: {backend_name()}  \u00b7  NN device: {v['nn_device']}  \u00b7  "
            + ", ".join(f"{k} {x}" for k, x in v.items() if k not in ("platform", "nn_device"))
        )
        fill_table(self.audit, pd.DataFrame(store.audit_entries(None, 300)[::-1]))
        return snap
