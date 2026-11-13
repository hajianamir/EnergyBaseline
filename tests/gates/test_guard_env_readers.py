"""Positive and negative tests for ``scripts/gates/guard_env_readers.py`` (D0.3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gates" / "guard_env_readers.py"


def _run_gate(target_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(target_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _seed_canonical_settings(root: Path) -> None:
    settings = root / "energybaseline" / "config" / "settings.py"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('import os\nURL = os.environ.get("DATABASE_URL", "")\n', encoding="utf-8")


def test_repo_env_readers_clean() -> None:
    res = _run_gate(ROOT)
    assert res.returncode == 0, res.stderr
    assert "OK [guard_env_readers]" in res.stdout


def test_allows_canonical_settings_reader(tmp_path: Path) -> None:
    _seed_canonical_settings(tmp_path)
    res = _run_gate(tmp_path)
    assert res.returncode == 0, res.stderr


def test_rejects_unauthorized_env_reader(tmp_path: Path) -> None:
    _seed_canonical_settings(tmp_path)
    rogue = tmp_path / "energybaseline" / "db" / "rogue.py"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_text('import os\nval = os.environ["DATABASE_URL"]\n', encoding="utf-8")

    res = _run_gate(tmp_path)
    assert res.returncode != 0
    assert "DATABASE_URL" in res.stderr
    assert "rogue.py" in res.stderr


def test_rejects_getenv_and_loop_readers(tmp_path: Path) -> None:
    _seed_canonical_settings(tmp_path)
    rogue = tmp_path / "energybaseline" / "core" / "sneaky.py"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_text(
        'import os\na = os.getenv("EB_DATABASE_URL")\n'
        'for k in ("POSTGRES_URL", "DB_URI"):\n    _ = os.environ.get(k)\n',
        encoding="utf-8",
    )

    res = _run_gate(tmp_path)
    assert res.returncode != 0
    assert "EB_DATABASE_URL" in res.stderr
    assert "POSTGRES_URL" in res.stderr
