"""Build EnergyBaseline.exe (PyInstaller one-folder) and place it in the project root.

    python scripts/build_exe.py

Result: EnergyBaseline.exe + _internal\ in the project root (double-click to start), plus EnergyBaseline.lnk.
Must be run on Windows 10/11 64-bit (PyInstaller does not cross-compile).
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = ";" if sys.platform == "win32" else ":"


def main():
    if sys.platform != "win32":
        print("WARNING: not on Windows - this produces a native binary for the current OS, not a .exe")
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "make_icon.py")])
    work = ROOT / "build"
    dist = ROOT / "dist"
    data = [("alembic", "alembic"), ("alembic.ini", "."), ("assets", "assets"), ("docs", "docs"), ("samples", "samples")]
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "EnergyBaseline",
           "--icon", str(ROOT / "assets" / "energybaseline.ico"), "--distpath", str(dist), "--workpath", str(work),
           "--specpath", str(work), "--paths", str(ROOT)]
    for src, dst in data:
        cmd += ["--add-data", f"{ROOT / src}{SEP}{dst}"]
    for h in ("sqlalchemy.dialects.sqlite", "alembic.runtime.migration", "alembic.ddl.sqlite", "sklearn.utils._typedefs", "sklearn.neighbors._partition_nodes",
              "statsmodels.tsa.statespace._filters", "scipy.special._cdflib", "pymysql", "psycopg2", "pyodbc", "openpyxl", "jinja2"):
        cmd += ["--hidden-import", h]
    for pkg in ("sklearn", "statsmodels", "scipy", "matplotlib", "torch", "xgboost", "lightgbm", "optuna", "shap", "weasyprint"):
        cmd += ["--collect-submodules", pkg] if pkg in ("sklearn", "statsmodels") else []
    cmd += ["--collect-data", "matplotlib", "--collect-data", "sklearn", str(ROOT / "energybaseline" / "launcher.py")]
    subprocess.check_call(cmd, cwd=ROOT)
    out = dist / "EnergyBaseline"
    for item in out.iterdir():
        target = ROOT / item.name
        if target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        shutil.move(str(item), str(target))
    shutil.rmtree(dist, ignore_errors=True)
    print("Built:", ROOT / "EnergyBaseline.exe")
    subprocess.call([sys.executable, str(ROOT / "scripts" / "make_shortcut.py")])


if __name__ == "__main__":
    main()
