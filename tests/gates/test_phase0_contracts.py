"""Phase 0 rail contracts: pinned/hashed manifests, CI matrix, process docs, hygiene, and baseline artifacts."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_manifests_exact_pins_and_hashes() -> None:
    for name in ("requirements.in", "requirements.txt", "requirements.lock"):
        p = ROOT / name
        assert p.is_file(), f"Missing {name}"
        text = p.read_text(encoding="utf-8")
        assert ("weasy" + "print") not in text.lower()
        for lineno, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("--hash="):
                continue
            assert not re.search(r"(>=|<=|~=)", s), f"Range pin in {name}:{lineno}: {s}"
            assert "==" in s, f"Unpinned entry in {name}:{lineno}: {s}"

    req_in = (ROOT / "requirements.in").read_text(encoding="utf-8")
    for expected in (
        "joblib==",
        "mapie==1.6.0",
        "arch==9.0.0",
        "sqlglot==30.31.0",
        "reportlab==5.1.0",
        "pydantic==",
        "hypothesis==",
        "pytest-qt==",
        "ruff==",
        "cyclonedx-bom==",
        "pip-audit==",
    ):
        assert expected in req_in, f"Expected {expected} in requirements.in"
    assert re.search(r"pywin32==\S+\s*;\s*sys_platform\s*==\s*['\"]win32['\"]", req_in)

    lock_lines = [
        ln.strip()
        for ln in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    pkg_count = sum(1 for ln in lock_lines if not ln.startswith("--hash="))
    hash_count = sum(1 for ln in lock_lines if ln.startswith("--hash=sha256:"))
    assert pkg_count >= 100
    assert hash_count >= pkg_count


def test_ci_workflows_structure() -> None:
    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    r_path = ROOT / ".github" / "workflows" / "r-reference.yml"
    assert ci_path.is_file() and r_path.is_file()

    ci_text = ci_path.read_text(encoding="utf-8")
    ci = yaml.safe_load(ci_text)
    jobs = ci["jobs"]
    first_job = next(iter(jobs.values()))
    os_matrix = first_job["strategy"]["matrix"]["os"]
    assert "windows-latest" in os_matrix and "ubuntu-latest" in os_matrix

    for token in (
        "3.11.9",
        "requirements.lock",
        "--require-hashes",
        "ruff check",
        "ruff format --check",
        "pyflakes energybaseline",
        "guard_web_free.py",
        "guard_env_readers.py",
        "guard_tests_integrity.py",
        "--cov=energybaseline/core",
        "--cov-fail-under=60",
        "tests/gui",
        "pip-audit",
        "cyclonedx-py",
        "sbom.json",
        "coverage.xml",
    ):
        assert token in ci_text, f"Missing '{token}' in ci.yml"

    r_wf = yaml.safe_load(r_path.read_text(encoding="utf-8"))
    triggers = r_wf.get("on") or r_wf.get(True)
    assert "schedule" in triggers and "workflow_dispatch" in triggers


def test_process_documents_present_and_english() -> None:
    required_docs = (
        "docs/deviations.md",
        "docs/assumptions.md",
        "docs/not_implemented.md",
        "docs/SUPPORT_MATRIX.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "CHANGELOG.md",
        "scripts/sign/sign_artifacts.ps1",
    )
    for rel in required_docs:
        p = ROOT / rel
        assert p.is_file() and p.stat().st_size > 200, f"Missing or empty {rel}"

    dev = (ROOT / "docs" / "deviations.md").read_text(encoding="utf-8")
    assert "PKG-3" in dev and "[S]" in dev
    assert "outside developer machines" in dev.lower()
    assert "MICEData" in dev and "acorr_ljungbox" in dev and "brunnermunzel" in dev
    assert "ReportLab" in dev and "Phase 8" in dev

    assumptions = (ROOT / "docs" / "assumptions.md").read_text(encoding="utf-8")
    for sid in ("S1", "S2", "S3", "S4", "S5", "S6", "S7"):
        assert sid in assumptions

    support = (ROOT / "docs" / "SUPPORT_MATRIX.md").read_text(encoding="utf-8")
    for item in ("3.11.9", "Windows 10 22H2", "Windows 11", "8-core", "16 GB", "1920", "torch==2.15.0"):
        assert item in support, f"Missing '{item}' in docs/SUPPORT_MATRIX.md"


def test_gitignore_and_repo_hygiene() -> None:
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        "data/",
        "logs/",
        "artifacts/**/*.db",
        "artifacts/**/*.sqlite",
        "artifacts/**/*.joblib",
        "*.joblib",
        "*.ebproj",
        "build/",
        "dist/",
        "_internal/",
        "EnergyBaseline.exe",
        "*.spec",
        "__pycache__/",
        ".venv/",
        "samples/*.xlsx",
    ):
        assert pattern in gi, f"Missing '{pattern}' in .gitignore"

    ls = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    assert not [f for f in ls if "__pycache__" in f or f.endswith(".pyc")]
    assert not [f for f in ls if f.startswith("samples/") and f.endswith(".xlsx")]


def test_evidence_bundle_and_license_intact() -> None:
    lic = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "PROPRIETARY AND CONFIDENTIAL" in lic
    assert "Copyright (c) 2026 Amir Hajian" in lic

    for base_ev in ("grep_audit.txt", "gui_smoke.txt", "library_verification.txt", "pytest.txt", "violations.txt"):
        assert (ROOT / "artifacts" / "review_baseline" / base_ev).is_file()
