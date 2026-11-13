"""Phase 6 Gate G6 Tests — 13-Criteria Scoring, ±20% Sensitivity, Separated Registers, SC-4/7/9, Pre-Lock Top-5 & Immutable Lock."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energybaseline import cli
from energybaseline.core import store
from energybaseline.core.pipeline import run_analysis
from energybaseline.lock import (
    ArtifactIntegrityError,
    BaselineImmutabilityError,
    BaselineLockManager,
    export_locked_baseline_excel,
    verify_excel_formula_roundtrip,
)
from energybaseline.models import (
    SURROGATE_BANNER,
    MLRBaselineWrapper,
    RandomForestWrapper,
    Top5CandidateCard,
    Top5ShortlistView,
    build_model_representation,
)
from energybaseline.scoring import (
    CRITERIA_13,
    DEFAULT_WEIGHTS_13,
    OverrideJustificationError,
    RegisterComminglingError,
    RegisterEntry,
    SeparatedModelRegisters,
    ZeroQualifiedUnacknowledgedError,
    analyze_weight_sensitivity_20pct,
    apply_manual_baseline_override,
    compute_13_subscores,
    compute_pairwise_comparison_matrix,
    fit_validity_domain_envelope,
    normalize_weights_13,
    partition_model_registers,
    rank_candidates_13,
    select_parsimonious_mcs_leader,
)


def test_13_criteria_weights_sensitivity_20pct_deterministic_tiebreak_and_sc4_parsimony() -> None:
    """Gate G6: 13-criteria rubric, ±20% weight sensitivity, permutation-invariant tie-break, and SC-4 MCS parsimony."""
    assert len(CRITERIA_13) == 13
    w = normalize_weights_13()
    assert set(w.keys()) == set(DEFAULT_WEIGHTS_13.keys())
    assert abs(sum(w.values()) - 1.0) < 1e-12

    subs_linear = compute_13_subscores(
        cvrmse=6.1, nmbe=0.4, r2=0.94, wape=4.8, n_obs=120, n_params=3, family="F1", is_parametric=True
    )
    subs_gbdt = compute_13_subscores(
        cvrmse=5.5, nmbe=0.3, r2=0.95, wape=4.4, n_obs=120, n_params=45, family="F6", is_parametric=False
    )
    subs_poor = compute_13_subscores(
        cvrmse=24.0, nmbe=8.5, r2=0.42, wape=19.0, n_obs=120, n_params=12, family="F5", is_parametric=False
    )

    cands = [
        {
            "model_name": "XGBoost_Complex",
            "family": "F6",
            "is_parametric": False,
            "qualified": True,
            "in_mcs": True,
            "cvrmse": 5.5,
            "nmbe": 0.3,
            "r2": 0.95,
            "n_params": 45,
            "subscores_13": subs_gbdt,
        },
        {
            "model_name": "ASHRAE_5P_Linear",
            "family": "F2",
            "is_parametric": True,
            "qualified": True,
            "in_mcs": True,
            "cvrmse": 6.1,
            "nmbe": 0.4,
            "r2": 0.94,
            "n_params": 5,
            "subscores_13": subs_linear,
        },
        {
            "model_name": "RF_Overfit",
            "family": "F5",
            "is_parametric": False,
            "qualified": False,
            "in_mcs": False,
            "cvrmse": 24.0,
            "nmbe": 8.5,
            "r2": 0.42,
            "n_params": 12,
            "subscores_13": subs_poor,
        },
    ]

    # Deterministic tie-break order under arbitrary input permutations
    r_fwd = [r.model_name for r in rank_candidates_13(cands)]
    r_rev = [r.model_name for r in rank_candidates_13(list(reversed(cands)))]
    assert r_fwd == r_rev

    # Exact score tie between F1 linear (p=3) and F5 tree (p=30): F1 linear MUST win!
    tie_cands = [
        {
            "model_name": "Tree_Tied",
            "family": "F5",
            "is_parametric": False,
            "qualified": True,
            "in_mcs": True,
            "cvrmse": 7.0,
            "nmbe": 0.5,
            "r2": 0.90,
            "n_params": 30,
            "subscores_13": subs_linear,
        },
        {
            "model_name": "MLR_Tied",
            "family": "F1",
            "is_parametric": True,
            "qualified": True,
            "in_mcs": True,
            "cvrmse": 7.0,
            "nmbe": 0.5,
            "r2": 0.90,
            "n_params": 3,
            "subscores_13": subs_linear,
        },
    ]
    assert rank_candidates_13(tie_cands)[0].model_name == "MLR_Tied"

    # SC-4 MCS parsimony tie-break: when complex GBDT is only slightly ahead (e.g. +1.8 pts) and BOTH are in MCS, promote physical F2!
    mcs_pair = [
        {
            "model_name": "XGBoost",
            "family": "F6",
            "is_parametric": False,
            "composite_score": 88.2,
            "in_mcs": True,
            "n_params": 50,
            "cvrmse": 5.8,
        },
        {
            "model_name": "ASHRAE_5P",
            "family": "F2",
            "is_parametric": True,
            "composite_score": 86.4,
            "in_mcs": True,
            "n_params": 5,
            "cvrmse": 6.0,
        },
    ]
    dec = select_parsimonious_mcs_leader(mcs_pair, mcs_score_band=3.5)
    assert dec.parsimony_applied is True
    assert dec.selected_model == "ASHRAE_5P"

    # ±20% weight sensitivity
    sens = analyze_weight_sensitivity_20pct(cands, n_hypercube_draws=40, seed=42)
    assert sens.n_scenarios == 26 + 40
    assert set(sens.criterion_coordinate_flips.keys()) == set(CRITERIA_13)
    assert 0.0 <= sens.top1_flip_rate <= 1.0
    assert 0.0 <= sens.top5_min_jaccard <= 1.0


def test_sc8_pairwise_dm_mcs_and_friedman_matrix() -> None:
    """SC-8: pairwise HLN Diebold-Mariano, Hansen MCS, and Friedman/Nemenyi fold ranks."""
    rng = np.random.default_rng(12)
    y = np.linspace(100.0, 200.0, 48)
    oof = {
        "MLR": y + rng.normal(0.0, 1.2, size=48),
        "Ridge": y + rng.normal(0.0, 1.3, size=48),
        "Mean": np.full(48, float(np.mean(y))),
    }
    fold_errs = np.array(
        [
            [1.1, 1.2, 25.0],
            [1.3, 1.4, 28.0],
            [1.0, 1.1, 22.0],
            [1.2, 1.3, 27.0],
        ]
    )
    pw = compute_pairwise_comparison_matrix(y, oof, fold_error_matrix=fold_errs, alpha=0.10, seed=42)
    assert pw.model_names == ("MLR", "Ridge", "Mean")
    assert abs(pw.dm_stat_matrix[0][2] + pw.dm_stat_matrix[2][0]) < 1e-9
    assert pw.dm_pvalue_matrix[0][2] < 0.01  # MLR significantly beats Mean
    assert "MLR" in pw.mcs_superior_set
    assert "Mean" not in pw.mcs_superior_set
    assert pw.friedman_pvalue is not None and pw.friedman_pvalue < 0.05


def test_strictly_separated_registers_sc9_acknowledgement_sc7_override_and_validity_envelope() -> None:
    """Gate G6: Rejected vs Qualified-not-shortlisted NEVER commingled; SC-9 ack & hull extrapolation; SC-7 override."""
    ranked = [
        {
            "model_name": f"QualModel_{i}",
            "family": "F1",
            "qualified": True,
            "composite_score": 95.0 - i,
            "cvrmse": 5.0 + 0.3 * i,
            "nmbe": 0.5,
            "r2": 0.93,
            "in_mcs": i <= 3,
        }
        for i in range(1, 8)
    ] + [
        {
            "model_name": "BadModel_A",
            "family": "F5",
            "qualified": False,
            "composite_score": 41.0,
            "cvrmse": 28.0,
            "nmbe": 9.1,
            "r2": 0.35,
            "in_mcs": False,
        },
    ]
    unfit = [
        {"model_name": "LSTM_Gated", "family": "F6", "status": "skipped", "reason": "NN gate skipped: n=48 < 60"},
        {"model_name": "FailedSVR", "family": "F5", "status": "rejected", "reason": "Diverged"},
    ]

    regs = partition_model_registers(ranked, unfit, top_k=5)
    regs.assert_ready_for_selection()
    assert len(regs.shortlisted_top5) == 5
    assert [e.model_name for e in regs.qualified_not_shortlisted] == ["QualModel_6", "QualModel_7"]
    assert {e.model_name for e in regs.rejected} == {"BadModel_A", "FailedSVR"}
    assert [e.model_name for e in regs.skipped] == ["LSTM_Gated"]

    # Illegal commingling MUST raise RegisterComminglingError
    bad_regs = SeparatedModelRegisters(
        shortlisted_top5=regs.shortlisted_top5,
        qualified_not_shortlisted=(),
        rejected=(
            *regs.rejected,
            RegisterEntry("QualModel_6", "F1", "rejected", True, 6, 89.0, 6.8, 0.5, 0.93, False, "illegal"),
        ),
        skipped=regs.skipped,
        zero_qualified_warning=None,
        requires_user_acknowledgement=False,
    )
    with pytest.raises(RegisterComminglingError):
        bad_regs.assert_disjoint()

    # Zero-qualified SC-9 workflow: blocks until acknowledged with justification
    zero_regs = partition_model_registers([ranked[-1]], unfit)
    assert zero_regs.requires_user_acknowledgement is True
    with pytest.raises(ZeroQualifiedUnacknowledgedError):
        zero_regs.assert_ready_for_selection()
    acked = zero_regs.with_user_acknowledgement(
        engineer_id="eng.jane",
        justification="Plant expansion during Q2 shifted static load; using diagnostic baseline pending submeter install.",
        original_thresholds={"max_cvrmse": 15.0, "max_nmbe": 5.0, "min_r2": 0.75},
    )
    acked.assert_ready_for_selection()
    assert acked.acknowledgement is not None and len(acked.acknowledgement.audit_sha256) == 64

    # SC-7 Manual engineer override requires >= 15 chars justification
    with pytest.raises(OverrideJustificationError):
        apply_manual_baseline_override(
            ranked, requested_model_name="QualModel_3", engineer_id="eng.jane", justification="too short"
        )
    ov = apply_manual_baseline_override(
        ranked,
        requested_model_name="QualModel_3",
        engineer_id="eng.jane",
        justification="Selected 3P heating model for direct ISO 50006 clause 6.5 auditability with utility reviewer.",
    )
    assert ov.default_recommended_model == "QualModel_1"
    assert ov.overridden_to_model == "QualModel_3"
    assert len(ov.audit_sha256) == 64

    # SC-9 Validity Domain Envelope (min/max + 2D joint ConvexHull)
    t_grid = np.linspace(0.0, 30.0, 80)
    # Correlated band y_2 ~= t_grid so corner (0, 30) is inside univariate [0, 30] x [0, 30] but OUTSIDE joint ConvexHull!
    X_env = pd.DataFrame({"temp_c": t_grid, "wetbulb_c": 0.9 * t_grid + np.sin(t_grid) * 1.5})
    env = fit_validity_domain_envelope(X_env)
    inside_rep = env.check_extrapolation(pd.DataFrame({"temp_c": [15.0], "wetbulb_c": [13.5]}))
    assert inside_rep.extrapolation_detected is False

    corner_hull_rep = env.check_extrapolation(pd.DataFrame({"temp_c": [1.0, 45.0], "wetbulb_c": [22.0, 20.0]}))
    assert corner_hull_rep.extrapolation_detected is True
    assert corner_hull_rep.n_rows_outside_minmax == 1
    assert corner_hull_rep.n_rows_outside_convex_hull == 2


def test_top5_visible_before_lock_and_nonparametric_surrogate_never_presented_as_formula() -> None:
    """Gate G6: Top-5 visible before lock; parametric -> closed-form equation; non-parametric -> primary_formula=None + labelled surrogate + Shapley."""
    rng = np.random.default_rng(55)
    X = pd.DataFrame({"temp_c": rng.uniform(-2.0, 32.0, size=90), "occ": rng.uniform(0.2, 1.0, size=90)})
    y = 120.0 + 4.5 * np.maximum(0.0, X["temp_c"] - 18.0) + 65.0 * X["occ"] + rng.normal(0.0, 1.5, size=90)

    mlr = MLRBaselineWrapper().fit(X, y)
    rf = RandomForestWrapper(n_estimators=100, max_depth=6, seed=42).fit(X, y)

    rep_param = build_model_representation(
        model_name="MLR",
        family="F1",
        is_parametric=True,
        formula_or_arch=mlr.formula_text(),
        coefficients=mlr.coef_raw_,
        X_ref=X,
        y_hat_ref=mlr.predict(X),
        predict_fn=mlr.predict,
    )
    assert rep_param.representation_kind == "closed_form_equation"
    assert rep_param.primary_formula is not None and "y =" in rep_param.primary_formula
    assert rep_param.surrogate is None
    assert rep_param.attributions is not None and rep_param.attributions.additivity_max_error < 1e-8

    rep_nonparam = build_model_representation(
        model_name="RandomForest",
        family="F5",
        is_parametric=False,
        formula_or_arch=rf.formula_text(),
        hyperparameters=rf.hyperparameters(),
        X_ref=X,
        y_hat_ref=rf.predict(X),
        predict_fn=rf.predict,
    )
    assert rep_nonparam.representation_kind == "architecture_with_labelled_surrogate"
    # AUTO-FAIL GUARD: non-parametric model MUST NEVER have primary_formula populated!
    assert rep_nonparam.primary_formula is None
    assert (
        rep_nonparam.architecture_summary is not None and "RandomForestRegressor" in rep_nonparam.architecture_summary
    )
    assert rep_nonparam.surrogate is not None
    assert rep_nonparam.surrogate.is_surrogate is True
    assert rep_nonparam.surrogate.surrogate_banner == SURROGATE_BANNER
    assert 0.50 <= rep_nonparam.surrogate.fidelity_r2 <= 1.0
    assert "SURROGATE APPROXIMATION ONLY" in rep_nonparam.surrogate.approximation_expression
    assert rep_nonparam.attributions is not None
    assert rep_nonparam.attributions.method == "exact_coalition_shapley"
    assert rep_nonparam.attributions.additivity_max_error < 1e-8

    view = Top5ShortlistView(
        target="elec_kwh",
        visible_before_lock=True,
        is_locked=False,
        locked_model_name=None,
        candidates=(
            Top5CandidateCard(
                1, "RandomForest", "F5", 91.2, True, 4.2, 0.1, 0.96, 3.1, 2.2, 32, rep_nonparam, "Rank #1"
            ),
            Top5CandidateCard(2, "MLR", "F1", 89.5, True, 5.1, 0.2, 0.94, 3.9, 2.8, 3, rep_param, "Rank #2"),
        ),
    )
    assert view.visible_before_lock is True and view.is_locked is False and len(view.candidates) == 2


def test_baseline_lock_immutability_sha256_tamper_rejection_and_excel_formula_roundtrip(tmp_path: Path) -> None:
    """Gate G6 (SC-10, AC-04, G-9): LockedBaselineRecord immutability, joblib SHA-256 tamper guard, and Excel formula round-trip."""
    rng = np.random.default_rng(99)
    X = pd.DataFrame(
        {
            "hdd": rng.uniform(0.0, 18.0, size=48),
            "cdd": rng.uniform(0.0, 14.0, size=48),
            "occ": rng.uniform(0.4, 1.0, size=48),
        }
    )
    y = 410.0 + 12.75 * X["hdd"] + 16.40 * X["cdd"] + 88.0 * X["occ"] + rng.normal(0.0, 2.0, size=48)

    est = MLRBaselineWrapper(enforce_k_max=False, seed=42).fit(X, y)
    yhat = est.predict(X)
    mgr = BaselineLockManager(tmp_path / "locks")
    coefs = {"intercept": float(est.intercept_), **{k: float(v) for k, v in est.coef_raw_.items()}}

    rec_v1 = mgr.lock_baseline(
        run_id="run-001",
        target="electricity_kwh",
        model_name="MLR",
        family="F1",
        is_parametric=True,
        fitted_artifact=est,
        formula_or_architecture=est.formula_text(),
        coefficients=coefs,
        selected_features=list(est.selected_cols_),
        vsg_snapshot={"accepted": True, "included": list(X.columns)},
        tcp_snapshot={"alpha": 0.05, "confidence": 0.95},
        baseline_window={"start": "2023-01-01", "end": "2023-12-31", "n_obs": 48, "granularity": "weekly"},
        thresholds={"max_cvrmse": 20.0, "max_nmbe": 7.0, "min_r2": 0.60},
        random_seed=42,
        calibration_metrics={"cvrmse": 1.2, "nmbe": 0.0, "r2": 0.995},
        composite_score=96.4,
        validity_envelope={"drivers": list(X.columns), "n_training_obs": 48},
        locked_by="senior.mv.engineer",
        notes="Initial ISO 50006 baseline lock",
    )

    # 1. Strict attribute & nested mapping immutability
    with pytest.raises(BaselineImmutabilityError):
        rec_v1.model_name = "Tampered"  # type: ignore[misc]
    with pytest.raises(BaselineImmutabilityError):
        del rec_v1.model_name
    with pytest.raises(TypeError):
        rec_v1.coefficients["hdd"] = 999.0  # type: ignore[index]
    with pytest.raises(BaselineImmutabilityError):
        mgr.update_locked_baseline(rec_v1, notes="illegal in-place edit")
    with pytest.raises(BaselineImmutabilityError):
        mgr.lock_baseline(
            run_id="run-001",
            target="electricity_kwh",
            model_name="MLR",
            family="F1",
            is_parametric=True,
            fitted_artifact=est,
            formula_or_architecture=est.formula_text(),
            explicit_version=1,
        )

    # 2. Appending v2 keeps v1 intact and chains previous_lock_sha256
    rec_v2 = mgr.lock_baseline(
        run_id="run-001",
        target="electricity_kwh",
        model_name="MLR_v2",
        family="F1",
        is_parametric=True,
        fitted_artifact=est,
        formula_or_architecture=est.formula_text(),
        coefficients=coefs,
        selected_features=list(est.selected_cols_),
        locked_by="senior.mv.engineer",
    )
    assert rec_v2.version == 2
    assert rec_v2.previous_lock_sha256 == rec_v1.record_sha256
    assert len(mgr.get_history("run-001", "electricity_kwh")) == 2
    assert len(mgr.read_audit_ledger()) == 2

    loaded = mgr.load_verified_artifact(rec_v1)
    assert np.allclose(loaded.predict(X), yhat)

    # 3. Excel live-formula export & pure-Python formula round-trip verification
    xlsx_out = tmp_path / "locked_v1_formulas.xlsx"
    export_locked_baseline_excel(rec_v1, X, y, xlsx_out)
    rt = verify_excel_formula_roundtrip(xlsx_out, yhat, y, tol=1e-9)
    assert rt.passed is True
    assert rt.all_cells_are_formulas is True
    assert rt.sample_formula.startswith("=Coefficients!$B$2")
    assert rt.max_abs_prediction_diff < 1e-10
    assert rt.rmse_abs_diff < 1e-10

    # 4. Corrupting 1 byte of the joblib file MUST raise ArtifactIntegrityError BEFORE joblib.load!
    art_bytes = bytearray(Path(rec_v2.artifact_path).read_bytes())
    art_bytes[-1] ^= 0xFF
    Path(rec_v2.artifact_path).write_bytes(bytes(art_bytes))
    with pytest.raises(ArtifactIntegrityError, match="SHA-256 verification FAILED"):
        mgr.load_verified_artifact(rec_v2)


def test_core_pipeline_non_commingled_registers_sha256_store_and_cli_e2(tmp_path: Path, eb_home: Path) -> None:
    """End-to-end verification of core/pipeline.py separated registers, store SHA-256 guard, and CLI E2 subcommands."""
    from energybaseline.db.session import init_database

    _ = eb_home
    init_database()
    df = pd.read_csv("samples/monthly_building.csv")
    rid = store.new_run(None, ["Energy_kWh"], {"hpo_trials": "0", "nn_max_epochs": "10", "enable_mining": "false"})
    run, bundles = run_analysis(
        df,
        "Month",
        ["Energy_kWh"],
        {"hpo_trials": "0", "nn_max_epochs": "10", "enable_mining": "false"},
        dataset_sha="phase6-sha",
        dataset_info={"name": "monthly_building.csv", "rows": len(df), "cols": df.shape[1]},
        artifact_fn=lambda t, m: str(store.artifact_path(rid, t, m)),
    )
    store.save_run(rid, run, bundles)
    tr = run.targets["Energy_kWh"]
    assert tr.status == "ok"

    # Qualified models outside Top-5 MUST live in qualified_not_shortlisted_register and NEVER in tr.register!
    qns_names = {r["model"] for r in tr.qualified_not_shortlisted_register}
    rej_names = {r["model"] for r in tr.register}
    short_names = {s["name"] for s in tr.shortlist}
    assert len(qns_names) >= 1
    assert qns_names.isdisjoint(rej_names)
    assert short_names.isdisjoint(rej_names)
    assert short_names.isdisjoint(qns_names)

    # Non-parametric Top-5 candidates must carry labelled surrogate with fidelity_r2
    for s in tr.shortlist:
        if s["formula"]["kind"] != "equation":
            surr = s["formula"].get("surrogate")
            assert isinstance(surr, dict) and surr["is_surrogate"] is True
            assert 0.0 <= float(surr["fidelity_r2"]) <= 1.0

    # Lock via store.lock_baseline creates immutable versioned manifest + SHA-256 verified artifact
    winner = tr.shortlist[0]["name"]
    lock_info = store.lock_baseline(rid, "Energy_kWh", winner, "Gate G6 end-to-end lock verification")
    assert lock_info.get("version") == 1
    assert len(str(lock_info.get("artifact_sha256", ""))) == 64

    # Tampering with saved bundle in store raises ArtifactIntegrityError on load_bundle
    ap = store.artifact_path(rid, "Energy_kWh", winner)
    orig = ap.read_bytes()
    try:
        tampered = bytearray(orig)
        tampered[-1] ^= 0x55
        ap.write_bytes(bytes(tampered))
        with pytest.raises(ArtifactIntegrityError):
            store.load_bundle(rid, "Energy_kWh", winner)
    finally:
        ap.write_bytes(orig)

    # CLI E2 commands
    sr_json = tmp_path / "e2_scoring_register.json"
    bl_json = tmp_path / "e2_locked_baseline.json"
    assert cli.main(["scoring-register", "samples/monthly_building.csv", "--out", str(sr_json)]) == 0
    assert cli.main(["baseline-lock", "samples/monthly_building.csv", "--out", str(bl_json)]) == 0
    sr_data = json.loads(sr_json.read_text(encoding="utf-8"))
    bl_data = json.loads(bl_json.read_text(encoding="utf-8"))
    assert sr_data["ok"] is True and sr_data["top5_pre_lock_view"]["visible_before_lock"] is True
    assert bl_data["ok"] is True and bl_data["excel_formula_roundtrip"]["passed"] is True
