"""Phase 4 Sub-Gate G4a: Statistical Test Catalog T1..T4, shared contract TC-1..8, CI-1..4 & Monte Carlo (AC-10..12)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from energybaseline.core.tcp import DecisionRule
from energybaseline.tests_catalog import (
    ObservedPowerForbiddenError,
    TestContractResult,
    compute_prospective_design_power,
    mean_ci_with_autocorrelation_guard,
    run_t1_stationarity,
    run_t2_seasonality,
    run_t3_autocorrelation,
    run_t4_normality,
    run_test_monte_carlo,
    stationarity_decision_matrix,
)


def _make_ar1(n: int, phi: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    eps = rng.normal(0.0, 1.0, size=n)
    y = np.empty(n, dtype=float)
    y[0] = eps[0]
    for t in range(1, n):
        y[t] = phi * y[t - 1] + eps[t]
    return y


def _assert_contract_invariants(res: TestContractResult) -> None:
    d = res.to_dict()
    for key in (
        "test_id",
        "statistic",
        "p_raw",
        "p_adj",
        "df",
        "effect_size",
        "ci",
        "n_used",
        "assumptions_checked",
        "decision",
        "interpretation",
        "downstream_action",
        "mode",
        "library_versions",
    ):
        assert key in d, f"Missing contract field {key}"
    assert isinstance(d["ci"], dict) and "method" in d["ci"] and d["ci"]["method"]
    assert "numpy" in d["library_versions"] and "scipy" in d["library_versions"]
    assert "observed_power" not in str(d).lower()


def test_t1_stationarity_pass_and_fail_plus_2x2_matrix() -> None:
    rng = np.random.default_rng(101)
    y_stat = rng.normal(100.0, 2.5, size=180)
    y_rw = np.cumsum(rng.normal(0.0, 1.5, size=180))

    r_pass = run_t1_stationarity(y_stat, alpha=0.05, confidence_level=0.95, seed=101)
    r_fail = run_t1_stationarity(y_rw, alpha=0.05, confidence_level=0.95, seed=101)
    _assert_contract_invariants(r_pass)
    _assert_contract_invariants(r_fail)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert r_fail.details["matrix_cell"] in ("unit_root_nonstationary", "trend_stationary_or_structural_shift")

    # Verify all 4 cells of the 2x2 ADF/KPSS decision matrix (adf_p, kpss_p, alpha=0.05)
    assert stationarity_decision_matrix(0.01, 0.50)["cell"] == "stationary"
    assert stationarity_decision_matrix(0.50, 0.01)["cell"] == "unit_root_nonstationary"
    assert stationarity_decision_matrix(0.01, 0.01)["cell"] == "trend_stationary_or_structural_shift"
    assert stationarity_decision_matrix(0.50, 0.50)["cell"] == "inconclusive_low_power"


def test_t2_seasonality_pass_and_fail_plus_kruskal_fallback() -> None:
    rng = np.random.default_rng(202)
    t = np.arange(144, dtype=float)
    y_flat = rng.normal(50.0, 1.0, size=144)
    y_seas = 50.0 + 4.5 * np.sin(2.0 * math.pi * t / 12.0) + rng.normal(0.0, 0.8, size=144)

    r_pass = run_t2_seasonality(y_flat, period=12, alpha=0.05, seed=202)
    r_fail = run_t2_seasonality(y_seas, period=12, alpha=0.05, seed=202)
    _assert_contract_invariants(r_pass)
    _assert_contract_invariants(r_fail)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert r_fail.effect_size is not None and r_fail.effect_size > 0.30

    # Heavy-kurtosis spike triggers Kruskal-Wallis fallback
    y_kurt = y_seas.copy()
    y_kurt[5] += 40.0
    r_fb = run_t2_seasonality(y_kurt, period=12, alpha=0.05, seed=202)
    assert "kruskal" in r_fb.subtest or "kruskal" in r_fb.fallback_used


def test_t3_autocorrelation_pass_and_fail() -> None:
    rng = np.random.default_rng(304)
    u_iid = rng.normal(0.0, 1.0, size=160)
    u_ar = _make_ar1(160, phi=0.72, seed=304)

    r_pass = run_t3_autocorrelation(u_iid, lags=10, alpha=0.05, seed=304)
    r_fail = run_t3_autocorrelation(u_ar, lags=10, alpha=0.05, seed=304)
    _assert_contract_invariants(r_pass)
    _assert_contract_invariants(r_fail)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert r_fail.details["durbin_watson"] < 1.2


def test_t4_normality_pass_and_fail() -> None:
    rng = np.random.default_rng(404)
    u_norm = rng.normal(0.0, 1.0, size=150)
    u_skew = rng.exponential(scale=2.0, size=150) - 2.0

    r_pass = run_t4_normality(u_norm, alpha=0.05, seed=404)
    r_fail = run_t4_normality(u_skew, alpha=0.05, seed=404)
    _assert_contract_invariants(r_pass)
    _assert_contract_invariants(r_fail)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"


def test_naive_labelling_on_autocorrelated_series_without_hac_and_ci_levels() -> None:
    """Gate G4 Criterion #4 & CI-1..4: autocorrelated series without HAC/block-bootstrap MUST carry 'naive'."""
    ar_series = _make_ar1(180, phi=0.78, seed=55)

    ci_naive = mean_ci_with_autocorrelation_guard(ar_series, confidence_level=0.95, allow_hac=False)
    ci_hac = mean_ci_with_autocorrelation_guard(ar_series, confidence_level=0.95, allow_hac=True)
    ci_mbb = mean_ci_with_autocorrelation_guard(
        ar_series, confidence_level=0.90, allow_hac=True, prefer_block_bootstrap=True
    )
    assert ci_naive.inference_label == "naive"
    assert "naive" in ci_naive.method
    assert ci_hac.inference_label == "hac_robust"
    assert "newey_west" in ci_hac.method
    assert ci_mbb.inference_label == "block_bootstrap"

    # Verify T1..T4 mark inference_label == 'naive' when allow_hac=False on autocorrelated series
    for fn in (run_t1_stationarity, run_t2_seasonality, run_t3_autocorrelation, run_t4_normality):
        res = fn(ar_series, allow_hac=False, confidence_level=0.99, seed=55)
        assert res.inference_label == "naive"
        assert "naive" in res.ci["method"]
        assert math.isclose(res.ci["confidence_level"], 0.99)


