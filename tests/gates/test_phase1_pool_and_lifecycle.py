"""Phase 1 process-pool transport, cancellation tokens, >1 MiB payload ref, and clean shutdown tests (P1.1, P1.2)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energybaseline.core.pool import (
    PROTOCOL_VERSION,
    AnalysisPoolManager,
    CancelToken,
    UnknownWorkerCommandError,
    compute_max_workers,
    pack_payload,
    run_in_pool,
    unpack_payload,
)
from energybaseline.gui.app import perform_clean_shutdown


def test_compute_max_workers_formula() -> None:
    assert compute_max_workers(tasks=1, cpu_count=8) == 1
    assert compute_max_workers(tasks=3, cpu_count=8) == 3
    assert compute_max_workers(tasks=10, cpu_count=8) == 4  # CPU // 2
    assert compute_max_workers(tasks=2, cpu_count=1) == 1  # floor of 1


def test_payload_over_1mib_uses_cache_file_ref_and_checks_sha256(tmp_path: Path) -> None:
    cdir = tmp_path / "cache"
    n = 40_000
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=n, freq="min"),
            "kwh": np.linspace(100.0, 250.0, n),
            "temp": np.sin(np.linspace(0.0, 50.0, n)) * 15.0 + 18.0,
            "prod": np.cos(np.linspace(0.0, 20.0, n)) * 50.0 + 200.0,
        }
    )
    env = pack_payload(df, cdir)
    assert env["__eb_ref__"] is True
    assert set(("path", "sha256", "rows")) <= set(env.keys())
    assert env["rows"] == n
    assert Path(env["path"]).resolve().parent == cdir.resolve()

    restored = unpack_payload(env, cdir)
    assert len(restored) == n
    assert list(restored.columns) == ["ts", "kwh", "temp", "prod"]

    # Tampering 1 byte in cached file must fail SHA-256 verification
    fpath = Path(env["path"])
    data = bytearray(fpath.read_bytes())
    data[10] ^= 0x5A
    fpath.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        unpack_payload(env, cdir)


def test_cancel_token_callbacks_raise_interrupted_error(tmp_path: Path) -> None:
    cfile = tmp_path / "cancel.flag"
    tok = CancelToken(cancel_file=cfile, poll_interval_s=0.0)
    tok.stage_callback("Elec", 10.0, "ok")
    tok.fold_callback(0)
    tok.test_callback("adf")
    tok.epoch_callback(1, 0.25)

    cfile.write_text("cancel\n", encoding="utf-8")
    with pytest.raises(InterruptedError, match="stage"):
        tok.stage_callback("Elec", 20.0, "should stop")
    with pytest.raises(InterruptedError, match="fold"):
        tok.fold_callback(1)
    with pytest.raises(InterruptedError, match="test"):
        tok.test_callback("kpss")
    with pytest.raises(InterruptedError, match="epoch"):
        tok.epoch_callback(2, 0.1)
    with pytest.raises(InterruptedError, match="trial"):
        tok.trial_callback(None, None)


def test_pool_handshake_shl5_worker_db_guard_and_hard_kill(tmp_path: Path) -> None:
    mgr = AnalysisPoolManager(max_tasks=2, cache_dir=tmp_path / "cache", cancel_grace_seconds=0.6)
    try:
        hs = mgr.handshake()
        assert hs["ok"] is True
        assert hs["protocol_version"] == PROTOCOL_VERSION
        assert hs["in_worker_guard"] is True

        if sys.platform == "win32":
            snap = mgr.status_snapshot()
            assert snap["job_object_active"] is True
            assert len(snap["job_object_assigned_pids"]) >= 1

        # Protocol mismatch rejected
        h_bad_ver = mgr.submit_command("worker.ping", {"protocol_version": "999.0"})
        with pytest.raises(RuntimeError, match="Protocol mismatch"):
            h_bad_ver.wait(timeout=10.0)

        # Unknown command rejected by supervisor
        with pytest.raises(UnknownWorkerCommandError):
            mgr.submit_command("worker.arbitrary_exec", {})

        # SHL-5: Worker attempting to open SQLite engine is blocked immediately
        h_db = mgr.submit_command("worker.sleep_probe", {"seconds": 0.05, "try_open_db": True})
        with pytest.raises(RuntimeError, match="SHL-5"):
            h_db.wait(timeout=10.0)

        # Cooperative cancel stops worker promptly
        h_coop = mgr.submit_command("worker.sleep_probe", {"seconds": 30.0, "ignore_cancel": False})
        time.sleep(0.15)
        h_coop.cancel()
        assert h_coop.done_event.wait(timeout=5.0) is True
        assert h_coop.status == "cancelled"
        assert h_coop.hard_killed is False

        # Uncooperative worker ignoring cancel is hard-killed after grace window (0.6 s)
        h_ignore = mgr.submit_command(
            "worker.sleep_probe",
            {"seconds": 60.0, "ignore_cancel": True},
            cancel_grace_seconds=0.6,
        )
        time.sleep(0.2)
        h_ignore.cancel()
        assert h_ignore.done_event.wait(timeout=8.0) is True
        assert h_ignore.status == "cancelled"
        assert h_ignore.hard_killed is True
    finally:
        receipt = mgr.shutdown_cleanly(join_timeout=3.0)
        assert receipt["orphan_pids"] == []


def test_run_in_pool_executes_analysis_end_to_end_and_clean_shutdown(tmp_path: Path) -> None:
    rng = np.random.default_rng(42)
    n = 90
    temp = rng.normal(18.0, 6.0, size=n)
    kwh = 120.0 + 3.5 * np.maximum(0.0, 18.0 - temp) + rng.normal(0.0, 1.5, size=n)
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="D"),
            "Energy_kWh": kwh,
            "Temp_C": temp,
        }
    )

    events: list[tuple[str, float, str]] = []
    # Force >1 MiB path with low threshold_bytes=256 so file-backed transport is exercised end-to-end!
    run, bundles = run_in_pool(
        {
            "df": df,
            "time_col": "timestamp",
            "targets": ["Energy_kWh"],
            "config": {"nn_enabled": "false", "cv_folds": "3", "hpo_trials": "2"},
            "dataset_sha": "f" * 64,
            "dataset_info": {"name": "synth_daily.csv", "rows": n},
            "cache_dir": str(tmp_path / "pool_cache"),
        },
        progress=lambda t, p, m: events.append((t, p, m)),
        threshold_bytes=256,
    )

    assert run.cancelled is False
    assert "Energy_kWh" in run.targets
    assert run.targets["Energy_kWh"].status == "ok"
    assert len(events) >= 3
    assert "Energy_kWh" in bundles

    shutdown_receipt = perform_clean_shutdown(join_timeout=3.0)
    assert shutdown_receipt["pool"]["orphan_pids"] == []
    assert shutdown_receipt["db"]["disposed"] in (True, False)
