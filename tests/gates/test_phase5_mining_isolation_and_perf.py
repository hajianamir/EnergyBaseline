"""Phase 5 Gate G5: Clustering validity + bootstrap ARI, MT-5 label isolation proof, LTTB/M4, EDA attach, & 1M benchmark."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energybaseline import cli
from energybaseline.core.models import Candidate, ColsDesign, Ctx, MeanEst, evaluate_candidate, make_folds
from energybaseline.core.tcp import TestConfigPanel
from energybaseline.core.vsg import VariableSelectionGate
from energybaseline.mining import (
    DESCRIPTIVE_LABEL,
    DescriptiveOnlyViolationError,
    RegimeLabelLeakageError,
    assert_descriptive_only,
    attach_descriptive_cluster_labels,
    build_optional_eda_attachment,
    compute_bootstrap_ari,
    compute_cluster_validity_indices,
    discover_operating_regimes,
    lttb_downsample,
    m4_downsample,
    strip_discovery_cluster_labels,
)
from energybaseline.models import (
    F1LinearBaseline,
    F2ChangePointBaseline,
    F3RegularizedBaseline,
    F4BasisBaseline,
    F5EnsembleBaseline,
    F6NeuralBaseline,
    F7TimeSeriesBaseline,
    F8RegimeBaseline,
    F9DecompositionBaseline,
    F10ConvexStackingBaseline,
    evaluate_f1_f10_catalog,
)
from scripts.benchmarks.bench_1m import run_benchmark_1m


def test_clustering_validity_indices_and_bootstrap_ari_stable_vs_unstable() -> None:
    rng = np.random.default_rng(5151)
    # 1. Well-separated 3-cluster operational regimes
    c1 = rng.normal(loc=[-6.0, -6.0], scale=0.55, size=(90, 2))
    c2 = rng.normal(loc=[0.0, 6.5], scale=0.55, size=(90, 2))
    c3 = rng.normal(loc=[6.5, -5.0], scale=0.55, size=(90, 2))
    Z_stable = np.vstack([c1, c2, c3])
    df_stable = pd.DataFrame(Z_stable, columns=["chiller_load", "oat_c"])

    rep_stable = discover_operating_regimes(
        df_stable, ["chiller_load", "oat_c"], method="kmeans", k_range=(2, 5), n_boot=25, seed=42
    )
    assert rep_stable.selected_k == 3
    assert rep_stable.validity.silhouette > 0.70
    assert rep_stable.validity.calinski_harabasz > 500.0
    assert rep_stable.validity.davies_bouldin < 0.50
    assert rep_stable.stability.mean_ari > 0.90
    assert rep_stable.stability.stability_grade == "stable"
    assert rep_stable.allowed_as_model_input is False

    # 2. Unstructured isotropic spherical Gaussian noise forced into k=6 partitions -> unstable bootstrap ARI
    Z_unstable = rng.normal(loc=0.0, scale=1.0, size=(300, 10))
    stab_unstable = compute_bootstrap_ari(Z_unstable, k=6, method="kmeans", n_boot=25, seed=42)
    val_unstable = compute_cluster_validity_indices(Z_unstable, np.arange(300) % 6, seed=42)
    assert val_unstable.silhouette < 0.15
    assert stab_unstable.mean_ari < 0.35
    assert stab_unstable.stability_grade == "unstable"
    assert rep_stable.stability.mean_ari - stab_unstable.mean_ari > 0.55

    # Verify GMM and Ward clustering stability also work cleanly on stable clusters
    assert compute_bootstrap_ari(Z_stable, k=3, method="gmm", n_boot=12, seed=42).mean_ari > 0.85
    assert compute_bootstrap_ari(Z_stable, k=3, method="ward", n_boot=12, seed=42).mean_ari > 0.85


def test_mt5_proof_discovery_cluster_labels_never_reach_any_model() -> None:
    rng = np.random.default_rng(5252)
    n = 120
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    df = pd.DataFrame(
        {
            "timestamp": idx.astype(str),
            "oat_c": np.concatenate([rng.normal(8.0, 1.0, n // 2), rng.normal(26.0, 1.0, n // 2)]),
            "occ": np.concatenate([rng.normal(0.3, 0.05, n // 2), rng.normal(0.9, 0.05, n // 2)]),
        },
        index=idx,
    )
    y = 100.0 + 3.0 * df["oat_c"].to_numpy() + 25.0 * df["occ"].to_numpy() + rng.normal(0.0, 0.8, size=n)
    df["energy_kwh"] = y

    rep = discover_operating_regimes(df, ["oat_c", "occ"], method="kmeans", k_range=(2, 3), n_boot=10, seed=42)
    tagged_df = attach_descriptive_cluster_labels(df, rep.labels, col_name="regime_cluster_k")
    X_leaked = tagged_df[["oat_c", "occ", "regime_cluster_k"]]

    # 1. Direct pass of leaked DataFrame or DescriptiveClusterLabels array into EVERY F1..F10 family MUST raise RegimeLabelLeakageError
    all_estimators = [
        F1LinearBaseline("ols"),
        F2ChangePointBaseline("3P-cooling", n_grid=15, n_boot_knots=0),
        F3RegularizedBaseline("ridge"),
        F4BasisBaseline("polynomial"),
        F5EnsembleBaseline("random_forest", n_estimators=20),
        F6NeuralBaseline("mlp", min_samples=30, max_epochs=20),
        F7TimeSeriesBaseline("arimax", order=(1, 0, 0)),
        F8RegimeBaseline(calendar_regime="weekday_weekend"),
        F9DecompositionBaseline("stl_fourier", period=12),
        F10ConvexStackingBaseline(cv_folds=3),
    ]
    for est in all_estimators:
        with pytest.raises(RegimeLabelLeakageError):
            est.fit(X_leaked, y)
        with pytest.raises(RegimeLabelLeakageError):
            est.fit(rep.labels, y)

    # F8 regime_col explicitly blocks discovery cluster columns too
    with pytest.raises(RegimeLabelLeakageError):
        F8RegimeBaseline(regime_col="__cluster_label__")
    with pytest.raises(RegimeLabelLeakageError):
        F8RegimeBaseline(regime_col="regime_cluster_k")

    # Core portfolio evaluate_candidate also blocks leaked DataFrames
    cand = Candidate("Mean", "Linear", 1, "linear", lambda c: ColsDesign(only=[]), lambda p, c: MeanEst(), scale=False)
    with pytest.raises(RegimeLabelLeakageError):
        evaluate_candidate(cand, X_leaked, y, make_folds(n, 3, "blocked_kfold", 1), Ctx(target="energy_kwh"))

    # 2. Automatic stripping in strip_discovery_cluster_labels & evaluate_f1_f10_catalog guarantees zero leakage
    clean_df, removed = strip_discovery_cluster_labels(tagged_df)
    assert removed == ["regime_cluster_k"]
    assert "regime_cluster_k" not in clean_df.columns

    vsg = VariableSelectionGate(
        clean_df.reset_index(drop=True), targets=["energy_kwh"], time_col="timestamp", dataset_sha256="mt5_sha"
    )
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    tcp.lock_preregistration()

    # Even if caller passes tagged_df (with cluster column attached!) to evaluate_f1_f10_catalog, it strips it before fitting
    entries = evaluate_f1_f10_catalog(
        tagged_df.reset_index(drop=True),
        target_col="energy_kwh",
        predictor_cols=["oat_c", "occ", "regime_cluster_k"],
        vsg=vsg,
        tcp=tcp,
        lite_profile=True,
        seed=42,
    )
    for entry in entries:
        assert "regime_cluster_k" not in entry.formula_summary
        assert "__cluster_label__" not in entry.formula_summary


def test_lttb_and_m4_downsampling_bounds_and_chart_sizes() -> None:
    n = 50_000
    x = np.linspace(0.0, 100.0, n)
    y_smooth = 50.0 + 20.0 * np.sin(x) + 5.0 * np.cos(3.0 * x)

    lttb_smooth = lttb_downsample(x, y_smooth, threshold=1000)
    assert lttb_smooth.n_input == n
    assert lttb_smooth.n_output == 1000
    assert lttb_smooth.indices[0] == 0 and lttb_smooth.indices[-1] == n - 1
    assert np.all(np.diff(lttb_smooth.indices) > 0)
    assert lttb_smooth.rmse_interp_error < 0.60

    # Inject sharp extreme spikes to test exact M4 & LTTB extremum preservation
    y_spiked = y_smooth.copy()
    y_spiked[12_345] = 250.0
    y_spiked[37_890] = -120.0

    m4 = m4_downsample(x, y_spiked, pixel_width=400)
    assert m4.n_input == n
    assert m4.n_output <= 1600
    assert np.all(np.diff(m4.indices) > 0)
    assert m4.envelope_max_error == 0.0
    assert m4.envelope_min_error == 0.0
    assert 250.0 in m4.y and -120.0 in m4.y


def test_optional_eda_attachment_strictly_descriptive_not_inferential() -> None:
    df = pd.read_csv("samples/monthly_building.csv")
    att = build_optional_eda_attachment(df, target_col="Energy_kWh")
    payload = att.to_dict()
    assert payload["label"] == DESCRIPTIVE_LABEL
    assert payload["classification"] == DESCRIPTIVE_LABEL
    assert payload["inferential_claims"] is False
    assert_descriptive_only(payload)

    # Negative guards: injecting p-value keys or inferential significance claims MUST fail
    bad_key = json.loads(json.dumps(payload))
    bad_key["association_table"][0]["p_value"] = 0.002
    with pytest.raises(DescriptiveOnlyViolationError):
        assert_descriptive_only(bad_key)

    bad_claim = json.loads(json.dumps(payload))
    bad_claim["notes"].append("Temperature is statistically significant at p < 0.05.")
    with pytest.raises(DescriptiveOnlyViolationError):
        assert_descriptive_only(bad_claim)


def test_1m_row_benchmark_and_phase5_cli_e2_commands(tmp_path: Path) -> None:
    bench = run_benchmark_1m(total_rows=1_000_000, page_size=512, max_cached_pages=8)
    assert bench["ok"] is True
    assert bench["table_paging"]["total_source_rows"] == 1_000_000
    assert bench["table_paging"]["full_frame_materialized"] is False
    assert bench["table_paging"]["peak_resident_rows"] <= 4096
    assert bench["downsampling"]["lttb_2000"]["n_output"] == 2000
    assert bench["downsampling"]["m4_800px"]["envelope_max_error"] == 0.0
    assert bench["total_wall_seconds"] < 30.0

    cv_json = tmp_path / "clustering_e2.json"
    rc1 = cli.main(["clustering-validity", "samples/monthly_building.csv", "--out", str(cv_json)])
    assert rc1 == 0
    cv_data = json.loads(cv_json.read_text(encoding="utf-8"))
    assert cv_data["ok"] is True
    assert cv_data["clustering"]["allowed_as_model_input"] is False
    assert cv_data["eda_attachment"]["label"] == DESCRIPTIVE_LABEL

    mc_json = tmp_path / "model_catalog_e2.json"
    rc2 = cli.main(["model-catalog", "samples/monthly_building.csv", "--lite", "--out", str(mc_json)])
    assert rc2 == 0
    mc_data = json.loads(mc_json.read_text(encoding="utf-8"))
    assert mc_data["ok"] is True
    assert len(mc_data["register"]) >= 10
