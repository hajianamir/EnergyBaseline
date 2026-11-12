"""Positive and negative tests for ``scripts/gates/guard_tests_integrity.py`` (D0.3)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gates" / "guard_tests_integrity.py"
BASELINE_FILES = (
    "conftest.py",
    "test_config_db.py",
    "test_core.py",
    "test_pipeline.py",
    ".baseline.sha256",
)


def _run_gate(target_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(target_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _copy_baseline_tests(dst_root: Path) -> Path:
    tests_dir = dst_root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    for name in BASELINE_FILES:
        shutil.copyfile(ROOT / "tests" / name, tests_dir / name)
    return tests_dir


def test_repo_baseline_tests_intact() -> None:
    res = _run_gate(ROOT)
    assert res.returncode == 0, res.stderr
    assert "OK [guard_tests_integrity]" in res.stdout


def test_rejects_one_byte_test_tampering(tmp_path: Path) -> None:
    tests_dir = _copy_baseline_tests(tmp_path)
    victim = tests_dir / "test_core.py"
    raw = bytearray(victim.read_bytes())
    raw[-1] = ord("#") if raw[-1] != ord("#") else ord("\n")
    victim.write_bytes(bytes(raw))

    res = _run_gate(tmp_path)
    assert res.returncode != 0
    assert "SHA-256 mismatch" in res.stderr
    assert "tests/test_core.py" in res.stderr


def test_rejects_deleted_baseline_test(tmp_path: Path) -> None:
    tests_dir = _copy_baseline_tests(tmp_path)
    (tests_dir / "test_config_db.py").unlink()

    res = _run_gate(tmp_path)
    assert res.returncode != 0
    assert "tests/test_config_db.py" in res.stderr
