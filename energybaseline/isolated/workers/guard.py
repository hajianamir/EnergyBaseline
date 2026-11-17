"""Shared worker helpers. Runs inside an isolated engine runtime: standard library only.

Kept compatible with Python 3.9 because the auto-sklearn runtime uses that version.
"""

import json
import os
import sys

LOOPBACK = ("127.0.0.1", "::1", "localhost", "")


def deny_external_network():
    """Refuse name resolution and connections that leave the loopback interface.

    Listening sockets must also bind loopback explicitly: a wildcard bind is refused, which
    stops engine dashboards from being exposed on other interfaces. The H2O client needs
    loopback TCP. This interpreter-level audit hook covers Python code in this process; it
    is not a kernel firewall and does not cover a JVM or other non-Python child.
    """

    def hook(event, args):
        if event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"):
            host = args[0]
            if isinstance(host, bytes):
                host = host.decode("ascii", "replace")
            if host is not None and str(host) not in LOOPBACK:
                raise PermissionError("External name resolution is disabled in isolated engines.")
        elif event == "socket.gethostbyaddr":
            if str(args[0]) not in LOOPBACK:
                raise PermissionError("External name resolution is disabled in isolated engines.")
        elif event in ("socket.connect", "socket.sendto", "socket.bind"):
            address = args[-1] if event == "socket.sendto" else args[1]
            # An empty or wildcard bind address would listen on every interface.
            allowed = LOOPBACK[:3] if event == "socket.bind" else LOOPBACK
            if isinstance(address, tuple) and address and str(address[0]) not in allowed:
                raise PermissionError("Non-loopback sockets are disabled in isolated engines.")

    sys.addaudithook(hook)


def watch_parent(root):
    """Stop this engine and everything it started as soon as the application process is gone.

    POSIX: the parent holds the write end of this process's standard input and never writes
    to it, so end-of-file means the parent exited or was killed. A detached helper then
    removes the private workspace after the whole process group has been killed.

    Windows: the parent's job object terminates the tree when the parent dies. As a fallback
    for a parent that could not create the job, this waits on the parent's process handle,
    named by ``EB_ENGINE_PARENT_PID``. Standard input is deliberately not used there: a
    blocked read on a Windows pipe stalls every child process that inherits the handle.
    """
    import threading

    parent_pid = os.environ.get("EB_ENGINE_PARENT_PID", "")
    if os.name == "nt" and not parent_pid.isdigit():
        return

    def wait():
        if os.name == "nt":
            import ctypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel.WaitForSingleObject.restype = ctypes.c_uint32
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            handle = kernel.OpenProcess(0x00100000, 0, int(parent_pid))  # SYNCHRONIZE
            if handle:
                kernel.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
            elif ctypes.get_last_error() != 87:  # ERROR_INVALID_PARAMETER: no such process
                return  # The parent exists but cannot be observed; its job object remains in charge.
        else:
            # Read the raw descriptor: a daemon thread blocked inside the buffered stdin object
            # would make interpreter shutdown abort while it tries to take that object's lock.
            try:
                os.read(0, 1)
            except OSError:
                pass
        import subprocess

        try:
            if os.name == "posix":
                import signal

                subprocess.Popen(
                    [sys.executable, "-I", "-c",
                     "import shutil,sys,time; time.sleep(1); shutil.rmtree(sys.argv[1], ignore_errors=True)", root],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd="/", start_new_session=True,
                )  # fmt: skip
                os.killpg(os.getpgid(0), signal.SIGKILL)
            else:
                taskkill = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "taskkill.exe")
                subprocess.call(
                    [taskkill, "/F", "/T", "/PID", str(os.getpid())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )  # fmt: skip
        finally:
            os._exit(1)

    threading.Thread(target=wait, name="parent-watch", daemon=True).start()


def distributions():
    """Distributions installed in this runtime's own site-packages.

    The search is limited to site-packages on purpose: some packages add private vendored
    directories to ``sys.path`` on import, and their bundled metadata is not an installed
    distribution. ``foreign_modules`` separately proves where loaded code came from.
    """
    import site
    from importlib import metadata

    return sorted({(d.metadata["Name"], d.version) for d in metadata.distributions(path=site.getsitepackages())})


def foreign_modules():
    """Count loaded modules whose file lies outside this runtime, its base Python and the workers.

    Import machinery always records absolute paths. A few libraries register synthetic
    namespace modules with a bare placeholder name instead; those have no file to check.
    """
    roots = [sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix, os.path.dirname(__file__)]
    roots = tuple(os.path.normcase(os.path.realpath(r)) + os.sep for r in roots)
    count = 0
    for module in list(sys.modules.values()):
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str) or not os.path.isabs(origin):
            continue
        if not (os.path.normcase(os.path.realpath(origin)) + os.sep).startswith(roots):
            count += 1
    return count


def read_request(root):
    with open(os.path.join(root, "request.json"), encoding="utf-8") as stream:
        request = json.load(stream)
    record_stall_before(request.get("budget_seconds"))
    return request


def record_stall_before(seconds):
    """Shortly before the wall-clock budget ends, describe where this engine is waiting.

    Thread stacks of this process and, on Linux, the state and current system call of every
    process in its session go to the private log. They hold file names, line numbers and
    command lines, never data values, and the log leaves the private directory only when the
    operator designated a diagnostics directory.
    """
    if not isinstance(seconds, int) or seconds < 12:
        return
    import faulthandler
    import threading

    faulthandler.dump_traceback_later(seconds - 5, exit=False, file=sys.stderr)

    def processes():
        session = os.getsid(0)
        for name in sorted(os.listdir("/proc"), key=lambda value: (len(value), value)):
            if not name.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % name) as stream:
                    fields = stream.read().rsplit(")", 1)[1].split()
                if int(fields[3]) != session:
                    continue
                with open("/proc/%s/cmdline" % name, "rb") as stream:
                    command = stream.read().replace(b"\0", b" ").decode("utf-8", "replace")[:240]
                try:
                    with open("/proc/%s/syscall" % name) as stream:
                        call = stream.read().split()[:2]
                except OSError:
                    call = ["unreadable"]
                sys.stderr.write("stall: pid=%s ppid=%s state=%s syscall=%s %s\n" % (name, fields[1], fields[0], call, command))
            except (OSError, ValueError, IndexError):
                continue
        sys.stderr.flush()

    if sys.platform.startswith("linux"):
        timer = threading.Timer(seconds - 6, processes)
        timer.daemon = True
        timer.start()


def load(root, name):
    import numpy as np

    return np.load(os.path.join(root, name + ".npy"), allow_pickle=False)


def write_result(root, result):
    """Publish the result atomically so the parent never reads a partial file."""
    result["distributions"] = distributions()
    result["foreign_modules"] = foreign_modules()
    partial = os.path.join(root, "result.json.partial")
    with open(partial, "w", encoding="utf-8") as stream:
        json.dump(result, stream, allow_nan=False)
    os.replace(partial, os.path.join(root, "result.json"))


def finite_list(values):
    import numpy as np

    array = np.asarray(values, dtype=float).reshape(-1)
    if not np.isfinite(array).all():
        raise ValueError("Engine produced nonfinite predictions.")
    return [float(v) for v in array]
