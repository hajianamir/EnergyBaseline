"""Phase 3 Gate G3 tests: deterministic AUTO-log hash, zero hidden constants, CLI E2 endpoints, and Hypothesis fuzzing."""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given, settings, strategies as st
import numpy as np
import pandas as pd
import pytest

from energybaseline import cli
from energybaseline.core import ingest
from energybaseline.core.auto import (
    AutoHiddenConstantError,
    AutoLog,
    AutoLogInspector,
    compute_auto_log_hash,
)
from energybaseline.core.cache import CachedComplianceRunner
from energybaseline.core.integrity import IntegrityViolationError
from energybaseline.core.pipeline import run_analysis
from energybaseline.core.tcp import (
    ALPHA_MAX,
    ALPHA_MIN,
    DecisionRule,
    MultipleTestingCorrection,
    TCPValidationError,
    TestConfigPanel,
    adjust_p_values,
    validate_alpha,
)
from energybaseline.core.vsg import VariableSelectionGate

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = REPO_ROOT / "samples"


# ===================================================================== Gate G3 #6 & #8: AUTO-log hash & no hidden constants
def test_auto_log_rejects_hidden_constants_and_adapts_to_data_signals() -> None:
    auto = AutoLog({})
    for bad_sig in ("", "default", "constant", "hardcoded", "default engineering limit", "none"):
        with pytest.raises(AutoHiddenConstantError):
            auto.decide("some_setting", lambda s=bad_sig: (0.05, s, "bad rule"))
    with pytest.raises(AutoHiddenConstantError):
        auto.note("derived_x", 1, "default", "bad note")

    # Verify empirical adaptation: small sample (n=24) vs large sample (n=2200) resolves different AUTO thresholds!
    rng = np.random.default_rng(77)
    df_small = pd.DataFrame(
        {
            "Timestamp": pd.date_range("2024-01-01", periods=24, freq="D", tz="UTC"),
            "Energy_kWh": rng.normal(200.0, 20.0, size=24),
            "Temp_C": rng.normal(15.0, 5.0, size=24),
        }
    )
    df_large = pd.DataFrame(
        {
            "Timestamp": pd.date_range("2020-01-01", periods=2200, freq="h", tz="UTC"),
            "Energy_kWh": rng.normal(200.0, 20.0, size=2200),
            "Temp_C": rng.normal(15.0, 5.0, size=2200),
        }
    )
    vsg_small = VariableSelectionGate(df_small, targets=["Energy_kWh"], time_col="Timestamp", dataset_sha256="s1")
    tcp_small = TestConfigPanel(vsg_small)
    vsg_large = VariableSelectionGate(df_large, targets=["Energy_kWh"], time_col="Timestamp", dataset_sha256="s2")
    tcp_large = TestConfigPanel(vsg_large)

    assert tcp_small.alpha != tcp_large.alpha  # 0.10 (n<30) vs 0.01 (n>=2000)
    assert tcp_small.decision_rule != tcp_large.decision_rule  # p_only vs p_and_effect_size
    insp_small = AutoLogInspector(vsg_small.auto_log.records)
    insp_large = AutoLogInspector(vsg_large.auto_log.records)
    assert insp_small.summary()["all_auto_have_empirical_signals"] is True
    assert insp_large.summary()["all_auto_have_empirical_signals"] is True
    assert len(insp_small.filter(mode="AUTO")) > 0


def test_pipeline_twice_produces_byte_identical_auto_log_hash(eb_home: Path) -> None:
    import energybaseline.db.session as ses

    ses.reset_engine()
    ses.init_database()

    df = ingest.load_file(str(SAMPLES / "monthly_building.csv"))
    tc = ingest.guess_time_column(df)
    fast_cfg = {"hpo_trials": "0", "nn_max_epochs": "5", "enable_mining": "false"}

    run1, _ = run_analysis(df, tc, ["Energy_kWh"], dict(fast_cfg), dataset_sha="monthly_sha")
    run2, _ = run_analysis(df, tc, ["Energy_kWh"], dict(fast_cfg), dataset_sha="monthly_sha")

    assert len(run1.auto_log_hash) == 64
    assert run1.auto_log_hash == run2.auto_log_hash
    assert run1.auto == run2.auto
    assert compute_auto_log_hash(run1.auto) == run1.auto_log_hash
    assert AutoLogInspector(run1.auto).summary()["all_auto_have_empirical_signals"] is True


