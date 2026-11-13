"""Positive and negative tests for ``scripts/gates/guard_web_free.py`` (D0.3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gates" / "guard_web_free.py"
_BANNED_PKG = "weasy" + "print"


def _run_gate(target_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(target_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_repo_is_web_free() -> None:
    res = _run_gate(ROOT)
    assert res.returncode == 0, res.stderr
    assert "OK [guard_web_free]" in res.stdout


def test_rejects_banned_import_ast(tmp_path: Path) -> None:
    bad_py = tmp_path / "pkg" / "bad_renderer.py"
    bad_py.parent.mkdir(parents=True)
    bad_py.write_text(f"import {_BANNED_PKG}\n", encoding="utf-8")

    res = _run_gate(tmp_path)
    assert res.returncode != 0
    assert _BANNED_PKG in res.stderr


def test_rejects_webengine_and_http_imports(tmp_path: Path) -> None:
    mod = tmp_path / "ui_leak.py"
    mod.write_text("from PySide6.QtWebEngineWidgets import QWebEngineView\nimport fastapi\n", encoding="utf-8")

    res = _run_gate(tmp_path)
    assert res.returncode != 0
    assert "QtWebEngineWidgets" in res.stderr
    assert "fastapi" in res.stderr


def test_rejects_banned_manifest_dependency(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(f"numpy==2.4.6\n{_BANNED_PKG}==61.0\n", encoding="utf-8")

    res = _run_gate(tmp_path)
    assert res.returncode != 0
    assert "requirements.txt" in res.stderr


def test_rejects_tracked_web_stack_file(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "package.json").write_text('{"name": "bad"}\n', encoding="utf-8")
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "page.tsx").write_text(
        "export default function P() { return null; }\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "package.json", "src/app/page.tsx"], cwd=tmp_path, check=True)

    res = _run_gate(tmp_path)
    assert res.returncode != 0
    assert "package.json" in res.stderr
    assert "page.tsx" in res.stderr
