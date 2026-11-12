"""Entry point used by EnergyBaseline.exe (PyInstaller) and ``python -m energybaseline``.

Launcher contract: run ``freeze_support()`` for spawned worker processes, ensure local
``./data`` and ``./logs`` folders when writable without overriding DB environment settings (DB-2),
then hand off to ``energybaseline.gui.app.main``.
"""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
from pathlib import Path


def _base() -> Path:
    from .config import get_base_dir

    return get_base_dir()


def make_shortcuts(force: bool = False) -> list[str]:
    """Create EnergyBaseline.lnk (custom .ico) on the Desktop and in the application folder (Windows only)."""
    if sys.platform != "win32":
        return []
    base = _base()
    exe = base / "EnergyBaseline.exe"
    if getattr(sys, "frozen", False):
        target, args = str(Path(sys.executable)), ""
    elif exe.exists():
        target, args = str(exe), ""
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        target, args = str(pyw if pyw.exists() else sys.executable), "-m energybaseline"
    ico = None
    for cand in (base / "assets" / "energybaseline.ico", base / "_internal" / "assets" / "energybaseline.ico"):
        if cand.exists():
            ico = cand
            break
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    made = []
    for folder in (desktop, base):
        lnk = folder / "EnergyBaseline.lnk"
        if lnk.exists() and not force:
            continue
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
            "$s.TargetPath='%s';$s.Arguments='%s';$s.WorkingDirectory='%s';%s$s.Save()"
            % (lnk, target, args, base, f"$s.IconLocation='{ico}';" if ico else "")
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                timeout=25,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            made.append(str(lnk))
        except Exception:
            pass
    return made


def main(argv: list[str] | None = None) -> None:
    multiprocessing.freeze_support()
    base = _base()
    try:
        base.mkdir(parents=True, exist_ok=True)
        (base / "data").mkdir(exist_ok=True)
        (base / "logs").mkdir(exist_ok=True)
    except OSError:
        pass
    os.environ.setdefault("QT_API", "pyside6")
    if os.environ.get("EB_NONINTERACTIVE") != "1":
        try:
            make_shortcuts()
        except Exception:
            pass
    from .gui.app import main as gui_main

    sys.exit(gui_main(argv))


if __name__ == "__main__":
    main()
