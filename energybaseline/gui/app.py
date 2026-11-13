"""GUI bootstrap: single-instance guard -> DB init -> Qt MainWindow -> deterministic clean shutdown."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from ..config import ensure_dirs, get_resource_dir, setup_logging

log = logging.getLogger("energybaseline.app")


def _parse_cli_options(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(prog="energybaseline", add_help=False)
    parser.add_argument("--instance-key", default=None)
    parser.add_argument("--auto-close-ms", type=int, default=None)
    parser.add_argument("--telemetry-out", default=None)
    parser.add_argument("--spawn-worker-seconds", type=float, default=None)
    parser.add_argument("--ignore-cancel", action="store_true")
    return parser.parse_known_args(argv[1:])


def perform_clean_shutdown(
    *,
    single_instance: Any = None,
    join_timeout: float = 5.0,
) -> dict[str, Any]:
    """Deterministic P1.2 shutdown order: cancel -> join (5 s) -> hard stop -> WAL checkpoint -> DB close."""
    from ..core.pool import shutdown_default_pool
    from ..db.session import checkpoint_and_close

    pool_receipt = shutdown_default_pool(join_timeout=join_timeout)
    db_receipt = checkpoint_and_close()
    if single_instance is not None:
        try:
            single_instance.close()
        except Exception:
            pass
    receipt = {"pool": pool_receipt, "db": db_receipt}
    log.info("app.clean_shutdown %s", json.dumps(receipt))
    return receipt


def main(argv: list[str] | None = None) -> int:
    t_boot = time.perf_counter()
    raw_argv = list(sys.argv if argv is None else [sys.argv[0], *argv])
    opts, qt_rest = _parse_cli_options(raw_argv)

    from ..config.runtime_env import configure_qt_runtime_env, is_interactive_ui

    configure_qt_runtime_env(enable_highdpi=True)
    ensure_dirs()
    setup_logging()

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication([raw_argv[0], *qt_rest])
    app.setApplicationName("EnergyBaseline")
    ico = get_resource_dir() / "assets" / "energybaseline.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))

    # P1.3 Single-instance guard: notify primary window and exit 0 on second launch.
    from .single_instance import SingleInstanceGuard

    guard = SingleInstanceGuard(server_name=opts.instance_key)
    if not guard.try_acquire():
        if opts.telemetry_out:
            out_p = Path(opts.telemetry_out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(
                json.dumps(
                    {
                        "role": "secondary",
                        "activated_primary": True,
                        "pid": os.getpid(),
                        "elapsed_ms": round((time.perf_counter() - t_boot) * 1000.0, 2),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        return 0

    from ..db.session import init_database

    boot = init_database()  # never raises; traverses fallback ladder
    log.info("boot self-test: %s", boot)

    from .theme import apply_theme

    apply_theme(app, "light")

    def hook(et, ev, tb):
        txt = "".join(traceback.format_exception(et, ev, tb))
        log.error("unhandled exception:\n%s", txt)
        if is_interactive_ui() and opts.auto_close_ms is None:
            try:
                QMessageBox.critical(
                    None,
                    "Unexpected error",
                    f"{ev}\n\nDetails were written to logs/energybaseline.log. The application keeps running.",
                )
            except Exception:
                pass

    sys.excepthook = hook

    from .main_window import MainWindow
    from .state import AppState

    win = MainWindow(AppState(), boot)

    def _activate_primary_window() -> None:
        win.setWindowState((win.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        win.showNormal()
        win.raise_()
        win.activateWindow()

    guard.activation_requested.connect(_activate_primary_window)

    shutdown_done = False
    shutdown_receipt: dict[str, Any] = {}

    def _on_about_to_quit() -> None:
        nonlocal shutdown_done, shutdown_receipt
        if shutdown_done:
            return
        shutdown_done = True
        shutdown_receipt = perform_clean_shutdown(single_instance=guard, join_timeout=5.0)

    app.aboutToQuit.connect(_on_about_to_quit)
    win.show()

    telemetry: dict[str, Any] = {
        "role": "primary",
        "pid": os.getpid(),
        "db": boot,
        "first_paint_ms": None,
        "dpi_scale": None,
        "window_geometry": None,
        "spawned_worker_pids": [],
    }

    def _after_first_paint() -> None:
        telemetry["first_paint_ms"] = round((time.perf_counter() - t_boot) * 1000.0, 2)
        scr = win.screen() or app.primaryScreen()
        if scr is not None:
            telemetry["dpi_scale"] = float(scr.devicePixelRatio())
            avail = scr.availableGeometry()
            # Ensure outer frame fits available desktop work area across high-DPI scales
            frame = win.frameGeometry()
            client = win.geometry()
            extra_w = max(0, frame.width() - client.width())
            extra_h = max(0, frame.height() - client.height())
            max_client_w = max(240, avail.width() - extra_w)
            max_client_h = max(200, avail.height() - extra_h)
            if client.width() > max_client_w or client.height() > max_client_h:
                win.setMinimumSize(min(win.minimumWidth(), max_client_w), min(win.minimumHeight(), max_client_h))
                win.resize(min(client.width(), max_client_w), min(client.height(), max_client_h))
                app.processEvents()
            frame = win.frameGeometry()
            client = win.geometry()
            telemetry["window_geometry"] = {
                "width": client.width(),
                "height": client.height(),
                "frame_width": frame.width(),
                "frame_height": frame.height(),
                "screen_width": avail.width(),
                "screen_height": avail.height(),
                "fits_width": client.width() <= avail.width(),
                "fits_height": client.height() <= avail.height(),
            }

        if opts.spawn_worker_seconds is not None and opts.spawn_worker_seconds > 0:
            from ..core.pool import get_pool_manager

            mgr = get_pool_manager()
            h = mgr.submit_command(
                "worker.sleep_probe",
                {
                    "seconds": float(opts.spawn_worker_seconds),
                    "ignore_cancel": bool(opts.ignore_cancel),
                },
            )
            deadline = time.monotonic() + 8.0
            while h.worker_pid is None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.05)
            snap = mgr.status_snapshot()
            telemetry["spawned_worker_pids"] = snap["alive_worker_pids"]
            telemetry["job_object_active"] = snap["job_object_active"]
            telemetry["job_object_assigned_pids"] = snap["job_object_assigned_pids"]

        if opts.telemetry_out:
            out_p = Path(opts.telemetry_out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(telemetry, indent=2), encoding="utf-8")

        if opts.auto_close_ms is not None:
            QTimer.singleShot(max(0, int(opts.auto_close_ms)), app.quit)

    QTimer.singleShot(0, _after_first_paint)
    rc = int(app.exec())
    _on_about_to_quit()

    if opts.telemetry_out:
        telemetry["shutdown"] = shutdown_receipt
        Path(opts.telemetry_out).write_text(json.dumps(telemetry, indent=2), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
