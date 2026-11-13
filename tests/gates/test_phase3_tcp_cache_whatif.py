"""Phase 3 Gate G3 tests: TCP-1..14, five corrections (raw+adjusted p), 6-part Cache, and 0-engine-call What-If."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from energybaseline.core import store
from energybaseline.core.cache import CacheKey, CachedComplianceRunner, ResultCache
from energybaseline.core.integrity import NON_DISABLEABLE_RULES
from energybaseline.core.schema import SchemaIntegrityError
from energybaseline.core.tcp import (
    ALPHA_PRESETS,
    DecisionRule,
    ExpectedSign,
    FamilyDefinition,
    GateRole,
    MultipleTestingCorrection,
    PreregistrationLockedError,
    TCPGuardRailError,
    TCPValidationError,
    TestActionPolicy,
    TestConfigPanel,
    adjust_p_values,
    compute_impact_preview,
    compute_target_characterization_profile,
)
from energybaseline.core.vsg import VariableSelectionGate
from energybaseline.db.session import session_scope


def _make_borderline_signal_df(n: int = 96, seed: int = 314) -> pd.DataFrame:
    """Create deterministic dataset with moderate/borderline predictor effects so What-If flips decisions."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    x_strong = rng.normal(0.0, 1.0, size=n)
    x_moderate = rng.normal(0.0, 1.0, size=n)
    x_borderline = rng.normal(0.0, 1.0, size=n)
    x_weak = rng.normal(0.0, 1.0, size=n)

    # Planted effects calibrated so p_raw spans ~0.0001, ~0.015, ~0.038, ~0.40
    noise = rng.normal(0.0, 1.0, size=n)
    y1 = 100.0 + 0.65 * x_strong + 0.26 * x_moderate + 0.21 * x_borderline + 0.04 * x_weak + noise
    y2 = 60.0 + 0.55 * x_strong - 0.24 * x_moderate + rng.normal(0.0, 1.0, size=n)

    return pd.DataFrame(
        {
            "Timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Elec_kWh": y1,
            "Gas_m3": y2,
            "Prod_Strong": x_strong,
            "Temp_Moderate": x_moderate,
            "Occup_Borderline": x_borderline,
            "Wind_Weak": x_weak,
        }
    )


# ===================================================================== TCP-1: Alpha bounds, presets, warning > 0.10, confidence
def test_tcp_alpha_bounds_presets_warning_and_confidence_modes() -> None:
    df = _make_borderline_signal_df(64)
    vsg = VariableSelectionGate(df, targets=["Elec_kWh"], time_col="Timestamp", dataset_sha256="tcp1_sha")
    tcp = TestConfigPanel(vsg)

    for preset_alpha in ALPHA_PRESETS:
        tcp.set_alpha(preset_alpha)
        assert tcp.alpha == pytest.approx(preset_alpha)
        if preset_alpha > 0.10:
            assert any(w["code"] == "ALPHA_ABOVE_0_10" for w in tcp.warnings)
        else:
            assert not any(w["code"] == "ALPHA_ABOVE_0_10" for w in tcp.warnings)

    for bad_alpha in (0.0, 0.0005, -0.05, 0.21, 0.50, float("nan"), float("inf")):
        with pytest.raises(TCPValidationError):
            tcp.set_alpha(bad_alpha)

    # Confidence presets 80/90/95/99 + custom
    for p_str, expected in (("80", 0.80), ("90", 0.90), ("95", 0.95), ("99", 0.99)):
        lv = tcp.set_confidence(p_str)
        assert lv == pytest.approx(expected)
        assert tcp.confidence_preset == p_str

    lv_custom = tcp.set_confidence("custom", 97.5)
    assert lv_custom == pytest.approx(0.975)
    assert tcp.confidence_preset == "custom"

    with pytest.raises(TCPValidationError):
        tcp.set_confidence("custom", 0.40)
    with pytest.raises(TCPValidationError):
        tcp.set_confidence("custom", 100.0)


