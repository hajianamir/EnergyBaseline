"""Phase 4 Sub-Gate G4c: T10..T14, R references (DM, Nemenyi, Little MCAR, MCS), 'not run (user)' report & 0-call what_if."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energybaseline.cli import main as cli_main
from energybaseline.core.cache import CachedComplianceRunner
from energybaseline.core.pipeline import run_analysis
from energybaseline.core.report import export_excel, render_html
from energybaseline.core.schema import ColumnRole, Inclusion, suggest_dataset_schema
from energybaseline.core.tcp import DecisionRule, MultipleTestingCorrection, TestConfigPanel
from energybaseline.core.vsg import ComplianceGateError, VariableSelectionGate
from energybaseline.tests_catalog import (
    CAUSAL_GROUP_TEST_IDS,
    POST_MODEL_TEST_IDS,
    PRE_MODEL_TEST_IDS,
    compute_lagged_ccf_and_granger,
    compute_mcs_exact,
    compute_multi_group_anova_kruskal,
    compute_ramsey_reset_harvey_collier,
    run_full_test_catalog,
    run_t10_diebold_mariano,
    run_t11_nemenyi,
    run_t12_little_mcar,
    run_t13_outlier_influence,
    run_t14_causal_group,
    run_test_monte_carlo,
)

PKG_REF_DIR = Path(__file__).resolve().parents[2] / "energybaseline" / "tests_catalog" / "r_references"


def _synthetic_energy_df(n: int = 96, with_group: bool = False, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2025-01-01", periods=n, freq="D")
    temp = 15.0 + 10.0 * np.sin(2.0 * np.pi * np.arange(n) / 30.0) + rng.normal(0.0, 1.2, size=n)
    occ = 0.7 + 0.2 * np.cos(2.0 * np.pi * np.arange(n) / 7.0) + rng.normal(0.0, 0.05, size=n)
    kwh = 120.0 + 3.2 * temp + 40.0 * occ + rng.normal(0.0, 2.5, size=n)
    data: dict[str, object] = {"timestamp": ts, "temp_c": temp, "occupancy": occ, "elec_kwh": kwh}
    if with_group:
        data["facility_group"] = np.where(np.arange(n) % 2 == 0, "control", "treatment")
    return pd.DataFrame(data)


def test_t10_diebold_mariano_and_mcs_pass_fail_and_r_references() -> None:
    dm_ref = json.loads((PKG_REF_DIR / "diebold_mariano.json").read_text(encoding="utf-8"))
    c_eq, c_sup = dm_ref["cases"]

    r_pass = run_t10_diebold_mariano(
        c_eq["inputs"]["errors_1"],
        c_eq["inputs"]["errors_2"],
        h=1,
        power=2,
        use_hln_exact=True,
    )
    r_fail = run_t10_diebold_mariano(
        c_sup["inputs"]["errors_1"],
        c_sup["inputs"]["errors_2"],
        h=2,
        power=2,
        use_hln_exact=True,
    )
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert abs(float(r_fail.statistic) - float(c_sup["expected"]["statistic"])) <= 1e-8

    mcs_ref = json.loads((PKG_REF_DIR / "mcs.json").read_text(encoding="utf-8"))
    c_tie, c_dom = mcs_ref["cases"]
    m_tie = compute_mcs_exact(
        c_tie["inputs"]["loss_matrix"],
        c_tie["inputs"]["model_names"],
        alpha=0.10,
        boot_indices=np.asarray(c_tie["inputs"]["boot_indices_0based"], dtype=int),
    )
    m_dom = compute_mcs_exact(
        c_dom["inputs"]["loss_matrix"],
        c_dom["inputs"]["model_names"],
        alpha=0.10,
        boot_indices=np.asarray(c_dom["inputs"]["boot_indices_0based"], dtype=int),
    )
    assert sorted(m_tie["superior_set"]) == sorted(c_tie["expected"]["superior_set"])
    assert sorted(m_dom["superior_set"]) == sorted(c_dom["expected"]["superior_set"])


def test_t11_nemenyi_pass_fail_and_r_reference() -> None:
    ref = json.loads((PKG_REF_DIR / "nemenyi.json").read_text(encoding="utf-8"))
    c_null, c_alt = ref["cases"]

    r_pass = run_t11_nemenyi(
        c_null["inputs"]["loss_matrix"],
        model_names=c_null["inputs"]["model_names"],
        use_nemenyi_exact=True,
    )
    r_fail = run_t11_nemenyi(
        c_alt["inputs"]["loss_matrix"],
        model_names=c_alt["inputs"]["model_names"],
        use_nemenyi_exact=True,
    )
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert abs(float(r_fail.statistic) - float(c_alt["expected"]["statistic"])) <= 1e-8


def test_t12_little_mcar_pass_fail_and_r_reference() -> None:
    ref = json.loads((PKG_REF_DIR / "little_mcar.json").read_text(encoding="utf-8"))
    c_mcar, c_mar = ref["cases"]
    mat_mcar = np.array([[np.nan if v is None else float(v) for v in row] for row in c_mcar["inputs"]["data"]])
    mat_mar = np.array([[np.nan if v is None else float(v) for v in row] for row in c_mar["inputs"]["data"]])

    r_pass = run_t12_little_mcar(mat_mcar, use_em_exact=True, alpha=0.05)
    r_fail = run_t12_little_mcar(mat_mar, use_em_exact=True, alpha=0.05)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert abs(float(r_fail.statistic) - float(c_mar["expected"]["statistic"])) <= 1e-6


def test_t13_outlier_influence_strictly_flag_only_zero_mutation() -> None:
    rng = np.random.default_rng(1313)
    x = np.linspace(-2.0, 2.0, num=100)
    y_clean = 50.0 + 3.0 * x + rng.normal(0.0, 0.8, size=100)
    y_spike = y_clean.copy()
    y_spike[98] += 18.0  # High-leverage gross spike

    sha_y_before = hashlib.sha256(y_spike.tobytes()).hexdigest()
    sha_x_before = hashlib.sha256(x.tobytes()).hexdigest()

    r_pass = run_t13_outlier_influence(y_clean, x, alpha=0.05, seed=1313)
    r_fail = run_t13_outlier_influence(y_spike, x, alpha=0.05, seed=1313)

    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"
    assert r_fail.downstream_action == "flag_only_preserve_all_rows"
    assert r_fail.details["rows_deleted"] == 0
    assert 98 in r_fail.details["flagged_indices"]
    assert hashlib.sha256(y_spike.tobytes()).hexdigest() == sha_y_before
    assert hashlib.sha256(x.tobytes()).hexdigest() == sha_x_before


def test_t14_causal_group_only_runs_with_group_column_and_pass_fail() -> None:
    rng = np.random.default_rng(1414)
    n = 120
    cov = rng.normal(0.0, 1.0, size=(n, 2))
    grp = np.array(["control"] * 60 + ["treatment"] * 60)
    t_vec = np.concatenate([np.linspace(0.0, 1.0, 60), np.linspace(0.0, 1.0, 60)])
    y_parallel = 20.0 + 1.5 * cov[:, 0] + 2.0 * t_vec + rng.normal(0.0, 0.7, size=n)
    y_divergent = y_parallel + np.where(grp == "treatment", 4.5 * t_vec, 0.0)

    # Without group column -> explicitly 'not applicable (no group column)'
    r_no_grp = run_t14_causal_group(y_parallel, cov, group=None)
    assert r_no_grp.decision == "not applicable (no group column)"

    r_pass = run_t14_causal_group(y_parallel, cov, group=grp, alpha=0.05, seed=1414)
    r_fail = run_t14_causal_group(y_divergent, cov, group=grp, alpha=0.05, seed=1414)
    assert r_pass.decision == "pass"
    assert r_fail.decision == "fail"

    # Skewed covariates trigger Brunner-Munzel / Mann-Whitney fallback
    cov_skew = rng.exponential(2.0, size=(n, 2))
    r_bm = run_t14_causal_group(y_parallel, cov_skew, group=grp, alpha=0.05, seed=1414)
    assert r_bm.fallback_used == "brunner_munzel_mann_whitney"


def test_stage_placement_gate_enforcement_zero_call_whatif_and_not_run_user_in_report(tmp_path: Path) -> None:
    """Gate G4 Criterion #2 + stage placement + VSG/TCP acceptance guard + 0-engine-call what_if_catalog."""
    df = _synthetic_energy_df(n=72, with_group=False, seed=77)
    schema = suggest_dataset_schema(df, time_col="timestamp", targets=["elec_kwh"])
    vsg = VariableSelectionGate(df, time_col="timestamp", targets=["elec_kwh"], schema=schema)

    # Unaccepted VSG must block inferential catalog execution
    tcp_unaccepted = TestConfigPanel(vsg)
    with pytest.raises(ComplianceGateError):
        run_full_test_catalog(df, target_col="elec_kwh", predictor_cols=["temp_c", "occupancy"], panel=tcp_unaccepted)

    vsg.accept()
    tcp = TestConfigPanel(vsg, config={"tcp_alpha": 0.05, "tcp_correction": MultipleTestingCorrection.HOLM.value})
    tcp.configure_test_panel("T7", enabled=False)
    tcp.configure_test_panel("T9", enabled=False)
    tcp.accept()

    runner = CachedComplianceRunner(seed=77)
    oof_stub = {
        "elec_kwh": {
            "ols": np.random.default_rng(1).normal(0.0, 1.0, size=72),
            "ridge": np.random.default_rng(2).normal(0.0, 1.2, size=72),
            "gbm": np.random.default_rng(3).normal(0.0, 2.5, size=72),
        }
    }
    by_target = runner.execute_catalog_suite(df, vsg, tcp, oof_errors_by_target=oof_stub, mode="fast")
    suite = by_target["elec_kwh"]
    by_id = {r.test_id: r for r in suite}

    # Verify stage placement
    for tid in PRE_MODEL_TEST_IDS:
        assert by_id[tid].stage == "pre_model"
    for tid in POST_MODEL_TEST_IDS:
        assert by_id[tid].stage == "post_model"
    for tid in CAUSAL_GROUP_TEST_IDS:
        assert by_id[tid].stage == "causal_group"
    assert by_id["T14"].decision == "not applicable (no group column)"
    assert by_id["T7"].decision == "not run (user)"
    assert by_id["T9"].decision == "not run (user)"

    # Verify zero-engine-call what_if_catalog re-evaluates decisions & preserves both p_raw and p_adj
    wi = runner.what_if_catalog(
        suite,
        alpha=0.001,
        correction=MultipleTestingCorrection.BONFERRONI.value,
        decision_rule=DecisionRule.P_AND_EFFECT_SIZE.value,
        min_effect_size=0.50,
    )
    assert wi["engine_calls_delta"] == 0
    for r_new in wi["results"]:
        if r_new.p_raw is not None:
            assert r_new.p_adj is not None and r_new.p_adj >= r_new.p_raw - 1e-12

    # Verify end-to-end pipeline + HTML & Excel reports explicitly show "not run (user)"!
    run_res, _ = run_analysis(
        df,
        "timestamp",
        ["elec_kwh"],
        config={"disabled_tests": ["T7", "T9"], "test_catalog_mode": "fast"},
    )
    html_str, _ = render_html(run_res, selections=[], audit=[])
    assert "not run (user)" in html_str
    assert "not applicable (no group column)" in html_str

    xlsx_path = tmp_path / "phase4_report.xlsx"
    export_excel(run_res, selections=[], audit=[], path=xlsx_path)
    cat_sheet = pd.read_excel(xlsx_path, sheet_name="TestCatalog_1_elec_kwh")
    assert "not run (user)" in set(cat_sheet["decision"].astype(str))

    # Also verify CLI test-catalog subcommand emits E2 JSON with "not run (user)"
    csv_path = tmp_path / "energy.csv"
    out_json = tmp_path / "test_catalog.json"
    df.to_csv(csv_path, index=False)
    rc = cli_main(
        [
            "test-catalog",
            str(csv_path),
            "--targets",
            "elec_kwh",
            "--disable-test",
            "T7",
            "--mode",
            "fast",
            "--out",
            str(out_json),
        ]
    )
    assert rc == 0
    cli_doc = json.loads(out_json.read_text(encoding="utf-8"))
    t7_cli = [x for x in cli_doc["targets"]["elec_kwh"] if x["test_id"] == "T7"][0]
    assert t7_cli["decision"] == "not run (user)"
    _ = ColumnRole, Inclusion


