"""SHA-256 integrity gate protecting baseline tests from unauthorized tampering.

Verifies every baseline test file listed in ``tests/.baseline.sha256`` against its
SHA-256 digest and confirms all mandatory baseline test files are covered.

Documented exemption:
``docs/EXECUTION_PLAN.md``, ``docs/DECISIONS.md``, ``docs/PHASE0_BRIEF.md``,
``docs/PROMPT_PHASE0_EXECUTOR.md`` and ``artifacts/**`` are non-test governance/evidence
files and are not hashed by this baseline test manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REL = "tests/.baseline.sha256"
REQUIRED_BASELINE_TESTS = (
    "tests/conftest.py",
    "tests/test_config_db.py",
    "tests/test_core.py",
    "tests/test_pipeline.py",
)


def sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(root: Path) -> Path:
    manifest = root / MANIFEST_REL
    lines = ["# SHA-256 baseline test integrity manifest (Phase 0 D0.3)"]
    for rel in REQUIRED_BASELINE_TESTS:
        target = root / rel
        if not target.is_file():
            raise FileNotFoundError(f"Missing baseline test file: {rel}")
        lines.append(f"{sha256_bytes(target)}  {rel}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def verify_manifest(root: Path) -> list[str]:
    manifest = root / MANIFEST_REL
    if not manifest.is_file():
        return [f"{MANIFEST_REL}: manifest file is missing"]

    entries: dict[str, str] = {}
    violations: list[str] = []
    for lineno, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            violations.append(f"{MANIFEST_REL}:{lineno}: malformed entry '{raw}'")
            continue
        digest, rel = parts[0].lower(), parts[1].strip().replace("\\", "/")
        if rel in entries:
            violations.append(f"{MANIFEST_REL}:{lineno}: duplicate entry for '{rel}'")
            continue
        entries[rel] = digest

    for req in REQUIRED_BASELINE_TESTS:
        if req not in entries:
            violations.append(f"{MANIFEST_REL}: mandatory baseline test '{req}' missing from manifest")

    for rel, expected in sorted(entries.items()):
        target = root / rel
        if not target.is_file():
            violations.append(f"{rel}: baseline test file was deleted or moved")
            continue
        actual = sha256_bytes(target)
        if actual != expected:
            violations.append(f"{rel}: SHA-256 mismatch (expected {expected[:12]}..., got {actual[:12]}...)")

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify SHA-256 integrity of baseline test suite.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Repository root.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    violations = verify_manifest(root)
    if violations:
        for v in violations:
            print(f"FAIL [guard_tests_integrity] {v}", file=sys.stderr)
        return 1
    print(f"OK [guard_tests_integrity] verified {len(REQUIRED_BASELINE_TESTS)} baseline test files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