def test_observed_power_strictly_prohibited_and_fast_subsampling_documented() -> None:
    """Gate G4 Criterion #3 & TC-8: post-hoc observed power raises error; fast mode subsamples deterministically."""
    with pytest.raises(ObservedPowerForbiddenError):
        compute_prospective_design_power(target_standardized_effect=0.5, sample_size=100, observed_p_value=0.04)

    with pytest.raises(ObservedPowerForbiddenError):
        TestContractResult(
            test_id="T1",
            name="Stationarity",
            subtest="adf",
            stage="pre_model",
            family="stationarity_trend",
            statistic=-3.5,
            p_raw=0.01,
            p_adj=0.01,
            df=100,
            effect_size=0.4,
            ci={"low": 0.1, "high": 0.7, "confidence_level": 0.95, "method": "exact_t"},
            n_used=100,
            assumptions_checked=(),
            decision="pass",
            interpretation="Stationary",
            downstream_action="proceed",
            mode="full",
            details={"observed_power": 0.62},
        )

    prospective = compute_prospective_design_power(target_standardized_effect=0.6, sample_size=120, alpha=0.05)
    assert prospective["design_type"] == "prospective_a_priori_only"
    assert 0.80 < prospective["prospective_power"] <= 1.0

    # Large-n deterministic fast subsampling (TC-8)
    big = np.random.default_rng(9).normal(0.0, 1.0, size=7500)
    r_fast_1 = run_t4_normality(big, mode="fast", seed=9)
    r_fast_2 = run_t4_normality(big, mode="fast", seed=9)
    assert r_fast_1.n_used == 5000
    assert r_fast_1.details["subsampling"]["applied"] is True
    assert r_fast_1.statistic == r_fast_2.statistic

    # Decision rule p_and_effect_size honors effect_size threshold (never ignores effect_size!)
    r_eff = run_t3_autocorrelation(
        _make_ar1(200, phi=0.28, seed=12),
        decision_rule=DecisionRule.P_AND_EFFECT_SIZE.value,
        min_effect_size=0.85,
    )
    assert r_eff.decision == "pass"


@pytest.mark.parametrize("tid", ["T1", "T2", "T3", "T4"])
def test_g4a_monte_carlo_type1_and_power(tid: str) -> None:
    mc = run_test_monte_carlo(tid, alpha=0.05, n_rep=80, sample_size=120, power_target=0.80, seed=1000)
    assert mc.type1_calibrated, f"{tid} Type I error uncalibrated: {mc.to_dict()}"
    assert mc.power_achieved, f"{tid} power below target: {mc.to_dict()}"
