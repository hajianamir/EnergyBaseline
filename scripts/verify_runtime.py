"""Fail before tests if the active interpreter violates the shared runtime contract."""

from __future__ import annotations

from pathlib import Path
import sys
import tomllib

from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def verify_runtime():
    declared = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    actual = Version(".".join(map(str, sys.version_info[:3])))
    if declared != "3.12" or sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Expected the declared Python {declared} runtime; got {actual}")
    if actual not in SpecifierSet(project["project"]["requires-python"]):
        raise RuntimeError("The active interpreter does not satisfy requires-python")
    if project["tool"]["ruff"]["target-version"] != "py312":
        raise RuntimeError("Ruff's target must agree with the runtime contract")
    print(f"Runtime contract verified: Python {actual}, {sys.platform}")


if __name__ == "__main__":
    verify_runtime()