# ===================================================================== CLI E2 endpoints: system.health, system.sbom, auto-log-hash, what-if
def test_cli_phase3_product_endpoints_emit_valid_json(eb_home: Path, tmp_path: Path) -> None:
    health_out = tmp_path / "system_health.json"
    sbom_out = tmp_path / "system_sbom.json"
    autohash_out = tmp_path / "auto_log_hash.json"
    whatif_out = tmp_path / "what_if.json"
    tcplock_out = tmp_path / "tcp_lock.json"
    vsginsp_out = tmp_path / "vsg_inspect.json"

    sample_csv = str(SAMPLES / "monthly_building.csv")

    assert cli.main(["system.health", "--out", str(health_out)]) == 0
    assert cli.main(["system.sbom", "--out", str(sbom_out)]) == 0
    assert cli.main(["auto-log-hash", sample_csv, "--out", str(autohash_out)]) == 0
    assert (
        cli.main(["what-if", sample_csv, "--alpha", "0.01", "--correction", "bonferroni", "--out", str(whatif_out)])
        == 0
    )
    assert cli.main(["tcp-lock", sample_csv, "--out", str(tcplock_out)]) == 0
    assert cli.main(["vsg-inspect", sample_csv, "--accept", "--out", str(vsginsp_out)]) == 0

    h_doc = json.loads(health_out.read_text(encoding="utf-8"))
    assert h_doc["command"] == "system.health" and h_doc["ok"] is True

    s_doc = json.loads(sbom_out.read_text(encoding="utf-8"))
    assert s_doc["bomFormat"] == "CycloneDX" and s_doc["component_count"] >= 20

    a_doc = json.loads(autohash_out.read_text(encoding="utf-8"))
    assert a_doc["byte_identical"] is True
    assert a_doc["auto_log_hash_run1"] == a_doc["auto_log_hash_run2"]
    assert a_doc["all_auto_have_empirical_signals"] is True

    w_doc = json.loads(whatif_out.read_text(encoding="utf-8"))
    assert w_doc["what_if"]["engine_calls_delta"] == 0
    assert w_doc["initial_engine_calls"] == w_doc["post_what_if_engine_calls"]


# ===================================================================== Gate G3 #4: Hypothesis Fuzzing of TCP & VSG


@settings(max_examples=75, deadline=None)
@given(
    p_vals=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=25,
    )
)
def test_hypothesis_fuzz_tcp_five_corrections_monotonicity_and_dominance(p_vals: list[float]) -> None:
    by_method: dict[str, list[float]] = {}
    for m in MultipleTestingCorrection:
        rows = adjust_p_values(p_vals, m)
        assert len(rows) == len(p_vals)
        for orig_p, r in zip(p_vals, rows):
            assert r["p_raw"] == pytest.approx(orig_p)
            assert 0.0 <= r["p_adj"] <= 1.0
            assert r["p_adj"] + 1e-12 >= r["p_raw"]
        # Rank order monotonicity: if p_raw[i] <= p_raw[j] then p_adj[i] <= p_adj[j]
        order = np.argsort(np.asarray(p_vals, dtype=float), kind="mergesort")
        adj_sorted = [rows[int(idx)]["p_adj"] for idx in order]
        for a, b in zip(adj_sorted, adj_sorted[1:]):
            assert a <= b + 1e-12
        by_method[m.value] = [r["p_adj"] for r in rows]

    # Dominance ordering: none <= BH <= BY and none <= Holm <= Bonferroni
    for i in range(len(p_vals)):
        assert by_method["none"][i] <= by_method["benjamini_hochberg"][i] + 1e-12
        assert by_method["benjamini_hochberg"][i] <= by_method["benjamini_yekutieli"][i] + 1e-12
        assert by_method["none"][i] <= by_method["holm"][i] + 1e-12
        assert by_method["holm"][i] <= by_method["bonferroni"][i] + 1e-12


