"""Phase 4 Sub-Gate G4b: Statistical Test Catalog T5..T9, R references (chow, quandt_andrews, pettitt) & Monte Carlo."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from energybaseline.tests_catalog import (
    run_t5_heteroskedasticity,
    run_t6_multicollinearity,
    run_t7_chow,
    run_t8_quandt_andrews,
    run_t9_pettitt,
    run_test_monte_carlo,
)

PKG_REF_DIR = Path(__file__).resolve().parents[2] / "energybaseline" / "tests_catalog" / "r_references"


def _make_ar1(n: int, phi: float, seed: int = 21) -> np.ndarray:
    rng = np.random.default_rng(seed)
    eps = rng.normal(0.0, 1.0, size=n)
    y = np.empty(n, dtype=float)
    y[0] = eps[0]
    for i in range(1, n):
        y[i] = phi * y[i - 1] + eps[i]
    return y


def test_t5_heteroskedasticity_pass_and_fail_plus_kurtosis_fallback() -> None:
    rng = np.random.default_rng(505)
    x = np.linspace(0.0, 2.0, num=160)
    u_homo = rng.normal(0.0, 1.0, size=160)
    u_het = rng.normal(0.0, 1.0, size=160) * (0.2 + 2.5 * x)

    r_pass = run_t5_heteroskedasticity(u_homo, x, alpha=0.05, seed=505)
    r_fail = run_t5_heteroskedasticity(u_het, x, alpha=0.05, seed=505)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"

    # Heavy kurtosis triggers Koenker studentized / Spearman fallback
    u_heavy = rng.standard_t(df=2.5, size=160) * (0.3 + 2.0 * x)
    r_fb = run_t5_heteroskedasticity(u_heavy, x, alpha=0.05, seed=505)
    assert r_fb.fallback_used in ("koenker_studentized_bp", "spearman_abs_residual_rank", "koenker_studentized")


def test_t6_multicollinearity_pass_and_fail_plus_rank_vif_fallback() -> None:
    rng = np.random.default_rng(606)
    z1 = rng.normal(0.0, 1.0, size=140)
    z2 = rng.normal(0.0, 1.0, size=140)
    X_orth = np.column_stack([z1, z2])
    X_col = np.column_stack([z1, z1 + 0.05 * z2])

    r_pass = run_t6_multicollinearity(X_orth, column_names=["oat", "solar"], vif_threshold=10.0, alpha=0.05, seed=606)
    r_fail = run_t6_multicollinearity(X_col, column_names=["oat", "cdd"], vif_threshold=10.0, alpha=0.05, seed=606)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert r_fail.statistic > 100.0
    assert r_fail.details["condition_index"] > 10.0


def test_t7_chow_pass_and_fail_and_r_reference_parity() -> None:
    ref = json.loads((PKG_REF_DIR / "chow.json").read_text(encoding="utf-8"))
    c_null, c_brk = ref["cases"]

    r_pass = run_t7_chow(
        c_null["inputs"]["y"],
        c_null["inputs"]["x"],
        break_point=c_null["inputs"]["break_point"],
        use_classical_f=True,
        alpha=0.05,
    )
    r_fail = run_t7_chow(
        c_brk["inputs"]["y"],
        c_brk["inputs"]["x"],
        break_point=c_brk["inputs"]["break_point"],
        use_classical_f=True,
        alpha=0.05,
    )
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert abs(float(r_pass.statistic) - float(c_null["expected"]["statistic"])) <= 1e-8
    assert abs(float(r_fail.statistic) - float(c_brk["expected"]["statistic"])) <= 1e-8

    # Unequal regime variance automatically activates HC/HAC Wald fallback when use_classical_f=False
    rng = np.random.default_rng(707)
    y_het_reg = np.concatenate([rng.normal(0.0, 0.3, size=60), rng.normal(2.0, 3.5, size=60)])
    r_rob = run_t7_chow(y_het_reg, break_point=60, use_classical_f=False, alpha=0.05)
    assert "wald_fallback" in r_rob.subtest


def test_t8_quandt_andrews_pass_and_fail_bootstrap_p_and_r_reference() -> None:
    ref = json.loads((PKG_REF_DIR / "quandt_andrews.json").read_text(encoding="utf-8"))
    c_null, c_brk = ref["cases"]

    r_pass = run_t8_quandt_andrews(
        c_null["inputs"]["y"],
        c_null["inputs"]["x"],
        trim=0.15,
        n_boot=249,
        seed=int(ref["seed"]),
    )
    r_fail = run_t8_quandt_andrews(
        c_brk["inputs"]["y"],
        c_brk["inputs"]["x"],
        trim=0.15,
        n_boot=249,
        seed=int(ref["seed"]),
    )
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert abs(float(r_fail.statistic) - float(c_brk["expected"]["statistic"])) <= 1e-8
    assert r_fail.details["estimated_break_point"] == int(c_brk["expected"]["break_point"])
    assert "hansen_p_approx" in r_fail.details


def test_t9_pettitt_pass_and_fail_and_r_reference() -> None:
    ref = json.loads((PKG_REF_DIR / "pettitt.json").read_text(encoding="utf-8"))
    c_null, c_shift = ref["cases"]

    r_pass = run_t9_pettitt(c_null["inputs"]["y"], use_asymptotic_formula=True, alpha=0.05)
    r_fail = run_t9_pettitt(c_shift["inputs"]["y"], use_asymptotic_formula=True, alpha=0.05)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert abs(float(r_fail.statistic) - float(c_shift["expected"]["statistic"])) <= 1e-9
    assert r_fail.details["change_point_1based"] == int(c_shift["expected"]["change_point_1based"])


def test_g4b_naive_labelling_when_autocorrelated_without_hac() -> None:
    ar = _make_ar1(140, phi=0.78, seed=88)
    X_ar = np.column_stack([ar, np.roll(ar, 1)])
    assert run_t5_heteroskedasticity(ar, allow_hac=False, seed=88).inference_label == "naive"
    assert run_t6_multicollinearity(X_ar, allow_hac=False, seed=88).inference_label == "naive"
    assert run_t7_chow(ar, allow_hac=False, seed=88).inference_label == "naive"
    assert run_t8_quandt_andrews(ar, n_boot=49, allow_hac=False, seed=88).inference_label == "naive"
    assert run_t9_pettitt(ar, allow_hac=False, seed=88).inference_label == "naive"


@pytest.mark.parametrize("tid", ["T5", "T6", "T7", "T8", "T9"])
def test_g4b_monte_carlo_type1_and_power(tid: str) -> None:
    n_rep = 50 if tid == "T8" else 80
    mc = run_test_monte_carlo(tid, alpha=0.05, n_rep=n_rep, sample_size=110, power_target=0.80, seed=2000)
    assert mc.type1_calibrated, f"{tid} Type I error uncalibrated: {mc.to_dict()}"
    assert mc.power_achieved, f"{tid} power below target: {mc.to_dict()}"
