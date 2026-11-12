"""Positive and negative tests for ``scripts/gen_matrix.py`` (D0.5)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gen_matrix.py"


def _load_repo_rows() -> list[dict]:
    data = yaml.safe_load((ROOT / "docs" / "requirements.yaml").read_text(encoding="utf-8"))
    return data["requirements"]


def test_matrix_check_passes_on_repo() -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "OK [gen_matrix]" in res.stdout


def test_rejects_done_without_tests(tmp_path: Path) -> None:
    rows = _load_repo_rows()
    for r in rows:
        if r["id"] == "AC-10":
            r["status"] = "done"
            r["test_ids"] = []
            break
    bad_yaml = tmp_path / "requirements.yaml"
    out_md = tmp_path / "requirements_matrix.md"
    bad_yaml.write_text(yaml.safe_dump({"requirements": rows}, sort_keys=False), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--yaml", str(bad_yaml), "--out", str(out_md), "--skip-fs-refs"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "AC-10" in res.stderr
    assert "empty 'test_ids'" in res.stderr


def test_rejects_missing_mandatory_id(tmp_path: Path) -> None:
    rows = [r for r in _load_repo_rows() if r["id"] != "TCP-14"]
    bad_yaml = tmp_path / "requirements.yaml"
    out_md = tmp_path / "requirements_matrix.md"
    bad_yaml.write_text(yaml.safe_dump({"requirements": rows}, sort_keys=False), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--yaml", str(bad_yaml), "--out", str(out_md), "--skip-fs-refs"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "TCP-14" in res.stderr


def test_rejects_ac_without_phase(tmp_path: Path) -> None:
    rows = _load_repo_rows()
    for r in rows:
        if r["id"] == "AC-06":
            r["phase"] = ""
            break
    bad_yaml = tmp_path / "requirements.yaml"
    out_md = tmp_path / "requirements_matrix.md"
    bad_yaml.write_text(yaml.safe_dump({"requirements": rows}, sort_keys=False), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--yaml", str(bad_yaml), "--out", str(out_md), "--skip-fs-refs"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "AC-06" in res.stderr
