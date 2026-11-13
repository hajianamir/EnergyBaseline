"""Phase 3 Gate G3 tests: VSG, PIPE-1 gate enforcement, O(n) non-disableable integrity, and provenance separation."""

from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import pytest

from energybaseline.core import store
from energybaseline.core.cache import CachedComplianceRunner
from energybaseline.core.integrity import (
    DEFAULT_INTEGRITY_ENGINE,
    NON_DISABLEABLE_RULES,
    RULE_BYPASS_BLOCKED,
    RULE_DST_DUPLICATE_TIMESTAMPS,
    RULE_TARGET_AS_PREDICTOR,
    RULE_TARGET_LEAKAGE_MT2,
    RULE_TIME_ORDER_VIOLATION,
    IntegrityBypassError,
    IntegrityViolationError,
    run_integrity_checks,
)
from energybaseline.core.pipeline import run_analysis, run_gated_inferential_stage
from energybaseline.core.provenance import (
    ProvenanceLedger,
    ProvenancePhase,
    ProvenanceSource,
    ProvenanceValidationError,
)
from energybaseline.core.schema import Availability, ColumnRole, suggest_dataset_schema
from energybaseline.core.tcp import TestConfigPanel
from energybaseline.core.vsg import (
    VALID_PRESETS,
    ComplianceGateError,
    VSGValidationError,
    VariableDisposition,
    VariableSelectionGate,
    pre_test_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_compliance_df(n: int = 120, seed: int = 123) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    temp_c = rng.uniform(-2.0, 34.0, size=n)
    # V-shaped HVAC load centered at 18C -> near-zero linear r, strong nonlinear MI!
    v_load = 12.0 * np.abs(temp_c - 18.0)
    prod_ton = rng.normal(100.0, 15.0, size=n)
    energy_kwh = 500.0 + v_load + 3.2 * prod_ton + rng.normal(0.0, 6.0, size=n)
    steam_kg = 250.0 + 2.1 * prod_ton + rng.normal(0.0, 5.0, size=n)
    temp_f_collinear = 1.8 * temp_c + 32.0 + rng.normal(0.0, 0.02, size=n)
    high_missing = rng.normal(50.0, 5.0, size=n)
    high_missing[: int(0.65 * n)] = np.nan
    const_col = np.full(n, 7.0)
    near_const = 1000.0 + rng.normal(0.0, 1e-7, size=n)
    leaked_meter_kwh = 0.5 * energy_kwh + 10.0

    return pd.DataFrame(
        {
            "Timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Energy_kWh": energy_kwh,
            "Steam_kg": steam_kg,
            "Temp_C": temp_c,
            "Temp_F_Collinear": temp_f_collinear,
            "Production_ton": prod_ton,
            "High_Missing_Sensor": high_missing,
            "Constant_Flag": const_col,
            "Near_Constant_Sensor": near_const,
            "Submeter_Leak_kWh": leaked_meter_kwh,
            "Shift_Mode": np.where(np.arange(n) % 2 == 0, "day", "night"),
        }
    )


# ===================================================================== Gate G3 #1: No tests before VSG/TCP acceptance
def test_pipe1_blocks_inferential_tests_before_vsg_and_tcp_acceptance() -> None:
    df = _make_compliance_df(80)
    vsg = VariableSelectionGate(df, targets=["Energy_kWh"], time_col="Timestamp", dataset_sha256="abc123")
    tcp = TestConfigPanel(vsg)
    runner = CachedComplianceRunner(seed=7)

    # 1. Neither VSG nor TCP accepted -> raises ComplianceGateError with 0 engine calls
    with pytest.raises(ComplianceGateError, match="VSG"):
        runner.execute_tests(df, vsg, tcp)
    assert runner.engine_call_count == 0

    with pytest.raises(ComplianceGateError):
        run_gated_inferential_stage(df, vsg, tcp)

    with pytest.raises(ComplianceGateError):
        run_analysis(df, "Timestamp", ["Energy_kWh"], {"hpo_trials": "0"}, vsg=vsg, tcp=tcp)

    with pytest.raises(ComplianceGateError):
        run_analysis(df, "Timestamp", ["Energy_kWh"], {"hpo_trials": "0"}, enforce_compliance_gates=True)

    # 2. VSG accepted, TCP NOT accepted -> still raises ComplianceGateError with 0 engine calls
    vsg.accept()
    assert vsg.is_accepted()
    assert not tcp.is_accepted()
    with pytest.raises(ComplianceGateError, match="TCP"):
        runner.execute_tests(df, vsg, tcp)
    assert runner.engine_call_count == 0

    # 3. Both VSG and TCP accepted -> inferential tests execute cleanly
    tcp.accept()
    assert tcp.is_accepted()
    evaluated = runner.execute_tests(df, vsg, tcp)
    assert len(evaluated) > 0
    assert runner.engine_call_count > 0


# ===================================================================== VSG-2, VSG-5, VSG-7, VSG-8: Pre-test profile, MI, VIF
def test_vsg_pre_test_profile_mi_degree_day_and_vif_grouping() -> None:
    df = _make_compliance_df(160, seed=99)
    schema = suggest_dataset_schema(df, time_col="Timestamp", targets=["Energy_kWh", "Steam_kg"])
    profiles = pre_test_profile(
        df,
        target="Energy_kWh",
        time_col="Timestamp",
        all_targets=["Energy_kWh", "Steam_kg"],
        schema=schema,
    )

    assert "Temp_C" in profiles
    assert "High_Missing_Sensor" in profiles
    assert profiles["High_Missing_Sensor"].missing_pct > 60.0
    assert profiles["Constant_Flag"].is_constant is True
    assert profiles["Near_Constant_Sensor"].is_near_constant is True
    assert profiles["Submeter_Leak_kWh"].leakage_suspicion is True
    assert profiles["Submeter_Leak_kWh"].integrity_blocking is True
    assert profiles["Steam_kg"].integrity_blocking is True
    assert profiles["Temp_C"].mini_histogram["sparkline"] != ""
    assert profiles["Shift_Mode"].mini_histogram["kind"] == "categorical"

    # V-shaped temperature has weak linear r but strong mutual information (VSG-7)
    assert abs(profiles["Temp_C"].pearson_r) < 0.35
    assert profiles["Temp_C"].mutual_info > 0.05

    vsg = VariableSelectionGate(
        df,
        targets=["Energy_kWh", "Steam_kg"],
        time_col="Timestamp",
        schema=schema,
        dataset_sha256="sha_demo",
    )
    decs = vsg.decisions_by_target["Energy_kWh"]
    assert decs["High_Missing_Sensor"].resolved_disposition == VariableDisposition.EXCLUDE.value
    assert decs["Constant_Flag"].resolved_disposition == VariableDisposition.EXCLUDE.value
    assert decs["Near_Constant_Sensor"].resolved_disposition == VariableDisposition.EXCLUDE.value
    assert decs["Submeter_Leak_kWh"].resolved_disposition == VariableDisposition.EXCLUDE.value
    assert decs["Steam_kg"].resolved_disposition == VariableDisposition.EXCLUDE.value
    assert decs["Production_ton"].resolved_disposition == VariableDisposition.INCLUDE.value

    # Temp_C and Temp_F_Collinear: one included, the other collinear_grouped!
    pair_disps = {decs["Temp_C"].resolved_disposition, decs["Temp_F_Collinear"].resolved_disposition}
    assert pair_disps == {
        VariableDisposition.INCLUDE.value,
        VariableDisposition.COLLINEAR_GROUPED.value,
    }


# ===================================================================== VSG-4 & VSG-9: Presets, bulk actions, cross-target copy, overrides
def test_vsg_bulk_presets_cross_target_copy_and_excluded_profiles() -> None:
    df = _make_compliance_df(100, seed=17)
    vsg = VariableSelectionGate(
        df,
        targets=["Energy_kWh", "Steam_kg"],
        time_col="Timestamp",
        dataset_sha256="sha_vsg4",
    )

    # Mandatory audit reason on override (VSG-9)
    with pytest.raises(VSGValidationError, match="reason"):
        vsg.override_variable("Energy_kWh", "Production_ton", "exclude", reason="   ")

    # Cannot force-include leaked or sibling-target column
    with pytest.raises(IntegrityViolationError):
        vsg.override_variable("Energy_kWh", "Submeter_Leak_kWh", "include", reason="try force leak")
    with pytest.raises(IntegrityViolationError):
        vsg.override_variable("Energy_kWh", "Steam_kg", "include", reason="try sibling target")
    with pytest.raises(IntegrityViolationError):
        vsg.override_variable("Energy_kWh", "Energy_kWh", "include", reason="try self target")

    # Presets
    for preset in sorted(VALID_PRESETS):
        out = vsg.apply_preset("Energy_kWh", preset)
        assert out["Submeter_Leak_kWh"] == VariableDisposition.EXCLUDE.value
        assert out["Steam_kg"] == VariableDisposition.EXCLUDE.value

    weather_out = vsg.apply_preset("Energy_kWh", "weather_only")
    assert weather_out["Temp_C"] == VariableDisposition.INCLUDE.value
    assert weather_out["Production_ton"] == VariableDisposition.EXCLUDE.value

    # Bulk action
    bulk_res = vsg.bulk_set_disposition(
        "Energy_kWh",
        ["Production_ton", "Submeter_Leak_kWh"],
        "include",
        reason="re-enable production in bulk",
    )
    assert bulk_res["Production_ton"] == VariableDisposition.INCLUDE.value
    assert bulk_res["Submeter_Leak_kWh"] == "skipped_integrity_blocked"

    # Cross-target copy re-enforces integrity on destination target
    copied = vsg.copy_target_decisions("Energy_kWh", "Steam_kg")
    assert "Energy_kWh" in copied
    assert copied["Energy_kWh"] in ("exclude", "blocked_by_integrity")
    assert "Steam_kg" not in vsg.included_variables("Steam_kg")
    assert "Energy_kWh" not in vsg.included_variables("Steam_kg")

    # Excluded descriptive profiles remain populated (VSG-4)
    exc_profiles = vsg.excluded_descriptive_profiles("Energy_kWh")
    exc_names = {p["variable"] for p in exc_profiles}
    assert {"Constant_Flag", "High_Missing_Sensor", "Submeter_Leak_kWh"} <= exc_names
    assert all("exclusion_reason" in p and "mini_histogram" in p for p in exc_profiles)


# ===================================================================== Gate G3 #5: Non-disableable O(n) integrity rules
def test_integrity_rules_cannot_be_disabled_and_catch_all_four_classes() -> None:
    # 1. Direct API mutation attempts raise IntegrityBypassError / TypeError
    for rid in NON_DISABLEABLE_RULES:
        with pytest.raises(IntegrityBypassError):
            DEFAULT_INTEGRITY_ENGINE.disable_rule(rid)
        with pytest.raises(IntegrityBypassError):
            DEFAULT_INTEGRITY_ENGINE.set_rule_enabled(rid, False)
    with pytest.raises(TypeError):
        DEFAULT_INTEGRITY_ENGINE.rule_registry["TARGET_AS_PREDICTOR"] = lambda *a: []  # type: ignore[index]

    # 2. Construct DataFrame violating all 4 integrity rules simultaneously + config bypass keys
    df_bad = _make_compliance_df(40)
    # Reverse rows 5 and 6 (time order violation) and duplicate row 10 timestamp (DST/duplicate violation)
    ts_list = list(df_bad["Timestamp"])
    ts_list[5], ts_list[6] = ts_list[6], ts_list[5]
    ts_list[11] = ts_list[10]
    df_bad["Timestamp"] = ts_list

    schema = suggest_dataset_schema(df_bad, time_col="Timestamp", targets=["Energy_kWh"])
    # Mark Shift_Mode reporting_only to verify schema availability leakage check too
    for sp in schema.columns:
        if sp.name == "Shift_Mode":
            sp.availability = Availability.REPORTING_ONLY.value
            sp.role = ColumnRole.PREDICTOR

    adversarial_cfg = {
        "disable_integrity": True,
        "skip_integrity": "true",
        "disabled_rules": list(NON_DISABLEABLE_RULES),
        "allow_target_as_predictor": True,
        "ignore_leakage": 1,
    }
    rep = run_integrity_checks(
        df_bad,
        target="Energy_kWh",
        time_col="Timestamp",
        all_targets=["Energy_kWh", "Steam_kg"],
        candidate_predictors=["Energy_kWh", "Steam_kg", "Submeter_Leak_kWh", "Shift_Mode", "Temp_C"],
        schema=schema,
        config=adversarial_cfg,
    )
    assert rep.passed is False
    assert rep.bypass_attempts_blocked == 5
    triggered_rules = {c.rule_id for c in rep.blocking_conflicts}
    assert triggered_rules == {
        RULE_BYPASS_BLOCKED,
        RULE_TARGET_AS_PREDICTOR,
        RULE_TARGET_LEAKAGE_MT2,
        RULE_TIME_ORDER_VIOLATION,
        RULE_DST_DUPLICATE_TIMESTAMPS,
    }

    # Attempting to accept VSG on unsorted/duplicate time series raises IntegrityViolationError
    vsg_bad = VariableSelectionGate(df_bad, targets=["Energy_kWh"], time_col="Timestamp", config=adversarial_cfg)
    with pytest.raises(IntegrityViolationError):
        vsg_bad.accept()


def test_integrity_linear_time_complexity_on_large_frame() -> None:
    rng = np.random.default_rng(2026)
    n_large = 25_000
    df_large = pd.DataFrame(
        {
            "Timestamp": pd.date_range("2020-01-01", periods=n_large, freq="h", tz="UTC"),
            "Energy_kWh": rng.normal(500.0, 40.0, size=n_large),
            "Temp_C": rng.normal(15.0, 8.0, size=n_large),
            "Prod": rng.normal(100.0, 12.0, size=n_large),
            "Humid": rng.uniform(20.0, 90.0, size=n_large),
        }
    )
    t0 = time.perf_counter()
    rep = run_integrity_checks(df_large, target="Energy_kWh", time_col="Timestamp")
    elapsed = time.perf_counter() - t0
    assert rep.passed is True
    assert elapsed < 2.0, f"O(n) integrity check took {elapsed:.2f}s on 25k rows"


# ===================================================================== Gate G3 #7: Provenance & strict post_hoc UI-only boundary
def test_provenance_ledger_hash_chain_and_zero_post_hoc_outside_gui(eb_home: Path) -> None:
    import energybaseline.db.session as ses

    ses.reset_engine()
    ses.init_database()

    # 1. Core ProvenanceLedger strictly validates phase in {'pre_test', 'post_test'}
    ledger = ProvenanceLedger(dataset_sha256="ds_sha_001")
    r1 = ledger.append(
        entity_type="vsg_variable",
        entity_id="Temp_C",
        source=ProvenanceSource.AUTO_RULE,
        phase=ProvenancePhase.PRE_TEST,
        target="Energy_kWh",
        new_value="include",
        reason="initial pre-test screening",
    )
    assert r1.requires_limitations_disclosure is False

    # Passing forbidden presentation token 'post_hoc' into core provenance MUST fail!
    forbidden_token = "post_" + "hoc"
    with pytest.raises(ProvenanceValidationError):
        ledger.append(
            entity_type="vsg_variable",
            entity_id="Temp_C",
            source=ProvenanceSource.USER_MODIFICATION,
            phase=forbidden_token,
        )

    r2 = ledger.append(
        entity_type="tcp_setting",
        entity_id="alpha",
        source=ProvenanceSource.USER_MODIFICATION,
        phase=ProvenancePhase.POST_TEST,
        previous_value=0.05,
        new_value=0.10,
        reason="amended after reviewing initial test output",
    )
    assert r2.requires_limitations_disclosure is True
    assert ledger.has_post_test_modifications() is True

    # Tampering with hash chain raises ProvenanceValidationError
    tampered = ledger.to_dict()
    tampered["records"][1]["reason"] = "silently altered"
    with pytest.raises(ProvenanceValidationError):
        ProvenanceLedger.from_dict(tampered)

    # 2. Persist VSG, TCP (after post-test amendment), and Provenance to SQLite and assert ZERO 'post_hoc' anywhere!
    df = _make_compliance_df(60)
    vsg = VariableSelectionGate(df, targets=["Energy_kWh"], time_col="Timestamp", dataset_sha256="ds_sha_002")
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    tcp.lock_preregistration()
    tcp.unlock_for_amendment(reason="amend alpha post-test")
    tcp.set_alpha(0.10, reason="widen alpha post-test")
    vsg.accept(phase=ProvenancePhase.POST_TEST)
    tcp.lock_preregistration()

    vid = store.save_vsg_state(vsg.to_dict())
    tid = store.save_tcp_config(tcp.to_dict(), vsg_id=vid)
    store.save_provenance_ledger(vsg.provenance.to_dict())

    serialized_blobs = [
        json.dumps(vsg.to_dict()),
        json.dumps(tcp.to_dict()),
        json.dumps(vsg.provenance.to_dict()),
        json.dumps(store.load_vsg_state(vid)),
        json.dumps(store.load_tcp_config(tid)),
        json.dumps(store.load_provenance_records()),
    ]
    for blob in serialized_blobs:
        assert forbidden_token not in blob.lower(), "post_hoc leaked into serialized non-UI payload!"

    # 3. Static repository guard: zero occurrences of 'post_hoc' in any non-GUI module under energybaseline/
    pkg_dir = REPO_ROOT / "energybaseline"
    for py_file in sorted(pkg_dir.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("energybaseline/gui/"):
            continue
        text = py_file.read_text(encoding="utf-8")
        assert forbidden_token not in text.lower(), f"Forbidden UI-only token found outside GUI in {rel}"
