"""Phase 1 gate tests: path guard (P1.4), static command allowlist (P1.5), QThread gate (P1.1), and SBOM (P1.8)."""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

from energybaseline.core.pool import WORKER_COMMAND_REGISTRY
from energybaseline.gui.commands import STATIC_UI_COMMAND_SPECS, CommandRegistry, UnknownCommandError
from energybaseline.io.paths import (
    PathGuardError,
    resolve_app_home,
    resolve_picked_path,
    validate_path,
)
from scripts.build_exe import generate_sbom
from scripts.gates import guard_no_qthread_compute

REPO_ROOT = Path(__file__).resolve().parents[2]


# ===================================================================== P1.4 / AC-04 Path Guard
def test_validate_path_accepts_inside_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    sample = allowed / "sub" / "dataset.csv"
    sample.parent.mkdir()
    sample.write_text("ts,kwh\n2024-01-01,10\n", encoding="utf-8")

    got = validate_path(sample, allowed_roots=[allowed], must_exist=True)
    assert got.is_absolute()
    assert got == sample.resolve()


@pytest.mark.parametrize(
    "bad_input",
    [
        "../secret.csv",
        "sub/../../outside.csv",
        r"sub\..\..\outside.csv",
        r"\\evilserver\share\data.csv",
        "//evilserver/share/data.csv",
        r"\\?\UNC\evilserver\share\data.csv",
    ],
)
def test_validate_path_rejects_traversal_and_unc(tmp_path: Path, bad_input: str) -> None:
    with pytest.raises(PathGuardError):
        validate_path(bad_input, allowed_roots=[tmp_path])


def test_validate_path_rejects_outside_allowed_roots(tmp_path: Path) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    outside = root_b / "other.csv"
    outside.write_text("x\n1\n", encoding="utf-8")

    with pytest.raises(PathGuardError, match="outside allowed roots"):
        validate_path(outside, allowed_roots=[root_a], must_exist=True)


def test_validate_path_rejects_leaf_and_intermediate_symlinks(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    secret_dir = tmp_path / "secret"
    allowed.mkdir()
    secret_dir.mkdir()
    real_file = secret_dir / "target.csv"
    real_file.write_text("a,b\n1,2\n", encoding="utf-8")

    leaf_link = allowed / "linked.csv"
    dir_link = allowed / "linked_dir"
    try:
        os.symlink(real_file, leaf_link)
        os.symlink(secret_dir, dir_link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"OS symlink privilege unavailable in this environment: {exc}")

    with pytest.raises(PathGuardError, match="Symbolic link"):
        resolve_picked_path(leaf_link, allowed_roots=[allowed, secret_dir])

    with pytest.raises(PathGuardError, match="Symbolic link"):
        resolve_picked_path(dir_link / "target.csv", allowed_roots=[allowed, secret_dir])


def test_resolve_app_home_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Explicit EB_HOME wins
    eb_dir = tmp_path / "custom_eb_home"
    eb_dir.mkdir()
    monkeypatch.setenv("EB_HOME", str(eb_dir))
    p1, mode1 = resolve_app_home()
    assert p1 == eb_dir.resolve()
    assert mode1 == "eb_home"

    # 2. portable.flag beside frozen executable wins when EB_HOME is unset
    monkeypatch.delenv("EB_HOME", raising=False)
    fake_exe_dir = tmp_path / "portable_dist"
    fake_exe_dir.mkdir()
    fake_exe = fake_exe_dir / "EnergyBaseline.exe"
    fake_exe.write_bytes(b"MZ")
    (fake_exe_dir / "portable.flag").write_text("portable\n", encoding="utf-8")

    p2, mode2 = resolve_app_home(frozen=True, executable=fake_exe)
    assert p2 == fake_exe_dir.resolve()
    assert mode2 == "portable"

    # 3. Windows %LOCALAPPDATA%\EnergyBaseline when LOCALAPPDATA is set without portable.flag
    local_app = tmp_path / "LocalAppData"
    local_app.mkdir()
    p3, mode3 = resolve_app_home(
        frozen=False,
        env={"LOCALAPPDATA": str(local_app)},
    )
    assert p3 == (local_app / "EnergyBaseline").resolve()
    assert mode3 == "localappdata"


# ===================================================================== P1.5 / SCR-7 Static Command Allowlist
def test_command_registries_are_compile_time_immutable_and_reject_unknown() -> None:
    assert isinstance(STATIC_UI_COMMAND_SPECS, MappingProxyType)
    assert isinstance(WORKER_COMMAND_REGISTRY, MappingProxyType)

    with pytest.raises(TypeError):
        STATIC_UI_COMMAND_SPECS["evil.inject"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        WORKER_COMMAND_REGISTRY["evil.inject"] = None  # type: ignore[index]

    # Missing bindings rejected at startup
    with pytest.raises(ValueError, match="Missing required startup command binding"):
        CommandRegistry({"nav.setup": lambda: None})

    # Unknown binding rejected at startup
    full_bindings = {cid: (lambda: cid) for cid in STATIC_UI_COMMAND_SPECS}
    with pytest.raises(UnknownCommandError):
        CommandRegistry({**full_bindings, "shell.exec": lambda: None})

    reg = CommandRegistry(full_bindings)
    assert reg.dispatch("nav.setup") is not None
    with pytest.raises(UnknownCommandError):
        reg.dispatch("__import__('os').system('calc')")


def test_no_eval_exec_or_getattr_in_command_dispatchers() -> None:
    for rel in ("energybaseline/gui/commands.py", "energybaseline/core/pool.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel)
        called_names = {
            node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "eval" not in called_names, f"{rel} must never call eval()"
        assert "exec" not in called_names, f"{rel} must never call exec()"
        if rel == "energybaseline/gui/commands.py":
            assert "getattr" not in called_names, "commands.py must never dispatch via getattr()"


# ===================================================================== Gate G1 #4 guard_no_qthread_compute
def test_guard_no_qthread_compute_passes_repo_and_catches_synthetic(tmp_path: Path) -> None:
    assert guard_no_qthread_compute.run_gate(REPO_ROOT) == []

    # Synthetic violation 1: QThread in pages_setup.py
    pkg = tmp_path / "energybaseline" / "gui"
    pkg.mkdir(parents=True)
    (pkg / "pages_setup.py").write_text(
        "from PySide6.QtCore import QThread\nclass Bad(QThread):\n    pass\n",
        encoding="utf-8",
    )
    v1 = guard_no_qthread_compute.run_gate(tmp_path)
    assert any("pages_setup.py" in v for v in v1)

    # Synthetic violation 2: FuncWorker dispatching run_analysis in another GUI module
    (pkg / "pages_setup.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "other_page.py").write_text(
        "from .widgets import FuncWorker\nfrom ..core.pipeline import run_analysis\nw = FuncWorker(run_analysis)\n",
        encoding="utf-8",
    )
    v2 = guard_no_qthread_compute.run_gate(tmp_path)
    assert any("FuncWorker dispatches compute callable" in v for v in v2)


# ===================================================================== P1.8 / PKG-6 SBOM Generation
def test_cyclonedx_sbom_generation(tmp_path: Path) -> None:
    sbom_file = tmp_path / "sbom.cdx.json"
    out = generate_sbom(sbom_file)
    assert out.is_file()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc.get("bomFormat") == "CycloneDX"
    assert isinstance(doc.get("components"), list)
    assert len(doc["components"]) >= 20
    names = {c.get("name", "").lower() for c in doc["components"]}
    assert "sqlalchemy" in names or "pyside6" in names
    _ = sys