# ===================================================================== Gate G3 #2 / TCP-2: Five corrections store BOTH raw & adjusted p
def test_tcp_five_multiple_testing_corrections_exact_values_and_dual_storage() -> None:
    raw = [0.004, 0.012, 0.039, 0.048, 0.220]

    r_none = adjust_p_values(raw, MultipleTestingCorrection.NONE)
    assert [row["p_raw"] for row in r_none] == pytest.approx(raw)
    assert [row["p_adj"] for row in r_none] == pytest.approx(raw)

    r_bonf = adjust_p_values(raw, MultipleTestingCorrection.BONFERRONI)
    assert [row["p_raw"] for row in r_bonf] == pytest.approx(raw)
    assert [row["p_adj"] for row in r_bonf] == pytest.approx([0.020, 0.060, 0.195, 0.240, 1.000])

    r_holm = adjust_p_values(raw, MultipleTestingCorrection.HOLM)
    # m=5: [5*0.004=0.020, max(0.02, 4*0.012)=0.048, max(0.048, 3*0.039)=0.117, max(0.117, 2*0.048)=0.117, 0.220]
    assert [row["p_raw"] for row in r_holm] == pytest.approx(raw)
    assert [row["p_adj"] for row in r_holm] == pytest.approx([0.020, 0.048, 0.117, 0.117, 0.220])

    r_bh = adjust_p_values(raw, MultipleTestingCorrection.BENJAMINI_HOCHBERG)
    # scaled=(5/i)*p: [0.020, 0.030, 0.065, 0.060, 0.220] -> step-up cummin from right: [0.020, 0.030, 0.060, 0.060, 0.220]
    assert [row["p_raw"] for row in r_bh] == pytest.approx(raw)
    assert [row["p_adj"] for row in r_bh] == pytest.approx([0.020, 0.030, 0.060, 0.060, 0.220])

    r_by = adjust_p_values(raw, MultipleTestingCorrection.BENJAMINI_YEKUTIELI)
    c5 = 1.0 + 0.5 + 1.0 / 3.0 + 0.25 + 0.2
    expected_by = [min(1.0, x * c5) for x in [0.020, 0.030, 0.060, 0.060, 0.220]]
    assert [row["p_raw"] for row in r_by] == pytest.approx(raw)
    assert [row["p_adj"] for row in r_by] == pytest.approx(expected_by)


# ===================================================================== TCP-3, TCP-4, TCP-5, VSG-3: Scope, guard rails, lock SHA-256, ImpactPreview
def test_tcp_scope_matrix_guard_rails_lock_immutability_and_impact_preview() -> None:
    df = _make_borderline_signal_df(80)
    vsg1 = VariableSelectionGate(df, targets=["Elec_kWh", "Gas_m3"], time_col="Timestamp", dataset_sha256="lock_sha")
    vsg1.override_variable("Elec_kWh", "Wind_Weak", "exclude", reason="excluded weak wind sensor")
    vsg1.accept()
    tcp1 = TestConfigPanel(vsg1)

    # Impact preview (VSG-3)
    preview = compute_impact_preview(vsg1, tcp1)
    assert preview["per_target_variables"]["Elec_kWh"]["included"] == 3
    assert preview["per_target_variables"]["Elec_kWh"]["excluded"] >= 2  # Gas_m3 + Wind_Weak
    assert preview["total_tests"] > 0
    assert preview["family_count"] >= 1
    assert preview["fast_vs_full"]["full_estimated_seconds"] >= preview["fast_vs_full"]["fast_estimated_seconds"]

    # Scope matrix respects VSG exclusions and blocks enabling excluded variable
    scope = tcp1.build_scope_matrix("Elec_kWh")
    assert scope["Wind_Weak"]["stationarity_trend"]["in_scope"] is False
    assert scope["Prod_Strong"]["stationarity_trend"]["in_scope"] is True
    with pytest.raises(TCPGuardRailError):
        tcp1.set_scope_cell("Elec_kWh", "Wind_Weak", "stationarity_trend", True)

    # Guard rails: cannot reconfigure non-disableable integrity rules or assign passive policy to hard_gate
    for irule in NON_DISABLEABLE_RULES:
        with pytest.raises(TCPGuardRailError):
            tcp1.configure_test_panel(irule, enabled=False)
    with pytest.raises(TCPGuardRailError):
        tcp1.configure_test_panel(
            "T1",
            gate_role=GateRole.HARD_GATE,
            action_policy=TestActionPolicy.FLAG_IN_REPORT,
        )
    with pytest.raises(TCPGuardRailError):
        tcp1.set_decision_rule(DecisionRule.P_AND_EFFECT_SIZE, min_effect_size=0.0)

    # Deterministic SHA-256 pre-registration lock across two independent instances
    vsg2 = VariableSelectionGate(df, targets=["Elec_kWh", "Gas_m3"], time_col="Timestamp", dataset_sha256="lock_sha")
    vsg2.override_variable("Elec_kWh", "Wind_Weak", "exclude", reason="excluded weak wind sensor")
    vsg2.accept()
    tcp2 = TestConfigPanel(vsg2)

    lock1 = tcp1.lock_preregistration()
    lock2 = tcp2.lock_preregistration()
    assert len(lock1) == 64
    assert lock1 == lock2

    # Mutation while locked raises PreregistrationLockedError
    with pytest.raises(PreregistrationLockedError):
        tcp1.set_alpha(0.01)
    with pytest.raises(PreregistrationLockedError):
        tcp1.set_correction(MultipleTestingCorrection.BONFERRONI)

    # Unlocking transitions provenance phase to post_test
    tcp1.unlock_for_amendment(reason="Reviewer requested stricter Bonferroni check")
    assert tcp1.phase == "post_test"
    tcp1.set_correction(MultipleTestingCorrection.BONFERRONI)
    assert vsg1.provenance.has_post_test_modifications() is True


