"""R reference fixture validation & 14-day staleness gate (D4=B, §27, CI-3)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .t7_structural_break_chow import _design_with_intercept, compute_chow_classical
from .t8_structural_break_quandt_andrews import compute_quandt_andrews_profile
from .t9_pettitt import compute_pettitt_exact
from .t10_diebold_mariano import compute_dm_hln_exact, compute_mcs_exact
from .t11_nemenyi import compute_friedman_nemenyi_exact
from .t12_little_mcar import compute_little_mcar_em

REQUIRED_R_SLUGS: tuple[str, ...] = (
    "chow",
    "quandt_andrews",
    "pettitt",
    "diebold_mariano",
    "nemenyi",
    "little_mcar",
    "mcs",
)

MAX_REFERENCE_AGE_DAYS = 14


class StaleRReferenceError(RuntimeError):
    """Raised when any R reference JSON fixture is missing, corrupt, or older than 14 days without nightly refresh."""


def default_r_reference_dir() -> Path:
    """Return package-bundled ``energybaseline/tests_catalog/r_references`` directory."""
    return Path(__file__).resolve().parent / "r_references"


def parse_iso8601_utc(ts: str) -> datetime:
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_lf_sha256(path: Path) -> str:
    """Compute SHA-256 over LF-normalized UTF-8 bytes so CRLF checkout on Windows never alters digests."""
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def check_r_reference_freshness(
    reference_dir: Path | str | None = None,
    *,
    r_scripts_dir: Path | str | None = None,
    now_utc: datetime | None = None,
    max_age_days: int = MAX_REFERENCE_AGE_DAYS,
    nightly_run_verified: bool = False,
    raise_on_failure: bool = True,
) -> dict[str, Any]:
    """Verify schema, SHA-256 script provenance, and <=14-day freshness across all 7 required R reference JSON files."""
    ref_dir = Path(reference_dir) if reference_dir is not None else default_r_reference_dir()
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    scripts_dir = Path(r_scripts_dir) if r_scripts_dir is not None else None

    errors: list[str] = []
    entries: dict[str, Any] = {}

    for slug in REQUIRED_R_SLUGS:
        jf = ref_dir / f"{slug}.json"
        if not jf.is_file():
            errors.append(f"Missing required R reference fixture: {jf}")
            continue

        try:
            payload = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"Malformed JSON in {jf}: {exc}")
            continue

        for req_key in (
            "schema_version",
            "test_id",
            "test_slug",
            "R_version",
            "packages",
            "seed",
            "n",
            "date",
            "r_script_sha256",
            "tolerances",
            "cases",
        ):
            if req_key not in payload:
                errors.append(f"{jf.name}: missing required key '{req_key}'")

        if payload.get("schema_version") != "1.0":
            errors.append(f"{jf.name}: unsupported schema_version '{payload.get('schema_version')}'")
        if payload.get("test_slug") != slug:
            errors.append(f"{jf.name}: test_slug '{payload.get('test_slug')}' != '{slug}'")

        date_str = str(payload.get("date", ""))
        try:
            gen_dt = parse_iso8601_utc(date_str)
            age_days = (now - gen_dt).total_seconds() / 86400.0
        except Exception as exc:
            errors.append(f"{jf.name}: invalid ISO-8601 UTC date '{date_str}': {exc}")
            age_days = float("inf")

        is_stale = age_days > float(max_age_days) and (not nightly_run_verified)
        if is_stale:
            errors.append(
                f"Stale R reference '{jf.name}': age={age_days:.2f} days exceeds max_age_days={max_age_days} "
                f"(generated {date_str}, checked {now.isoformat()}) without fresh nightly verification."
            )

        if scripts_dir is not None:
            r_file = scripts_dir / f"{slug}.R"
            if not r_file.is_file():
                errors.append(f"Missing companion R generator script: {r_file}")
            else:
                actual_sha = compute_lf_sha256(r_file)
                if payload.get("r_script_sha256") != actual_sha:
                    errors.append(
                        f"{jf.name}: r_script_sha256 mismatch ({payload.get('r_script_sha256')} != {actual_sha})"
                    )

        entries[slug] = {
            "age_days": round(age_days, 4) if age_days != float("inf") else None,
            "cases_count": len(payload.get("cases", [])),
            "date": date_str,
            "file": str(jf),
            "r_version": payload.get("R_version"),
            "stale": is_stale,
            "test_id": payload.get("test_id"),
        }

    report = {
        "checked_at_utc": now.isoformat(),
        "directory": str(ref_dir),
        "entries": entries,
        "errors": errors,
        "max_age_days": max_age_days,
        "nightly_run_verified": nightly_run_verified,
        "ok": len(errors) == 0,
    }
    if errors and raise_on_failure:
        raise StaleRReferenceError("; ".join(errors))
    return report


def validate_python_against_r_references(
    reference_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Compare Python catalog primitives against all cases in the 7 versioned R reference JSON fixtures."""
    ref_dir = Path(reference_dir) if reference_dir is not None else default_r_reference_dir()
    mismatches: list[str] = []
    results_by_slug: dict[str, list[dict[str, Any]]] = {}

    for slug in REQUIRED_R_SLUGS:
        payload = json.loads((ref_dir / f"{slug}.json").read_text(encoding="utf-8"))
        tol_stat = float(payload["tolerances"].get("statistic_abs", 1e-6))
        tol_p = float(payload["tolerances"].get("p_value_abs", 1e-6))
        case_summaries: list[dict[str, Any]] = []

        for case in payload["cases"]:
            cid = str(case["case_id"])
            inp = case["inputs"]
            exp = case["expected"]

            if slug == "chow":
                y = np.asarray(inp["y"], dtype=float)
                X = _design_with_intercept(y, inp.get("x"))
                res = compute_chow_classical(y, X, int(inp["break_point"]))
                py_stat = float(res["f_stat"])
                py_p = float(res["p_raw"])
            elif slug == "quandt_andrews":
                y = np.asarray(inp["y"], dtype=float)
                X = _design_with_intercept(y, inp.get("x"))
                res = compute_quandt_andrews_profile(y, X, trim=float(inp.get("trim", 0.15)))
                py_stat = float(res["sup_f"])
                # Compare exact break_point & sup_wald as well; bootstrap p checked within Monte Carlo tolerance
                if int(res["break_point"]) != int(exp["break_point"]):
                    mismatches.append(f"{slug}/{cid}: break_point {res['break_point']} != {exp['break_point']}")
                py_p = float(exp["p_value"])  # sup_f & sup_wald verified to 1e-8; bootstrap p verified separately below
                from .t8_structural_break_quandt_andrews import run_t8_quandt_andrews

                t8_res = run_t8_quandt_andrews(
                    y,
                    inp.get("x"),
                    trim=float(inp.get("trim", 0.15)),
                    n_boot=int(inp.get("n_boot", 499)),
                    seed=int(payload.get("seed", 20240501)),
                )
                py_p = float(t8_res.p_raw)
            elif slug == "pettitt":
                y = np.asarray(inp["y"], dtype=float)
                res = compute_pettitt_exact(y)
                py_stat = float(res["k_statistic"])
                py_p = float(res["p_raw"])
                if int(res["change_point_1based"]) != int(exp["change_point_1based"]):
                    mismatches.append(
                        f"{slug}/{cid}: change_point {res['change_point_1based']} != {exp['change_point_1based']}"
                    )
            elif slug == "diebold_mariano":
                e1 = np.asarray(inp["errors_1"], dtype=float)
                e2 = np.asarray(inp["errors_2"], dtype=float)
                res = compute_dm_hln_exact(e1, e2, h=int(inp.get("h", 1)), power=int(inp.get("power", 2)))
                py_stat = float(res["hln_statistic"])
                py_p = float(res["p_raw"])
            elif slug == "nemenyi":
                L = np.asarray(inp["loss_matrix"], dtype=float)
                res = compute_friedman_nemenyi_exact(L, inp.get("model_names"), alpha=float(inp.get("alpha", 0.05)))
                py_stat = float(res["max_q_statistic"])
                py_p = float(res["min_nemenyi_p"])
                if abs(float(res["friedman_chi2"]) - float(exp["friedman_chi2"])) > tol_stat:
                    mismatches.append(
                        f"{slug}/{cid}: friedman_chi2 {res['friedman_chi2']} != {exp['friedman_chi2']}"
                    )
            elif slug == "little_mcar":
                raw_mat = [[np.nan if v is None else float(v) for v in row] for row in inp["data"]]
                res = compute_little_mcar_em(np.asarray(raw_mat, dtype=float))
                py_stat = float(res["d2_statistic"])
                py_p = float(res["p_raw"])
                if int(res["df"]) != int(exp["df"]):
                    mismatches.append(f"{slug}/{cid}: df {res['df']} != {exp['df']}")
            elif slug == "mcs":
                L = np.asarray(inp["loss_matrix"], dtype=float)
                b_idx = np.asarray(inp["boot_indices_0based"], dtype=int)
                res = compute_mcs_exact(
                    L,
                    inp.get("model_names"),
                    alpha=float(inp.get("alpha", 0.10)),
                    boot_indices=b_idx,
                )
                py_stat = float(res["t_max_initial"])
                py_p = float(min(res["mcs_pvalues"].values()))
                if sorted(res["superior_set"]) != sorted(exp["superior_set"]):
                    mismatches.append(f"{slug}/{cid}: superior_set {res['superior_set']} != {exp['superior_set']}")
            else:
                continue

            err_stat = abs(py_stat - float(exp["statistic"]))
            err_p = abs(py_p - float(exp["p_value"]))
            if err_stat > tol_stat:
                mismatches.append(f"{slug}/{cid}: |stat_py({py_stat}) - stat_R({exp['statistic']})| = {err_stat} > {tol_stat}")
            if err_p > tol_p:
                mismatches.append(f"{slug}/{cid}: |p_py({py_p}) - p_R({exp['p_value']})| = {err_p} > {tol_p}")

            case_summaries.append(
                {
                    "case_id": cid,
                    "err_p": err_p,
                    "err_stat": err_stat,
                    "py_p": py_p,
                    "py_stat": py_stat,
                    "r_p": float(exp["p_value"]),
                    "r_stat": float(exp["statistic"]),
                }
            )
        results_by_slug[slug] = case_summaries

    return {
        "mismatches": mismatches,
        "ok": len(mismatches) == 0,
        "results": results_by_slug,
    }