@pytest.mark.parametrize("tid", ["T10", "T11", "T12", "T13", "T14"])
def test_g4c_monte_carlo_type1_and_power(tid: str) -> None:
    mc = run_test_monte_carlo(tid, alpha=0.05, n_rep=80, sample_size=120, power_target=0.80, seed=3000)
    assert mc.type1_calibrated, f"{tid} Type I error uncalibrated: {mc.to_dict()}"
    assert mc.power_achieved, f"{tid} power below target: {mc.to_dict()}"


def test_auxiliary_ramsey_reset_granger_and_multigroup_anova_kruskal() -> None:
    rng = np.random.default_rng(909)
    x = np.linspace(-2.0, 2.0, num=120)
    y_lin = 10.0 + 2.0 * x + rng.normal(0.0, 0.4, size=120)
    y_quad = y_lin + 1.8 * (x**2)
    assert compute_ramsey_reset_harvey_collier(y_lin, x)["reset_p"] > 0.05
    assert compute_ramsey_reset_harvey_collier(y_quad, x)["reset_p"] < 0.001

    drv = rng.normal(0.0, 1.0, size=120)
    y_lagged = np.roll(drv, 2) * 1.5 + rng.normal(0.0, 0.5, size=120)
    y_lagged[:2] = 0.0
    gr = compute_lagged_ccf_and_granger(y_lagged, drv, max_lag=4)
    assert gr["best_lag"] == 2
    assert gr["granger_p"] < 0.001

    vals = np.concatenate([rng.exponential(1.0, 40), rng.exponential(1.0, 40) + 3.0, rng.exponential(1.0, 40)])
    grps = np.array(["zone_a"] * 40 + ["zone_b"] * 40 + ["zone_c"] * 40)
    mg = compute_multi_group_anova_kruskal(vals, grps)
    assert mg["method_selected"] == "kruskal_wallis_nonparametric"
    assert mg["p_value"] < 0.001
