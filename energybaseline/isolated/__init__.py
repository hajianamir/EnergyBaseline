"""Separately provisioned engine runtimes with hash-locked dependencies and a file protocol.

Auto-sklearn, TPOT, H2O, AutoGluon and Chronos cannot share the application's dependency
set, so each one runs in its own virtual environment and operating-system process. The
application never imports them. Provisioning is an explicit operator action that needs
package-index access; execution afterwards uses only local files.

This boundary isolates dependencies, bounds wall-clock time and terminates the engine's
process tree. It is not an operating-system sandbox against hostile third-party code.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from types import MappingProxyType

PACKAGE_ROOT = Path(__file__).resolve().parent
LOCKS = PACKAGE_ROOT / "locks"
WORKERS = PACKAGE_ROOT / "workers"
MANIFEST_NAME = "energybaseline-engine.json"
# Installer bootstrap distributions that a virtual environment owns outside the lock.
BOOTSTRAP = frozenset({"pip", "setuptools", "wheel"})
RUN_PREFIX, INSTALL_PREFIX = "run-", "provision-"
MAX_RUN_SECONDS, MAX_INSTALL_SECONDS = 600, 7200
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*(.*?))?\s*\\?$")


class ProvisioningRequired(ValueError):
    """A separately provisioned runtime or model asset is absent or unverified.

    Messages are fixed operator guidance and never contain dataset values.
    """


class EngineUnavailable(ProvisioningRequired):
    """The engine runtime is absent, unsupported here or differs from the approved lock."""


class EngineFailure(RuntimeError):
    """The engine process failed, exceeded its budget or broke the result contract."""


@dataclass(frozen=True)
class EngineSpec:
    name: str
    distribution: str
    version: str
    python: tuple[int, int]
    platforms: tuple[str, ...]
    worker: str
    license: str
    note: str


ENGINES = MappingProxyType(
    {
        "tpot": EngineSpec(
            "tpot",
            "tpot",
            "1.1.0",
            (3, 12),
            ("linux", "win32"),
            "automl_worker.py",
            "LGPL-3.0",
            "Genetic-programming pipeline search on a Dask local cluster.",
        ),
        "h2o": EngineSpec(
            "h2o",
            "h2o",
            "3.46.0.12",
            (3, 12),
            ("linux", "win32"),
            "automl_worker.py",
            "Apache-2.0",
            "H2O AutoML in a loopback-only authenticated JVM from the locked jdk4py Java 17 runtime.",
        ),
        "autogluon": EngineSpec(
            "autogluon",
            "autogluon.tabular",
            "1.6.3",
            (3, 12),
            ("linux", "win32"),
            "automl_worker.py",
            "Apache-2.0",
            "AutoGluon Tabular with gradient-boosting and forest models.",
        ),
        "autosklearn": EngineSpec(
            "autosklearn",
            "auto-sklearn",
            "0.15.0",
            (3, 9),
            ("linux",),
            "automl_worker.py",
            "BSD-3-Clause",
            "Final upstream release: POSIX only, Python 3.9, scikit-learn 0.24. No Windows support exists upstream.",
        ),
        "chronos": EngineSpec(
            "chronos",
            "chronos-forecasting",
            "2.3.2",
            (3, 12),
            ("linux", "win32"),
            "chronos_worker.py",
            "Apache-2.0",
            "Chronos-Bolt zero-shot forecasting from separately provisioned pretrained weights.",
        ),
    }
)


def _platform():
    return "win32" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform


def _spec(name):
    if not isinstance(name, str) or name not in ENGINES:
        raise ValueError("Unknown isolated engine.")
    return ENGINES[name]


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_path(name):
    return LOCKS / f"{_spec(name).name}.lock"


def lock_sha256(name):
    return hashlib.sha256(lock_path(name).read_bytes()).hexdigest()


def _marker_applies(marker, python, platform):
    """Evaluate the lock's environment markers with the packaging library when present.

    Only the lock files shipped with the application are parsed here, never caller text.
    """
    if not marker:
        return True
    from packaging.markers import Marker

    version = f"{python[0]}.{python[1]}"
    environment = {
        "python_version": version,
        "python_full_version": f"{version}.0",
        "sys_platform": platform,
        "platform_system": "Windows" if platform == "win32" else "Linux",
        "os_name": "nt" if platform == "win32" else "posix",
        "platform_machine": "AMD64" if platform == "win32" else "x86_64",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
    }
    return Marker(marker).evaluate(environment)


def locked_distributions(name, platform=None):
    """Return the normalized name -> version pins that apply to this engine and platform."""
    spec = _spec(name)
    platform = platform or _platform()
    pins = {}
    for line in lock_path(name).read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line)
        if match is None:
            continue
        distribution, version, marker = match.groups()
        if _marker_applies(marker, spec.python, platform):
            key = normalize(distribution)
            if pins.setdefault(key, version) != version:
                raise EngineUnavailable("Engine lock pins one distribution twice for this platform.")
    if pins.get(normalize(spec.distribution)) != spec.version:
        raise EngineUnavailable("Engine lock does not pin the registered engine version.")
    return pins


def engines_root():
    from ..io.paths import resolve_app_home

    return resolve_app_home()[0] / "engines"


def runtime_dir(name):
    return engines_root() / f"{_spec(name).name}-{lock_sha256(name)[:16]}"


def runtime_python(name):
    root = runtime_dir(name)
    return root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _base_environment(private):
    """Build the child environment from nothing: no inherited paths, proxies or credentials."""
    from ..config.runtime_env import get_process_environ

    parent = get_process_environ()
    env = {
        "HOME": str(private),
        "USERPROFILE": str(private),
        "TMP": str(private),
        "TEMP": str(private),
        "TMPDIR": str(private),
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(private),
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "HF_HOME": str(private),
        "DASK_DISTRIBUTED__DASHBOARD__LINK": "",
        "DASK_TEMPORARY_DIRECTORY": str(private),
    }
    if sys.platform == "win32":
        root = parent.get("SYSTEMROOT") or parent.get("SystemRoot") or r"C:\Windows"
        env.update(
            {
                "SYSTEMROOT": root,
                "WINDIR": root,
                "COMSPEC": str(Path(root) / "System32" / "cmd.exe"),
                "PATH": os.pathsep.join([str(Path(root) / "System32"), root]),
                "LOCALAPPDATA": str(private),
                "APPDATA": str(private),
            }
        )
        # Machine facts that the interpreter, the JVM and native libraries read; none is personal.
        for key in ("SYSTEMDRIVE", "PROGRAMDATA", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "PATHEXT", "OS"):
            if parent.get(key):
                env[key] = parent[key]
    else:
        env["PATH"] = "/usr/bin:/bin"
        env["LANG"] = "C.UTF-8"
    return env


def _kill_tree(process, job):
    if job is not None:
        job.close()
    elif sys.platform == "win32" and process.poll() is None:
        # Job assignment failed (for example inside a restrictive parent job): ask Windows to
        # terminate the whole tree rooted at the child instead.
        from ..config.runtime_env import get_process_environ

        root = get_process_environ().get("SYSTEMROOT") or r"C:\Windows"
        subprocess.run(  # noqa: S603 - fixed system executable
            [str(Path(root) / "System32" / "taskkill.exe"), "/F", "/T", "/PID", str(process.pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, check=False, timeout=30,
        )  # fmt: skip
    if sys.platform != "win32":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - the kernel did not reap a killed child
        pass


def _resume(process):
    """Let a process created with CREATE_SUSPENDED run (Windows only)."""
    import ctypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.restype = ctypes.c_long
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    if ntdll.NtResumeProcess(int(process._handle)) != 0:
        raise EngineFailure("Isolated engine process could not be started.")


def _run(command, *, cwd, env, timeout, log, lifeline=False):
    """Run one child in its own process group/job and always terminate everything it started.

    With ``lifeline`` the worker also learns when this process dies and terminates its own
    tree. On POSIX its standard input is a pipe that is never written, so end-of-file is the
    signal. On Windows the job object does this in the kernel and the worker additionally
    waits on this process's handle; a pipe is not used there because a blocked read on a
    Windows pipe stalls every grandchild that inherits the handle.
    """
    options = {}
    job = None
    pipe = lifeline and sys.platform != "win32"
    if lifeline:
        env = dict(env, EB_ENGINE_PARENT_PID=str(os.getpid()))
    if sys.platform == "win32":
        # CREATE_SUSPENDED: the job must own the process before it can start any child. A
        # virtual-environment interpreter on Windows is a launcher that immediately starts
        # the real interpreter, which would otherwise escape a job assigned afterwards.
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x00000004
    else:
        options["start_new_session"] = True
    with open(log, "ab") as stream:
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter and packaged worker, no shell
            [str(part) for part in command],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE if pipe else subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            **options,
        )
        try:
            if sys.platform == "win32":
                from ..core.pool import WindowsJobObject

                job = WindowsJobObject()
                if not job.assign_pid(process.pid):
                    job.close()
                    job = None
                _resume(process)
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise EngineFailure("Isolated engine exceeded its wall-clock budget and was terminated.") from None
        finally:
            _kill_tree(process, job)
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass


def _workspace(prefix):
    """Create a private directory under the application's temporary root, sweeping stale ones.

    A workspace outlives its call only when the application was killed mid-run. Engine calls
    are limited to ``MAX_RUN_SECONDS`` and provisioning to ``MAX_INSTALL_SECONDS``, so an
    older directory with the same prefix cannot belong to a live call and is removed.
    """
    import time

    from ..io.paths import resolve_temp_dir

    root = resolve_temp_dir("EnergyBaselineEngines")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = time.time()
    for name, limit in ((RUN_PREFIX, MAX_RUN_SECONDS), (INSTALL_PREFIX, MAX_INSTALL_SECONDS)):
        for stale in root.glob(name + "*"):
            try:
                if stale.is_dir() and not stale.is_symlink() and now - stale.stat().st_mtime > limit + 300:
                    shutil.rmtree(stale, ignore_errors=True)
            except OSError:
                continue
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


def _remove(path):
    for _ in range(12):
        shutil.rmtree(path, ignore_errors=True)
        if not Path(path).exists():
            return
        import time

        time.sleep(0.5)
    if Path(path).exists():
        raise EngineFailure("Private engine workspace could not be removed.")


def _keep_diagnostics(name, log):
    """Copy the third-party log only into a directory the operator explicitly designated."""
    from ..config.runtime_env import get_process_environ

    target = get_process_environ().get("EB_ENGINE_DIAGNOSTICS", "").strip()
    if target and Path(target).is_dir() and Path(log).is_file():
        shutil.copyfile(log, Path(target) / f"{name}-engine.log")


def _probe(python, private):
    """Ask an interpreter for its identity and installed distributions without importing engines."""
    code = (
        "import json,sys\n"
        "try:\n"
        "    from importlib import metadata\n"
        "except ImportError:\n"
        "    metadata = None\n"
        "import site\n"
        "rows = sorted({(d.metadata['Name'], d.version)\n"
        "               for d in metadata.distributions(path=site.getsitepackages())}) if metadata else []\n"
        "json.dump({'version': list(sys.version_info[:3]), 'platform': sys.platform,\n"
        "           'implementation': sys.implementation.name, 'prefix': sys.prefix,\n"
        "           'base_prefix': sys.base_prefix, 'distributions': rows}, open(sys.argv[1], 'w'))\n"
    )
    out = Path(private) / "probe.json"
    out.unlink(missing_ok=True)
    status = _run(
        [python, "-s", "-B", "-c", code, out],
        cwd=private,
        env=_base_environment(private),
        timeout=120,
        log=Path(private) / "probe.log",
    )
    if status != 0 or not out.is_file():
        raise EngineUnavailable("Interpreter probe failed.")
    return json.loads(out.read_text(encoding="utf-8"))


def _check_closure(name, probe):
    """Every installed distribution must be exactly one applicable lock pin; none may be missing."""
    spec = _spec(name)
    if probe["implementation"] != "cpython" or tuple(probe["version"][:2]) != spec.python:
        raise EngineUnavailable("Engine runtime uses a different Python version than its lock.")
    pins = locked_distributions(name)
    installed = {normalize(n): v for n, v in probe["distributions"]}
    if len(installed) != len(probe["distributions"]):
        raise EngineUnavailable("Engine runtime contains duplicate distribution metadata.")
    extra = sorted(k for k in installed if k not in pins and k not in BOOTSTRAP)
    missing = sorted(k for k in pins if k not in installed)
    changed = sorted(k for k, v in installed.items() if k in pins and pins[k] != v)
    if extra or missing or changed:
        raise EngineUnavailable(
            "Engine runtime differs from its hash lock "
            f"(unlocked={len(extra)}, missing={len(missing)}, changed={len(changed)})."
        )
    return {k: installed[k] for k in sorted(installed)}


def engine_status(name):
    """Describe one engine without starting it; never raises for an absent runtime."""
    spec = _spec(name)
    supported = _platform() in spec.platforms
    root = runtime_dir(name)
    manifest = root / MANIFEST_NAME
    state = "unsupported-platform" if not supported else "not-installed"
    if supported and manifest.is_file() and runtime_python(name).is_file():
        try:
            recorded = json.loads(manifest.read_text(encoding="utf-8"))
            state = "installed" if recorded.get("lock_sha256") == lock_sha256(name) else "stale"
        except (OSError, ValueError):
            state = "stale"
    return {
        "engine": spec.name,
        "distribution": spec.distribution,
        "version": spec.version,
        "license": spec.license,
        "python": f"{spec.python[0]}.{spec.python[1]}",
        "platforms": list(spec.platforms),
        "lock_sha256": lock_sha256(name),
        "state": state,
        "note": spec.note,
    }


def install_engine(name, python=None, *, timeout=3600):
    """Create the engine's virtual environment from its hash lock and verify the closure.

    ``python`` is the base CPython interpreter of the version the lock was resolved for.
    It defaults to the running interpreter when that is a matching, non-frozen CPython.
    Wheels and source archives are verified against the lock's SHA-256 values by pip.
    Source distributions execute their own build back ends during installation.
    """
    spec = _spec(name)
    if _platform() not in spec.platforms:
        raise EngineUnavailable(f"{spec.distribution} {spec.version} does not support this platform.")
    if python is None:
        if getattr(sys, "frozen", False) or sys.version_info[:2] != spec.python:
            raise EngineUnavailable(
                f"Pass an explicit CPython {spec.python[0]}.{spec.python[1]} interpreter for this engine."
            )
        python = sys.executable
    python = Path(python)
    if not python.is_file():
        raise EngineUnavailable("Base interpreter does not exist.")
    root = runtime_dir(name)
    root.parent.mkdir(parents=True, exist_ok=True)
    if not 60 <= timeout <= MAX_INSTALL_SECONDS:
        raise ValueError("Provisioning timeout is outside the supported range.")
    private = _workspace(INSTALL_PREFIX)
    try:
        base = _probe(python, private)
        if base["implementation"] != "cpython" or tuple(base["version"][:2]) != spec.python:
            raise EngineUnavailable(
                f"Base interpreter must be CPython {spec.python[0]}.{spec.python[1]} for this engine."
            )
        _remove(root)
        env = _base_environment(private)
        # Compilers and the dynamic loader for source builds stay reachable during provisioning only.
        from ..config.runtime_env import get_process_environ

        env["PATH"] = get_process_environ().get("PATH", env["PATH"])
        log = private / "install.log"
        if _run([python, "-s", "-m", "venv", root], cwd=private, env=env, timeout=600, log=log) != 0:
            raise EngineUnavailable("Virtual environment creation failed.")
        child = runtime_python(name)
        command = [
            child, "-s", "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
            "--no-cache-dir", "--require-hashes", "-r", lock_path(name),
        ]  # fmt: skip
        if _run(command, cwd=private, env=env, timeout=timeout, log=log) != 0:
            tail = log.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise EngineUnavailable(f"Hash-locked dependency installation failed.\n{tail}")
        probe = _probe(child, private)
        closure = _check_closure(name, probe)
        manifest = {
            "engine": spec.name,
            "distribution": spec.distribution,
            "version": spec.version,
            "lock_sha256": lock_sha256(name),
            "python": ".".join(str(v) for v in probe["version"]),
            "platform": probe["platform"],
            "distributions": closure,
        }
        (root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest
    except BaseException:
        _remove(root)
        raise
    finally:
        _remove(private)


def remove_engine(name):
    _remove(runtime_dir(name))
    return engine_status(name)


def run_engine(name, request, arrays=None, *, timeout, extra_paths=None):
    """Execute the packaged worker in the engine runtime and return its validated JSON result.

    ``arrays`` are float64 NumPy arrays written as pickle-free ``.npy`` files into a private
    directory that is removed before returning. The child receives only this directory.
    The installed closure is rechecked against the hash lock on every call.
    """
    import numpy as np

    spec = _spec(name)
    if not 1 <= timeout <= MAX_RUN_SECONDS:
        raise ValueError("Engine timeout is outside the supported range.")
    status = engine_status(name)
    if status["state"] == "unsupported-platform":
        raise EngineUnavailable(
            f"Engine runtime is unsupported-platform: {spec.distribution} {spec.version} "
            "has no release for this operating system."
        )
    if status["state"] != "installed":
        raise EngineUnavailable(f"Engine runtime is {status['state']}; provision it with 'engines install {name}'.")
    worker = WORKERS / spec.worker
    if not worker.is_file():
        raise EngineUnavailable("Packaged engine worker is missing.")
    private = _workspace(RUN_PREFIX)
    try:
        closure = _check_closure(name, _probe(runtime_python(name), private))
        for key, value in (arrays or {}).items():
            if not re.fullmatch(r"[a-z_]{1,32}", key):
                raise ValueError("Invalid engine array name.")
            np.save(private / f"{key}.npy", np.ascontiguousarray(value, dtype=np.float64), allow_pickle=False)
        payload = dict(request, engine=spec.name, paths={k: str(v) for k, v in (extra_paths or {}).items()})
        (private / "request.json").write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
        try:
            code = _run(
                [runtime_python(name), "-s", "-B", worker, private],
                cwd=private,
                env=_base_environment(private),
                timeout=timeout,
                log=private / "engine.log",
                lifeline=True,
            )
        except EngineFailure:
            _keep_diagnostics(spec.name, private / "engine.log")
            raise
        result_path = private / "result.json"
        if code != 0 or not result_path.is_file() or result_path.stat().st_size > 20_000_000:
            _keep_diagnostics(spec.name, private / "engine.log")
            raise EngineFailure("Isolated engine failed; third-party diagnostics are not exported.")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError:
            raise EngineFailure("Isolated engine returned malformed output.") from None
        if not isinstance(result, dict) or result.get("engine") != spec.name:
            raise EngineFailure("Isolated engine returned an unexpected result envelope.")
        reported = {normalize(n): v for n, v in result.pop("distributions", [])}
        if reported != closure or result.pop("foreign_modules", None) != 0:
            raise EngineFailure("Isolated engine executed with a different dependency closure.")
        if result.get("engine_version") != spec.version:
            raise EngineFailure("Isolated engine reported an unexpected version.")
        result["lock_sha256"] = status["lock_sha256"]
        result["runtime_python"] = json.loads((runtime_dir(name) / MANIFEST_NAME).read_text(encoding="utf-8"))["python"]
        return result
    finally:
        _remove(private)