@settings(max_examples=50, deadline=None)
@given(
    alpha_candidate=st.floats(min_value=-0.25, max_value=0.50, allow_nan=False, allow_infinity=False),
    correction=st.sampled_from([c.value for c in MultipleTestingCorrection]),
    rule=st.sampled_from([d.value for d in DecisionRule]),
    eff_floor=st.floats(min_value=0.01, max_value=0.40, allow_nan=False, allow_infinity=False),
)
def test_hypothesis_fuzz_tcp_guard_rails_and_zero_call_what_if(
    alpha_candidate: float,
    correction: str,
    rule: str,
    eff_floor: float,
) -> None:
    if ALPHA_MIN <= alpha_candidate <= ALPHA_MAX:
        val, warns = validate_alpha(alpha_candidate)
        assert ALPHA_MIN <= val <= ALPHA_MAX
        assert (len(warns) > 0) == (val > 0.10 + 1e-12)
    else:
        with pytest.raises(TCPValidationError):
            validate_alpha(alpha_candidate)
        return

    rng = np.random.default_rng(101)
    df = pd.DataFrame(
        {
            "Timestamp": pd.date_range("2024-01-01", periods=36, freq="D", tz="UTC"),
            "Energy_kWh": rng.normal(100.0, 12.0, size=36),
            "Temp_C": rng.normal(15.0, 6.0, size=36),
            "Prod": rng.normal(50.0, 8.0, size=36),
        }
    )
    vsg = VariableSelectionGate(df, targets=["Energy_kWh"], time_col="Timestamp", dataset_sha256="fuzz_tcp")
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    tcp.lock_preregistration()
    runner = CachedComplianceRunner(seed=11)
    runner.execute_tests(df, vsg, tcp)
    calls_0 = runner.engine_call_count

    probe = tcp.clone_for_what_if(
        alpha=val,
        correction=correction,
        decision_rule=rule,
        min_effect_size=eff_floor,
    )
    wi = runner.what_if(probe)
    assert wi["engine_calls_delta"] == 0
    assert runner.engine_call_count == calls_0


@settings(max_examples=45, deadline=None)
@given(
    n_rows=st.integers(min_value=16, max_value=64),
    inject_leak=st.booleans(),
    inject_unsorted_time=st.booleans(),
    inject_dup_time=st.booleans(),
    miss_frac=st.floats(min_value=0.0, max_value=0.80, allow_nan=False, allow_infinity=False),
)
def test_hypothesis_fuzz_vsg_and_integrity_invariants(
    n_rows: int,
    inject_leak: bool,
    inject_unsorted_time: bool,
    inject_dup_time: bool,
    miss_frac: float,
) -> None:
    rng = np.random.default_rng(n_rows + (100 if inject_leak else 0))
    ts = list(pd.date_range("2024-01-01", periods=n_rows, freq="D", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"))
    if inject_unsorted_time:
        ts[2], ts[3] = ts[3], ts[2]
    if inject_dup_time:
        ts[-1] = ts[-2]

    y = rng.normal(300.0, 25.0, size=n_rows)
    x_clean = rng.normal(20.0, 4.0, size=n_rows)
    x_miss = rng.normal(10.0, 2.0, size=n_rows)
    n_miss = int(round(miss_frac * n_rows))
    if n_miss > 0:
        x_miss[:n_miss] = np.nan

    cols: dict[str, object] = {
        "Timestamp": ts,
        "Energy_kWh": y,
        "Temp_C": x_miss,
        "Prod_ton": x_clean,
        "Const_Col": np.ones(n_rows),
    }
    if inject_leak:
        cols["Leaked_Energy_kWh"] = 2.5 * y - 15.0

    df = pd.DataFrame(cols)
    vsg_a = VariableSelectionGate(df, targets=["Energy_kWh"], time_col="Timestamp", dataset_sha256="fuzz_vsg")
    vsg_b = VariableSelectionGate(df, targets=["Energy_kWh"], time_col="Timestamp", dataset_sha256="fuzz_vsg")

    # Deterministic hashes across two runs
    assert vsg_a.vsg_sha256 == vsg_b.vsg_sha256
    assert vsg_a.auto_log.compute_hash() == vsg_b.auto_log.compute_hash()

    # Constant & leaked predictors are never included
    inc = set(vsg_a.included_variables("Energy_kWh"))
    assert "Const_Col" not in inc
    assert "Energy_kWh" not in inc
    if inject_leak:
        assert "Leaked_Energy_kWh" not in inc

    # Time-order or duplicate timestamp violations strictly block VSG acceptance
    if inject_unsorted_time or inject_dup_time:
        with pytest.raises(IntegrityViolationError):
            vsg_a.accept()
    else:
        vsg_a.accept()
        assert vsg_a.is_accepted()
