"""Staleness gate (<=14 days), SHA-256 provenance, mirror parity & Python-vs-R validation for R reference fixtures (D4=B, CI-3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil

import pytest

from energybaseline.cli import main as cli_main
from energybaseline.tests_catalog.r_reference_validator import (
    REQUIRED_R_SLUGS,
    StaleRReferenceError,
    check_r_reference_freshness,
    parse_iso8601_utc,
    validate_python_against_r_references,
)

ROOT = Path(__file__).resolve().parents[1]
TESTS_REF_DIR = ROOT / "tests" / "reference"
PKG_REF_DIR = ROOT / "energybaseline" / "tests_catalog" / "r_references"
TOP_REF_DIR = ROOT / "tests_catalog" / "r_references"


def test_all_seven_r_reference_bundles_exist_and_match_byte_for_byte() -> None:
    assert len(REQUIRED_R_SLUGS) == 7
    for slug in REQUIRED_R_SLUGS:
        r_script = TESTS_REF_DIR / f"{slug}.R"
        j_ref = TESTS_REF_DIR / f"{slug}.json"
        j_pkg = PKG_REF_DIR / f"{slug}.json"
        j_top = TOP_REF_DIR / f"{slug}.json"
        assert r_script.is_file(), f"Missing R generator script {r_script}"
        assert j_ref.is_file(), f"Missing reference JSON {j_ref}"
        assert j_pkg.is_file(), f"Missing package reference JSON {j_pkg}"
        assert j_top.is_file(), f"Missing top-level tests_catalog reference JSON {j_top}"
        b_ref = j_ref.read_bytes()
        assert b_ref == j_pkg.read_bytes(), f"Mirror drift between {j_ref} and {j_pkg}"
        assert b_ref == j_top.read_bytes(), f"Mirror drift between {j_ref} and {j_top}"


def test_r_reference_freshness_passes_within_14_day_window() -> None:
    sample = json.loads((PKG_REF_DIR / "chow.json").read_text(encoding="utf-8"))
    gen_dt = parse_iso8601_utc(sample["date"])
    rep = check_r_reference_freshness(
        PKG_REF_DIR,
        r_scripts_dir=TESTS_REF_DIR,
        now_utc=gen_dt + timedelta(days=13, hours=23),
        max_age_days=14,
        raise_on_failure=True,
    )
    assert rep["ok"] is True
    assert len(rep["entries"]) == 7
    assert all(not item["stale"] for item in rep["entries"].values())


def test_stale_r_reference_older_than_14_days_fails_gate(tmp_path: Path) -> None:
    """Gate G4 Rejection Criterion #5: R reference JSON > 14 days old without nightly run MUST fail."""
    stale_dir = tmp_path / "stale_refs"
    stale_dir.mkdir(parents=True, exist_ok=True)
    for slug in REQUIRED_R_SLUGS:
        shutil.copy2(PKG_REF_DIR / f"{slug}.json", stale_dir / f"{slug}.json")

    # Age pettitt.json to 15 days before evaluation timestamp
    pettitt_path = stale_dir / "pettitt.json"
    payload = json.loads(pettitt_path.read_text(encoding="utf-8"))
    now_dt = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
    payload["date"] = (now_dt - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pettitt_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(StaleRReferenceError, match="Stale R reference 'pettitt.json'"):
        check_r_reference_freshness(stale_dir, now_utc=now_dt, max_age_days=14, nightly_run_verified=False)

    # Verify CLI r-references-check also fails with non-zero exit code on stale directory
    rc_stale = cli_main(["r-references-check", "--reference-dir", str(stale_dir), "--max-age-days", "14"])
    assert rc_stale == 2

    # When nightly_run_verified=True is explicitly attested, report records non-stale override
    rep_nightly = check_r_reference_freshness(
        stale_dir,
        now_utc=now_dt,
        max_age_days=14,
        nightly_run_verified=True,
        raise_on_failure=True,
    )
    assert rep_nightly["ok"] is True


def test_r_script_sha256_tamper_detection_fails(tmp_path: Path) -> None:
    tampered_dir = tmp_path / "tampered_refs"
    tampered_dir.mkdir(parents=True, exist_ok=True)
    for slug in REQUIRED_R_SLUGS:
        shutil.copy2(PKG_REF_DIR / f"{slug}.json", tampered_dir / f"{slug}.json")

    chow_file = tampered_dir / "chow.json"
    doc = json.loads(chow_file.read_text(encoding="utf-8"))
    doc["r_script_sha256"] = "0" * 64
    chow_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(StaleRReferenceError, match="r_script_sha256 mismatch"):
        check_r_reference_freshness(tampered_dir, r_scripts_dir=TESTS_REF_DIR)


def test_python_implementations_match_all_seven_r_reference_fixtures() -> None:
    parity = validate_python_against_r_references(PKG_REF_DIR)
    assert parity["ok"] is True, f"R reference parity failures: {parity['mismatches']}"
    for slug in REQUIRED_R_SLUGS:
        assert len(parity["results"][slug]) >= 2