# ===================================================================== Gate G3 #3 / CACHE-1, CACHE-2: Selective invalidation & 0-call What-If
def test_cache_selective_invalidation_and_zero_recomputation_what_if() -> None:
    df = _make_borderline_signal_df(96, seed=314)
    vsg = VariableSelectionGate(
        df,
        targets=["Elec_kWh", "Gas_m3"],
        time_col="Timestamp",
        dataset_sha256="cache_sha_01",
    )
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    tcp.set_alpha(0.05)
    tcp.set_correction(MultipleTestingCorrection.NONE)
    tcp.set_decision_rule(DecisionRule.P_ONLY)
    tcp.set_family_definition(FamilyDefinition.GLOBAL)
    tcp.lock_preregistration()

    cache = ResultCache()
    runner = CachedComplianceRunner(cache=cache, seed=42)

    # 1. Initial execution populates cache and stores BOTH p_raw and p_adj
    initial_results = runner.execute_tests(df, vsg, tcp)
    initial_calls = runner.engine_call_count
    assert initial_calls >= 10
    assert all(0.0 <= r.p_raw <= r.p_adj <= 1.0 for r in initial_results)

    # 2. Re-running identical configuration hits cache 100% -> 0 additional engine calls!
    repeat_results = runner.execute_tests(df, vsg, tcp)
    assert runner.engine_call_count == initial_calls
    assert [r.to_dict() for r in repeat_results] == [r.to_dict() for r in initial_results]

    # 3. What-If changing alpha, correction, decision_rule, expected_signs, and gate_role flips decisions
    #    with STRICTLY ZERO engine calls!
    strict_probe = tcp.clone_for_what_if(
        alpha=0.005,
        correction=MultipleTestingCorrection.BONFERRONI,
        decision_rule=DecisionRule.P_AND_EFFECT_SIZE,
        min_effect_size=0.45,
        expected_signs={"Elec_kWh": {"Prod_Strong": ExpectedSign.NEGATIVE.value}},
        gate_roles={"T8": GateRole.HARD_GATE.value},
        action_policies={"T8": TestActionPolicy.BLOCK_CANDIDATE.value},
    )
    wi_out = runner.what_if(strict_probe)
    assert wi_out["engine_calls_delta"] == 0
    assert runner.engine_call_count == initial_calls
    assert wi_out["flip_count"] >= 1
    assert any(r["sign_conflict"] is True for r in wi_out["evaluated_results"])
    assert all("p_raw" in r and "p_adj" in r for r in wi_out["evaluated_results"])

    # 4. Full 30-scenario Decision-Sensitivity Table also runs with ZERO engine calls!
    sens = runner.decision_sensitivity_table(tcp)
    assert sens["engine_calls_delta"] == 0
    assert runner.engine_call_count == initial_calls
    assert len(sens["scenarios"]) == 3 * 5 * 2

    # 5. Selective invalidation (AC-23): changing parameters ONLY on T2 recomputes T2 per target (2 calls),
    #    keeping every other test cached!
    tcp_mod_t2 = TestConfigPanel(vsg)
    tcp_mod_t2.configure_test_panel("T2", parameters={"max_lags": 5}, reason="change T2 lag")
    tcp_mod_t2.accept()
    calls_before_t2 = runner.engine_call_count
    runner.execute_tests(df, vsg, tcp_mod_t2)
    assert runner.engine_call_count - calls_before_t2 == 2  # Only T2 on Elec_kWh and Gas_m3 recomputed!

    # 6. Selective invalidation across targets (MT-2): modifying Elec_kWh variable_set does NOT recompute Gas_m3!
    vsg_t1_change = VariableSelectionGate(
        df,
        targets=["Elec_kWh", "Gas_m3"],
        time_col="Timestamp",
        dataset_sha256="cache_sha_01",
    )
    vsg_t1_change.override_variable("Elec_kWh", "Wind_Weak", "exclude", reason="drop Wind_Weak on Elec_kWh only")
    vsg_t1_change.accept()
    tcp_t1_change = TestConfigPanel(vsg_t1_change)
    tcp_t1_change.accept()

    calls_before_t1_change = runner.engine_call_count
    runner.execute_tests(df, vsg_t1_change, tcp_t1_change)
    delta_t1 = runner.engine_call_count - calls_before_t1_change
    # Only multi-variable test T8 on Elec_kWh depends on Elec_kWh's full variable_set tuple;
    # Gas_m3 and all retained single-variable tests hit the cache (0 recomputations -> delta == 1)!
    assert delta_t1 == 1


