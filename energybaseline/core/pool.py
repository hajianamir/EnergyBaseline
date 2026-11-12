"""Process-pool transport adapter for ``run_analysis`` (P1.1, P1.2, R-1, SHL-5, IPC-1..7, LIF-1/2, SCR-7).

Key guarantees:
* ``ProcessPoolExecutor`` with ``mp_context="spawn"`` and ``max_workers=min(CPU//2, tasks)`` (min 1).
* Static compile-time command allowlist (``WORKER_COMMAND_REGISTRY``) — zero ``eval`` or ``getattr`` dispatch.
* Workers set ``_EB_IN_WORKER=1`` at init and never open SQLite (enforced in ``energybaseline.db.session``).
* Progress and results return over ``multiprocessing.Queue`` (inherited at spawn via ``initargs``; zero TCP ports).
* Payloads > 1 MiB travel as ``{path, sha256, rows}`` under ``APP_HOME/cache/`` and are SHA-256-verified on read.
* ``CancelToken`` checks cancellation at stage boundaries and inside epoch, trial, fold, and test callbacks.
* Uncooperative workers ignoring cancel past ``cancel_grace_seconds`` (default 10 s) are hard-killed.
* On Windows, every worker PID is assigned to a Win32 Job Object configured with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import multiprocessing as mp
import os
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import MappingProxyType
from typing import Any

import joblib

from ..config import get_base_dir
from ..io.paths import resolve_cache_dir, validate_path

log = logging.getLogger("energybaseline.pool")

PROTOCOL_VERSION = "1.0"
PAYLOAD_THRESHOLD_BYTES = 1_048_576  # 1 MiB
DEFAULT_CANCEL_GRACE_SECONDS = 10.0
FIRST_HANDSHAKE_TIMEOUT_SECONDS = 30.0
WARM_HANDSHAKE_TIMEOUT_SECONDS = 10.0


class UnknownWorkerCommandError(ValueError):
    """Raised when an unallowlisted command ID is submitted to the worker dispatcher."""


class PoolHandshakeError(TimeoutError):
    """Raised when the worker pool fails readiness or protocol-version verification."""


class WorkerCancelledError(InterruptedError):
    """Raised when an in-flight worker job is cancelled cooperatively or hard-killed."""


# ===================================================================== Windows Job Object (P1.2 / LIF-2)
class WindowsJobObject:
    """Win32 Job Object wrapper enforcing ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` on Windows."""

    def __init__(self) -> None:
        self.supported = sys.platform == "win32"
        self._hjob: Any = None
        self.assigned_pids: set[int] = set()
        self.error: str = ""
        if self.supported:
            self._create()

    def _create(self) -> None:
        try:
            import win32job

            name = f"Local\\EnergyBaselinePool_{os.getpid()}_{uuid.uuid4().hex[:8]}"
            hjob = win32job.CreateJobObject(None, name)
            ext_info = win32job.QueryInformationJobObject(hjob, win32job.JobObjectExtendedLimitInformation)
            ext_info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(hjob, win32job.JobObjectExtendedLimitInformation, ext_info)
            self._hjob = hjob
        except Exception as exc:  # pragma: no cover - defensive
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("pool.job_object_create_failed error=%s", self.error)

    @property
    def active(self) -> bool:
        return self._hjob is not None

    def assign_pid(self, pid: int) -> bool:
        if not self._hjob or pid <= 0 or pid in self.assigned_pids:
            return pid in self.assigned_pids
        try:
            import win32api
            import win32con
            import win32job

            access = win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE | 0x1000
            hproc = win32api.OpenProcess(access, False, int(pid))
            try:
                win32job.AssignProcessToJobObject(self._hjob, hproc)
            finally:
                win32api.CloseHandle(hproc)
            self.assigned_pids.add(int(pid))
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("pool.job_object_assign_failed pid=%s error=%s", pid, self.error)
            return False

    def active_process_count(self) -> int:
        if not self._hjob:
            return 0
        try:
            import win32job

            info = win32job.QueryInformationJobObject(self._hjob, win32job.JobObjectBasicAccountingInformation)
            return int(info.get("ActiveProcesses", 0))
        except Exception:
            return 0

    def close(self) -> None:
        if self._hjob is not None:
            try:
                import win32api

                win32api.CloseHandle(self._hjob)
            except Exception:
                pass
            self._hjob = None


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = ctypes.c_ulong(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return int(code.value) == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            # Check zombie state on Linux /proc
            stat_file = Path(f"/proc/{pid}/stat")
            if stat_file.is_file():
                parts = stat_file.read_text(encoding="utf-8", errors="ignore").rsplit(")", 1)
                if len(parts) == 2 and parts[1].strip().startswith("Z"):
                    return False
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


# ===================================================================== >1 MiB Cache Payload Helpers (P1.1)
def pack_payload(
    obj: Any,
    cache_dir: str | Path,
    *,
    prefix: str = "payload",
    rows: int | None = None,
    threshold_bytes: int = PAYLOAD_THRESHOLD_BYTES,
) -> dict[str, Any]:
    """Serialize ``obj``; if larger than ``threshold_bytes`` (1 MiB), store under ``cache_dir`` and return ref."""
    cdir = Path(cache_dir).resolve()
    cdir.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO()
    joblib.dump(obj, buf)
    raw = buf.getvalue()
    nbytes = len(raw)
    digest = hashlib.sha256(raw).hexdigest()
    row_count = int(rows) if rows is not None else (int(len(obj)) if hasattr(obj, "__len__") else 0)

    if nbytes > threshold_bytes:
        fname = f"{prefix}_{uuid.uuid4().hex}.joblib"
        fpath = validate_path(cdir / fname, allowed_roots=[cdir])
        tmp_path = validate_path(cdir / f"{fname}.tmp", allowed_roots=[cdir])
        tmp_path.write_bytes(raw)
        tmp_path.replace(fpath)
        return {
            "__eb_ref__": True,
            "path": str(fpath),
            "sha256": digest,
            "rows": row_count,
            "size_bytes": nbytes,
        }

    return {
        "__eb_ref__": False,
        "inline_bytes": raw,
        "sha256": digest,
        "rows": row_count,
        "size_bytes": nbytes,
    }


def unpack_payload(envelope: dict[str, Any], allowed_cache_dir: str | Path) -> Any:
    """Validate SHA-256 and deserialize an inline or file-backed payload reference."""
    cdir = Path(allowed_cache_dir).resolve()
    if envelope.get("__eb_ref__"):
        fpath = validate_path(envelope["path"], allowed_roots=[cdir], must_exist=True)
        raw = fpath.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != envelope["sha256"]:
            raise ValueError(f"Cached payload SHA-256 mismatch for {fpath.name}")
        obj = joblib.load(io.BytesIO(raw))
        expected_rows = int(envelope.get("rows", 0))
        if expected_rows and hasattr(obj, "__len__") and len(obj) != expected_rows:
            raise ValueError(f"Cached payload row count mismatch: expected {expected_rows}, got {len(obj)}")
        return obj

    raw = envelope["inline_bytes"]
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != envelope["sha256"]:
        raise ValueError("Inline payload SHA-256 mismatch")
    return joblib.load(io.BytesIO(raw))


# ===================================================================== Cancellation Token (P1.1)
class CancelToken:
    """Cooperative cancellation token checked at stage, fold, test, trial, and epoch callbacks."""

    def __init__(
        self,
        *,
        cancel_file: Path | None = None,
        global_event: Any = None,
        queue: Any = None,
        job_id: str = "",
        poll_interval_s: float = 0.02,
    ) -> None:
        self._cancel_file = cancel_file
        self._global_event = global_event
        self._queue = queue
        self.job_id = job_id
        self._poll_interval_s = poll_interval_s
        self._last_check_t = 0.0
        self._tripped = False

    def trip(self) -> None:
        self._tripped = True

    def is_cancelled(self) -> bool:
        if self._tripped:
            return True
        if self._global_event is not None and self._global_event.is_set():
            self._tripped = True
            return True
        now = time.monotonic()
        if self._cancel_file is not None and (now - self._last_check_t) >= self._poll_interval_s:
            self._last_check_t = now
            if self._cancel_file.exists():
                self._tripped = True
                return True
        return False

    def check(self, boundary: str = "boundary") -> None:
        if self.is_cancelled():
            raise InterruptedError(f"cancelled at {boundary}")

    def stage_callback(self, target: str, pct: float, message: str) -> None:
        self.check("stage")
        if self._queue is not None:
            with contextlib.suppress(Exception):
                self._queue.put(
                    {
                        "type": "progress",
                        "job_id": self.job_id,
                        "target": target,
                        "pct": float(pct),
                        "message": str(message),
                    }
                )

    def fold_callback(self, fold_index: int = 0) -> None:
        self.check(f"fold:{fold_index}")

    def test_callback(self, test_name: str = "") -> None:
        self.check(f"test:{test_name}")

    def epoch_callback(self, epoch: int = 0, loss: float | None = None) -> None:
        _ = loss
        self.check(f"epoch:{epoch}")

    def trial_callback(self, study: Any, _trial: Any = None) -> None:
        if self.is_cancelled():
            if study is not None and hasattr(study, "stop"):
                study.stop()
            raise InterruptedError("cancelled at trial")


class _EpochCancelList(list):
    """List subclass hooked onto NN ``history_['train_loss']`` in worker scope to check cancel every epoch."""

    def __init__(self, token: CancelToken) -> None:
        super().__init__()
        self._token = token

    def append(self, item: Any) -> None:
        self._token.epoch_callback(len(self), item if isinstance(item, (int, float)) else None)
        super().append(item)


@contextlib.contextmanager
def _worker_inner_cancel_hooks(token: CancelToken):
    """Attach ``token`` to Optuna trial optimization and NN epoch loops inside this worker call."""
    from . import nn as nnmod

    orig_sk_init = nnmod.SkMLP.__init__
    orig_torch_init = nnmod.TorchSeqRegressor.__init__ if getattr(nnmod, "TORCH_OK", False) else None
    orig_optimize = None

    def _patched_sk_init(self, *a, **kw):
        orig_sk_init(self, *a, **kw)
        self.history_["train_loss"] = _EpochCancelList(token)

    def _patched_torch_init(self, *a, **kw):
        orig_torch_init(self, *a, **kw)  # type: ignore[misc]
        self.history_["train_loss"] = _EpochCancelList(token)

    nnmod.SkMLP.__init__ = _patched_sk_init  # type: ignore[method-assign]
    if orig_torch_init is not None:
        nnmod.TorchSeqRegressor.__init__ = _patched_torch_init  # type: ignore[method-assign]

    try:
        import optuna

        orig_optimize = optuna.study.Study.optimize

        def _patched_optimize(self, func, *a, **kw):
            cbs = list(kw.get("callbacks") or [])
            cbs.append(token.trial_callback)
            kw["callbacks"] = cbs
            return orig_optimize(self, func, *a, **kw)

        optuna.study.Study.optimize = _patched_optimize  # type: ignore[method-assign]
    except ImportError:
        optuna = None  # type: ignore[assignment]

    try:
        yield
    finally:
        nnmod.SkMLP.__init__ = orig_sk_init  # type: ignore[method-assign]
        if orig_torch_init is not None:
            nnmod.TorchSeqRegressor.__init__ = orig_torch_init  # type: ignore[method-assign]
        if orig_optimize is not None and optuna is not None:
            optuna.study.Study.optimize = orig_optimize  # type: ignore[method-assign]


# ===================================================================== Worker Process Runtime
_WORKER_QUEUE: Any = None
_WORKER_CANCEL_EVENT: Any = None


def _worker_initializer(event_queue: Any, global_cancel_event: Any) -> None:
    """Executed inside every spawned worker process before handling requests."""
    global _WORKER_QUEUE, _WORKER_CANCEL_EVENT
    os.environ["_EB_IN_WORKER"] = "1"
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    _WORKER_QUEUE = event_queue
    _WORKER_CANCEL_EVENT = global_cancel_event
    with contextlib.suppress(Exception):
        event_queue.put(
            {
                "type": "worker_ready",
                "pid": os.getpid(),
                "protocol_version": PROTOCOL_VERSION,
            }
        )


def _cmd_worker_ping(payload: dict[str, Any]) -> dict[str, Any]:
    req_ver = payload.get("protocol_version")
    if req_ver != PROTOCOL_VERSION:
        raise PoolHandshakeError(f"Protocol mismatch: worker={PROTOCOL_VERSION}, caller={req_ver}")
    reply = {
        "ok": True,
        "pid": os.getpid(),
        "protocol_version": PROTOCOL_VERSION,
        "in_worker_guard": os.environ.get("_EB_IN_WORKER") == "1",
    }
    if _WORKER_QUEUE is not None:
        _WORKER_QUEUE.put({"type": "pong", "job_id": payload.get("job_id", ""), **reply})
    return reply


def _cmd_worker_run_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    from .pipeline import Cancelled, run_analysis

    job_id = str(payload["job_id"])
    cache_dir = Path(payload["cache_dir"]).resolve()
    cancel_file = Path(payload["cancel_file"]).resolve() if payload.get("cancel_file") else None
    threshold_bytes = int(payload.get("threshold_bytes", PAYLOAD_THRESHOLD_BYTES))

    if _WORKER_QUEUE is not None:
        _WORKER_QUEUE.put({"type": "job_started", "job_id": job_id, "pid": os.getpid()})

    token = CancelToken(
        cancel_file=cancel_file,
        global_event=_WORKER_CANCEL_EVENT,
        queue=_WORKER_QUEUE,
        job_id=job_id,
    )
    df = unpack_payload(payload["df_envelope"], cache_dir)
    time_col = str(payload["time_col"])
    targets = list(payload["targets"])
    config = dict(payload.get("config") or {})
    dataset_sha = payload.get("dataset_sha")
    dataset_info = dict(payload.get("dataset_info") or {})
    artifact_templates: dict[str, str] = dict(payload.get("artifact_dir_by_target") or {})

    def _artifact_fn(target: str, model_name: str) -> str:
        safe_model = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in model_name)
        base_t = artifact_templates.get(target)
        if not base_t:
            return f"{safe_model}.joblib"
        return str(Path(base_t) / f"{safe_model}.joblib")

    try:
        with _worker_inner_cancel_hooks(token):
            run, bundles = run_analysis(
                df,
                time_col,
                targets,
                config,
                dataset_sha=dataset_sha,
                dataset_info=dataset_info,
                progress=token.stage_callback,
                cancel=token.is_cancelled,
                artifact_fn=_artifact_fn if artifact_templates else None,
            )
    except (Cancelled, InterruptedError):
        if _WORKER_QUEUE is not None:
            _WORKER_QUEUE.put({"type": "result_cancelled", "job_id": job_id, "pid": os.getpid()})
        return {"job_id": job_id, "status": "cancelled", "pid": os.getpid()}

    result_envelope = pack_payload(
        (run, bundles),
        cache_dir,
        prefix=f"result_{job_id}",
        threshold_bytes=threshold_bytes,
    )
    if _WORKER_QUEUE is not None:
        _WORKER_QUEUE.put(
            {
                "type": "result",
                "job_id": job_id,
                "pid": os.getpid(),
                "cancelled": bool(run.cancelled),
                "envelope": result_envelope,
            }
        )
    return {
        "job_id": job_id,
        "status": "cancelled" if run.cancelled else "completed",
        "pid": os.getpid(),
        "is_ref": bool(result_envelope.get("__eb_ref__")),
    }


def _cmd_worker_sleep_probe(payload: dict[str, Any]) -> dict[str, Any]:
    """Deterministic diagnostic command for testing cooperative cancel, 10 s hard-kill, and Job Object cleanup."""
    job_id = str(payload["job_id"])
    seconds = float(payload.get("seconds", 30.0))
    ignore_cancel = bool(payload.get("ignore_cancel", False))
    try_open_db = bool(payload.get("try_open_db", False))
    cancel_file = Path(payload["cancel_file"]).resolve() if payload.get("cancel_file") else None

    if _WORKER_QUEUE is not None:
        _WORKER_QUEUE.put({"type": "job_started", "job_id": job_id, "pid": os.getpid()})

    if try_open_db:
        from ..db.session import get_engine

        get_engine()  # Must raise RuntimeError (SHL-5 guard)

    token = CancelToken(
        cancel_file=cancel_file,
        global_event=_WORKER_CANCEL_EVENT,
        queue=_WORKER_QUEUE,
        job_id=job_id,
        poll_interval_s=0.01,
    )
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not ignore_cancel and token.is_cancelled():
            if _WORKER_QUEUE is not None:
                _WORKER_QUEUE.put({"type": "result_cancelled", "job_id": job_id, "pid": os.getpid()})
            return {"job_id": job_id, "status": "cancelled", "pid": os.getpid()}
        time.sleep(0.05)

    if _WORKER_QUEUE is not None:
        _WORKER_QUEUE.put(
            {
                "type": "result",
                "job_id": job_id,
                "pid": os.getpid(),
                "cancelled": False,
                "envelope": pack_payload({"slept": seconds, "pid": os.getpid()}, Path(payload["cache_dir"])),
            }
        )
    return {"job_id": job_id, "status": "completed", "pid": os.getpid()}


# Compile-time static worker command allowlist (SCR-7: zero eval / getattr dispatch)
WORKER_COMMAND_REGISTRY: MappingProxyType[str, Callable[[dict[str, Any]], dict[str, Any]]] = MappingProxyType(
    {
        "worker.ping": _cmd_worker_ping,
        "worker.run_analysis": _cmd_worker_run_analysis,
        "worker.sleep_probe": _cmd_worker_sleep_probe,
    }
)


def _dispatch_worker_command(command_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if command_id not in WORKER_COMMAND_REGISTRY:
        raise UnknownWorkerCommandError(f"Unregistered worker command: {command_id!r}")
    handler = WORKER_COMMAND_REGISTRY[command_id]
    try:
        return handler(payload)
    except Exception as exc:
        if _WORKER_QUEUE is not None and isinstance(payload, dict) and payload.get("job_id"):
            with contextlib.suppress(Exception):
                _WORKER_QUEUE.put(
                    {
                        "type": "error",
                        "job_id": str(payload["job_id"]),
                        "pid": os.getpid(),
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    }
                )
        raise


# ===================================================================== Supervisor & Job Handle
def compute_max_workers(tasks: int = 1, cpu_count: int | None = None) -> int:
    """Compute ``min(CPU // 2, tasks)`` bounded to at least 1 worker."""
    cpu = int(cpu_count if cpu_count is not None else (os.cpu_count() or 2))
    half_cpu = max(1, cpu // 2)
    return max(1, min(half_cpu, max(1, int(tasks))))


class PoolJobHandle:
    """Handle to an in-flight worker job in ``AnalysisPoolManager``."""

    def __init__(
        self,
        *,
        job_id: str,
        manager: "AnalysisPoolManager",
        future: Future,
        cancel_file: Path,
        cache_dir: Path,
        cleanup_paths: list[Path],
        cancel_grace_seconds: float,
        on_progress: Callable[[str, float, str], None] | None = None,
    ) -> None:
        self.job_id = job_id
        self._manager = manager
        self.future = future
        self.cancel_file = cancel_file
        self.cache_dir = cache_dir
        self._cleanup_paths = cleanup_paths
        self.cancel_grace_seconds = cancel_grace_seconds
        self.on_progress = on_progress

        self.done_event = threading.Event()
        self.status = "running"
        self.worker_pid: int | None = None
        self.result_value: Any = None
        self.error_message: str = ""
        self.hard_killed = False
        self._kill_timer: threading.Timer | None = None

    def cancel(self) -> None:
        """Signal cooperative cancellation and arm the hard-kill watchdog timer."""
        try:
            self.cancel_file.parent.mkdir(parents=True, exist_ok=True)
            self.cancel_file.write_text("cancel\n", encoding="utf-8")
        except OSError:
            pass
        if self.done_event.is_set():
            return
        self.status = "cancelling"
        if self._kill_timer is None:
            timer = threading.Timer(self.cancel_grace_seconds, self._on_grace_expired)
            timer.daemon = True
            self._kill_timer = timer
            timer.start()

    def _on_grace_expired(self) -> None:
        if not self.done_event.is_set():
            self.hard_killed = True
            self._manager.hard_kill_job(self.job_id)

    def _finish(self, status: str, *, result: Any = None, error: str = "") -> None:
        if self._kill_timer is not None:
            self._kill_timer.cancel()
            self._kill_timer = None
        self.status = status
        self.result_value = result
        self.error_message = error
        self._cleanup_temp_files()
        self.done_event.set()

    def _cleanup_temp_files(self) -> None:
        for p in list(self._cleanup_paths) + [self.cancel_file]:
            with contextlib.suppress(OSError):
                p.unlink(missing_ok=True)

    def wait(self, timeout: float | None = None) -> Any:
        finished = self.done_event.wait(timeout=timeout)
        if not finished:
            raise TimeoutError(f"Job {self.job_id} did not finish within {timeout}s")
        if self.status == "failed":
            raise RuntimeError(self.error_message or f"Job {self.job_id} failed")
        if self.status == "cancelled":
            raise WorkerCancelledError(f"Job {self.job_id} was cancelled")
        return self.result_value


class AnalysisPoolManager:
    """Supervisor owning the ``spawn`` ``ProcessPoolExecutor``, ``mp.Queue``, and ``WindowsJobObject``."""

    def __init__(
        self,
        *,
        max_tasks: int = 2,
        cache_dir: Path | None = None,
        cancel_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
    ) -> None:
        self._ctx = mp.get_context("spawn")
        self.max_workers = compute_max_workers(max_tasks)
        self.cancel_grace_seconds = float(cancel_grace_seconds)
        self._cache_dir = cache_dir
        self._lock = threading.RLock()

        self._executor: ProcessPoolExecutor | None = None
        self._queue: Any = None
        self._global_cancel: Any = None
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._handshaken_once = False

        self.job_object = WindowsJobObject()
        self.known_worker_pids: set[int] = set()
        self._retired_worker_pids: set[int] = set()
        self._jobs: dict[str, PoolJobHandle] = {}

    def cache_dir(self) -> Path:
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            return self._cache_dir.resolve()
        return resolve_cache_dir(get_base_dir(), create=True)

    def _ensure_pool(self, *, tasks: int | None = None) -> None:
        with self._lock:
            if self._executor is not None:
                return
            if tasks is not None:
                self.max_workers = compute_max_workers(tasks)
            self._reader_stop.clear()
            self._queue = self._ctx.Queue()
            self._global_cancel = self._ctx.Event()
            self._executor = ProcessPoolExecutor(
                max_workers=self.max_workers,
                mp_context=self._ctx,
                initializer=_worker_initializer,
                initargs=(self._queue, self._global_cancel),
            )
            self._reader_thread = threading.Thread(
                target=self._drain_queue_loop,
                name="eb-pool-queue-reader",
                daemon=True,
            )
            self._reader_thread.start()

        timeout = WARM_HANDSHAKE_TIMEOUT_SECONDS if self._handshaken_once else FIRST_HANDSHAKE_TIMEOUT_SECONDS
        self.handshake(timeout=timeout)
        self._handshaken_once = True

    def _register_worker_processes(self) -> list[int]:
        pids: list[int] = []
        with self._lock:
            procs = getattr(self._executor, "_processes", None) or {}
            for pid in list(procs.keys()):
                ipid = int(pid)
                self.known_worker_pids.add(ipid)
                self.job_object.assign_pid(ipid)
                pids.append(ipid)
        return pids

    def handshake(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Verify pool readiness and ``PROTOCOL_VERSION`` within 30 s (cold) / 10 s (warm)."""
        self._ensure_raw_executor()
        wait_s = (
            timeout
            if timeout is not None
            else (WARM_HANDSHAKE_TIMEOUT_SECONDS if self._handshaken_once else FIRST_HANDSHAKE_TIMEOUT_SECONDS)
        )
        fut = self._executor.submit(  # type: ignore[union-attr]
            _dispatch_worker_command,
            "worker.ping",
            {"protocol_version": PROTOCOL_VERSION, "job_id": f"ping_{uuid.uuid4().hex[:8]}"},
        )
        try:
            reply = fut.result(timeout=wait_s)
        except Exception as exc:
            raise PoolHandshakeError(f"Worker pool handshake failed within {wait_s:.1f}s: {exc}") from exc
        if reply.get("protocol_version") != PROTOCOL_VERSION or not reply.get("ok"):
            raise PoolHandshakeError(f"Invalid worker handshake response: {reply}")
        pid = int(reply.get("pid", 0))
        if pid > 0:
            self.known_worker_pids.add(pid)
            self.job_object.assign_pid(pid)
        self._register_worker_processes()
        return reply

    def _ensure_raw_executor(self) -> None:
        with self._lock:
            if self._executor is None:
                self._reader_stop.clear()
                self._queue = self._ctx.Queue()
                self._global_cancel = self._ctx.Event()
                self._executor = ProcessPoolExecutor(
                    max_workers=self.max_workers,
                    mp_context=self._ctx,
                    initializer=_worker_initializer,
                    initargs=(self._queue, self._global_cancel),
                )
                self._reader_thread = threading.Thread(
                    target=self._drain_queue_loop,
                    name="eb-pool-queue-reader",
                    daemon=True,
                )
                self._reader_thread.start()

    def _drain_queue_loop(self) -> None:
        q = self._queue
        while not self._reader_stop.is_set() and q is not None:
            try:
                msg = q.get(timeout=0.1)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")
            if mtype == "STOP":
                break
            pid = int(msg.get("pid") or 0)
            if pid > 0:
                self.known_worker_pids.add(pid)
                self.job_object.assign_pid(pid)

            job_id = str(msg.get("job_id") or "")
            handle = self._jobs.get(job_id)
            if handle is None:
                continue

            if mtype == "job_started":
                handle.worker_pid = pid
            elif mtype == "progress":
                if handle.on_progress is not None:
                    with contextlib.suppress(Exception):
                        handle.on_progress(
                            str(msg.get("target", "")),
                            float(msg.get("pct", 0.0)),
                            str(msg.get("message", "")),
                        )
            elif mtype == "result":
                try:
                    env = msg["envelope"]
                    ref_cleanup = Path(env["path"]) if env.get("__eb_ref__") else None
                    val = unpack_payload(env, handle.cache_dir)
                    if ref_cleanup is not None:
                        ref_cleanup.unlink(missing_ok=True)
                    status = "cancelled" if msg.get("cancelled") else "completed"
                    handle._finish(status, result=val)
                except Exception as exc:
                    handle._finish("failed", error=f"{type(exc).__name__}: {exc}")
            elif mtype == "result_cancelled":
                handle._finish("cancelled")
            elif mtype == "error":
                handle._finish("failed", error=str(msg.get("error", "Worker error")))

    def submit_command(
        self,
        command_id: str,
        payload: dict[str, Any],
        *,
        on_progress: Callable[[str, float, str], None] | None = None,
        cleanup_paths: list[Path] | None = None,
        cancel_grace_seconds: float | None = None,
    ) -> PoolJobHandle:
        if command_id not in WORKER_COMMAND_REGISTRY:
            raise UnknownWorkerCommandError(f"Unregistered worker command: {command_id!r}")

        self._ensure_pool()
        cdir = self.cache_dir()
        job_id = str(payload.get("job_id") or uuid.uuid4().hex)
        cancel_file = validate_path(cdir / f"cancel_{job_id}.flag", allowed_roots=[cdir])
        cancel_file.unlink(missing_ok=True)

        full_payload = {
            **payload,
            "job_id": job_id,
            "cache_dir": str(cdir),
            "cancel_file": str(cancel_file),
        }
        grace = self.cancel_grace_seconds if cancel_grace_seconds is None else float(cancel_grace_seconds)

        with self._lock:
            fut = self._executor.submit(_dispatch_worker_command, command_id, full_payload)  # type: ignore[union-attr]
            handle = PoolJobHandle(
                job_id=job_id,
                manager=self,
                future=fut,
                cancel_file=cancel_file,
                cache_dir=cdir,
                cleanup_paths=list(cleanup_paths or []),
                cancel_grace_seconds=grace,
                on_progress=on_progress,
            )
            self._jobs[job_id] = handle

        def _on_future_done(f: Future) -> None:
            self._register_worker_processes()
            if handle.done_event.is_set():
                return
            exc = f.exception()
            if exc is not None:
                if handle.status == "cancelling" or isinstance(exc, (BrokenProcessPool, WorkerCancelledError)):
                    handle._finish("cancelled")
                else:
                    handle._finish("failed", error=f"{type(exc).__name__}: {exc}")

        fut.add_done_callback(_on_future_done)
        return handle

    def submit_analysis(
        self,
        *,
        df: Any,
        time_col: str,
        targets: list[str],
        config: dict[str, Any] | None = None,
        dataset_sha: str | None = None,
        dataset_info: dict[str, Any] | None = None,
        artifact_dir_by_target: dict[str, str] | None = None,
        on_progress: Callable[[str, float, str], None] | None = None,
        threshold_bytes: int = PAYLOAD_THRESHOLD_BYTES,
        cancel_grace_seconds: float | None = None,
    ) -> PoolJobHandle:
        cdir = self.cache_dir()
        df_env = pack_payload(df, cdir, prefix="dataset", rows=len(df), threshold_bytes=threshold_bytes)
        cleanup = [Path(df_env["path"])] if df_env.get("__eb_ref__") else []
        payload = {
            "df_envelope": df_env,
            "time_col": time_col,
            "targets": list(targets),
            "config": dict(config or {}),
            "dataset_sha": dataset_sha,
            "dataset_info": dict(dataset_info or {}),
            "artifact_dir_by_target": dict(artifact_dir_by_target or {}),
            "threshold_bytes": threshold_bytes,
        }
        return self.submit_command(
            "worker.run_analysis",
            payload,
            on_progress=on_progress,
            cleanup_paths=cleanup,
            cancel_grace_seconds=cancel_grace_seconds,
        )

    def hard_kill_job(self, job_id: str) -> list[int]:
        """Force-terminate unresponsive worker processes and tear down the broken executor."""
        handle = self._jobs.get(job_id)
        killed = self._kill_active_processes()
        if handle is not None and not handle.done_event.is_set():
            handle._finish("cancelled")
        return killed

    def _kill_active_processes(self) -> list[int]:
        killed: list[int] = []
        with self._lock:
            ex = self._executor
            self._executor = None
            procs = dict(getattr(ex, "_processes", None) or {}) if ex is not None else {}

        for pid, proc in procs.items():
            ipid = int(pid)
            self._retired_worker_pids.add(ipid)
            if proc.is_alive():
                with contextlib.suppress(Exception):
                    proc.kill()
                killed.append(ipid)
            with contextlib.suppress(Exception):
                proc.join(timeout=2.0)

        if ex is not None:
            with contextlib.suppress(Exception):
                ex.shutdown(wait=False, cancel_futures=True)

        self._reader_stop.set()
        if self._queue is not None:
            with contextlib.suppress(Exception):
                self._queue.put({"type": "STOP"})
        return killed

    def cancel_all(self) -> int:
        count = 0
        with self._lock:
            if self._global_cancel is not None:
                self._global_cancel.set()
            handles = list(self._jobs.values())
        for h in handles:
            if not h.done_event.is_set():
                h.cancel()
                count += 1
        return count

    def shutdown_cleanly(self, *, join_timeout: float = 5.0) -> dict[str, Any]:
        """Deterministic P1.2 shutdown: cancel -> join (up to ``join_timeout``) -> hard_stop."""
        t0 = time.monotonic()
        cancelled_n = self.cancel_all()

        with self._lock:
            active_handles = [h for h in self._jobs.values() if not h.done_event.is_set()]
            ex = self._executor
            procs = list((getattr(ex, "_processes", None) or {}).values()) if ex is not None else []

        deadline = t0 + max(0.05, float(join_timeout))
        for h in active_handles:
            rem = max(0.0, deadline - time.monotonic())
            h.done_event.wait(timeout=rem)

        # Shutdown executor cooperatively if no active jobs remain
        if ex is not None and all(h.done_event.is_set() for h in active_handles):
            with self._lock:
                self._executor = None
            with contextlib.suppress(Exception):
                ex.shutdown(wait=False, cancel_futures=True)
            for proc in procs:
                rem = max(0.0, deadline - time.monotonic())
                with contextlib.suppress(Exception):
                    proc.join(timeout=rem)
                if proc.pid:
                    self._retired_worker_pids.add(int(proc.pid))

        joined_cleanly = all(not p.is_alive() for p in procs)
        hard_killed = []
        if not joined_cleanly or self._executor is not None:
            hard_killed = self._kill_active_processes()
            for proc in procs:
                if proc.is_alive():
                    with contextlib.suppress(Exception):
                        proc.kill()
                    with contextlib.suppress(Exception):
                        proc.join(timeout=2.0)
                    if proc.pid:
                        hard_killed.append(int(proc.pid))
                        self._retired_worker_pids.add(int(proc.pid))

        self._reader_stop.set()
        if self._queue is not None:
            with contextlib.suppress(Exception):
                self._queue.put({"type": "STOP"})
                self._queue.close()
            self._queue = None

        self.job_object.close()
        orphans = self.live_orphan_pids()
        return {
            "cancelled_jobs": cancelled_n,
            "joined_within_timeout": joined_cleanly,
            "hard_killed_pids": sorted(set(hard_killed)),
            "orphan_pids": orphans,
            "elapsed_s": round(time.monotonic() - t0, 4),
        }

    def live_orphan_pids(self) -> list[int]:
        """Return any retired worker PIDs that are still alive in the OS process table."""
        return sorted(pid for pid in self._retired_worker_pids if is_pid_alive(pid))

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            active_jobs = [
                {"job_id": h.job_id, "status": h.status, "worker_pid": h.worker_pid}
                for h in self._jobs.values()
                if not h.done_event.is_set()
            ]
            alive_workers = []
            if self._executor is not None:
                for pid, proc in (getattr(self._executor, "_processes", None) or {}).items():
                    if proc.is_alive():
                        alive_workers.append(int(pid))
        cdir = self.cache_dir()
        cache_files = list(cdir.glob("*")) if cdir.is_dir() else []
        cache_bytes = sum(f.stat().st_size for f in cache_files if f.is_file())
        orphans = self.live_orphan_pids()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "state": "running" if active_jobs else ("ready" if self._executor is not None else "idle"),
            "max_workers": self.max_workers,
            "active_jobs": active_jobs,
            "alive_worker_pids": sorted(alive_workers),
            "orphan_pids": orphans,
            "orphan_count": len(orphans),
            "job_object_supported": self.job_object.supported,
            "job_object_active": self.job_object.active,
            "job_object_assigned_pids": sorted(self.job_object.assigned_pids),
            "cache_dir": str(cdir),
            "cache_file_count": len(cache_files),
            "cache_size_bytes": cache_bytes,
        }


# Process-wide default manager for the desktop UI process
_default_manager_lock = threading.Lock()
_default_manager: AnalysisPoolManager | None = None


def get_pool_manager() -> AnalysisPoolManager:
    global _default_manager
    with _default_manager_lock:
        if _default_manager is None:
            _default_manager = AnalysisPoolManager()
        return _default_manager


def shutdown_default_pool(*, join_timeout: float = 5.0) -> dict[str, Any]:
    global _default_manager
    with _default_manager_lock:
        mgr = _default_manager
        _default_manager = None
    if mgr is None:
        return {
            "cancelled_jobs": 0,
            "joined_within_timeout": True,
            "hard_killed_pids": [],
            "orphan_pids": [],
            "elapsed_s": 0.0,
        }
    return mgr.shutdown_cleanly(join_timeout=join_timeout)


def run_in_pool(
    payload: dict[str, Any],
    *,
    progress: Callable[[str, float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    manager: AnalysisPoolManager | None = None,
    threshold_bytes: int = PAYLOAD_THRESHOLD_BYTES,
    cancel_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
) -> tuple[Any, dict[str, Any]]:
    """Execute ``run_analysis`` inside the ``spawn`` process pool and return ``(RunResult, bundles)``."""
    own_mgr = manager is None
    mgr = manager or AnalysisPoolManager(
        max_tasks=len(payload.get("targets") or [1]),
        cache_dir=Path(payload["cache_dir"]) if payload.get("cache_dir") else None,
        cancel_grace_seconds=cancel_grace_seconds,
    )
    try:
        handle = mgr.submit_analysis(
            df=payload["df"],
            time_col=payload["time_col"],
            targets=list(payload["targets"]),
            config=payload.get("config"),
            dataset_sha=payload.get("dataset_sha"),
            dataset_info=payload.get("dataset_info"),
            artifact_dir_by_target=payload.get("artifact_dir_by_target"),
            on_progress=progress,
            threshold_bytes=threshold_bytes,
            cancel_grace_seconds=cancel_grace_seconds,
        )
        while not handle.done_event.wait(timeout=0.05):
            if cancel_check is not None and cancel_check() and handle.status == "running":
                handle.cancel()
        if handle.status == "cancelled":
            from .pipeline import RunResult, collect_versions

            return (
                RunResult(
                    seed=0,
                    config=dict(payload.get("config") or {}),
                    versions=collect_versions(),
                    auto=[],
                    targets={},
                    created=time.strftime("%Y-%m-%d %H:%M:%S"),
                    dataset=dict(payload.get("dataset_info") or {}),
                    cancelled=True,
                ),
                {},
            )
        return handle.wait()
    finally:
        if own_mgr:
            mgr.shutdown_cleanly(join_timeout=5.0)