# ===================================================================== TCP-1..14 characterization & SQLite persistence tamper checks
def test_tcp_14d_characterization_and_sqlite_persistence_tamper_guards(eb_home: Path) -> None:
    import energybaseline.db.session as ses

    ses.reset_engine()
    ses.init_database()

    df = _make_borderline_signal_df(96)
    char = compute_target_characterization_profile(
        df,
        target="Elec_kWh",
        predictors=["Prod_Strong", "Temp_Moderate", "Occup_Borderline"],
        time_col="Timestamp",
        dataset_sha256="char_sha",
    )
    assert len(char["dimensions"]) == 14
    assert len(char["profile_sha256"]) == 64

    vsg = VariableSelectionGate(df, targets=["Elec_kWh"], time_col="Timestamp", dataset_sha256="char_sha")
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    tcp.lock_preregistration()

    vid = store.save_vsg_state(vsg.to_dict())
    tid = store.save_tcp_config(tcp.to_dict(), vsg_id=vid, characterization=char)

    ck = CacheKey.build(data_hash="char_sha", variable_set=["Elec_kWh", "Prod_Strong"], test_id="T1", seed=42)
    store.save_cache_artifact(ck.to_dict(), ck.key_sha256, {"p_raw": 0.012, "statistic": -3.4})
    assert store.load_cache_artifact(ck.key_sha256)["p_raw"] == pytest.approx(0.012)

    loaded_tcp = store.load_tcp_config(tid)
    assert loaded_tcp["preregistration_sha256"] == tcp.preregistration_sha256
    assert len(loaded_tcp["characterization"]["dimensions"]) == 14

    # Corrupting persisted SQLite JSON triggers SchemaIntegrityError
    with session_scope() as s:
        s.execute(
            text("UPDATE tcp_configs SET config_json = :val WHERE id = :id"),
            {"val": '{"alpha":0.19}', "id": tid},
        )
    with pytest.raises(SchemaIntegrityError):
        store.load_tcp_config(tid)
