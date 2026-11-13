"""Phase 5 Gate G5: Planted-effect recovery, monotonicity, simplex, breakdown, and gating contracts for F1..F10."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energybaseline.core.tcp import TestConfigPanel
from energybaseline.core.vsg import ComplianceGateError, VariableSelectionGate
from energybaseline.models import (
    SUPPORTED_F2_TYPES,
    SUPPORTED_F5_VARIANTS,
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
    OptionalBackendSkippedError,
    SampleSizeGateSkippedError,
    align_oof_predictions,
    compute_feature_consensus_f8,
    compute_kendalls_w,
    compute_regularization_path,
    compute_variable_base_degree_days,
    evaluate_f1_f10_catalog,
    recommend_autocorr_embargo_gap,
)


def test_f1_ols_wls_hac_planted_effect_recovery_and_ci() -> None:
    rng = np.random.default_rng(101)
    n = 240
    x1 = rng.normal(0.0, 2.0, size=n)
    x2 = rng.uniform(0.0, 10.0, size=n)
    # Autocorrelated AR(1) disturbances for HAC verification
    eps = np.zeros(n, dtype=float)
    z = rng.normal(0.0, 1.2, size=n)
    for t in range(1, n):
        eps[t] = 0.75 * eps[t - 1] + z[t]
    y = 50.0 + 3.5 * x1 - 1.8 * x2 + eps
    X = pd.DataFrame({"x1": x1, "x2": x2})

    ols = F1LinearBaseline("ols", confidence=0.95).fit(X, y)
    hac = F1LinearBaseline("hac", confidence=0.95).fit(X, y)
    assert abs(ols.coefficients_["intercept"] - 50.0) < 0.8
    assert abs(hac.coefficients_["x1"] - 3.5) < 0.25
    assert abs(hac.coefficients_["x2"] - (-1.8)) < 0.25
    # Under strong positive AR(1) residuals, HAC SE exceeds naive OLS SE on persistent regressors or provides valid CI
    lo1, hi1 = hac.ci_["x1"]
    assert lo1 < 3.5 < hi1

    # Heteroskedastic WLS recovery
    sigma_i = 0.3 + 0.5 * x2
    y_het = 40.0 + 2.75 * x1 + rng.normal(0.0, sigma_i)
    wls = F1LinearBaseline("wls", confidence=0.95).fit(X, y_het, weights=1.0 / (sigma_i**2))
    assert abs(wls.coefficients_["x1"] - 2.75) < 0.20
    assert "wls" in wls.formula_text()


def test_f2_ashrae_2p_3p_4p_5p_planted_knots_and_hdd_cdd() -> None:
    rng = np.random.default_rng(202)
    temp = np.linspace(-2.0, 34.0, 220)
    dd = compute_variable_base_degree_days(temp, heating_base=14.0, cooling_base=20.0)
    assert np.allclose(dd["hdd"], np.maximum(0.0, 14.0 - temp))
    assert np.allclose(dd["cdd"], np.maximum(0.0, temp - 20.0))

    # Planted 5P model: base=100, T_bh=13.0 (slope 4.5), T_bc=21.0 (slope 6.0)
    y_5p = (
        100.0
        + 4.5 * np.maximum(0.0, 13.0 - temp)
        + 6.0 * np.maximum(0.0, temp - 21.0)
        + rng.normal(0.0, 0.8, size=temp.size)
    )
    X = pd.DataFrame({"oat_temp_c": temp})
    m5 = F2ChangePointBaseline("5P", n_grid=40, n_boot_knots=30, seed=42).fit(X, y_5p)
    assert abs(m5.knots_[0] - 13.0) < 0.9
    assert abs(m5.knots_[1] - 21.0) < 0.9
    assert abs(m5.coefficients_["base_load"] - 100.0) < 1.5
    assert abs(m5.coefficients_["heating_slope"] - 4.5) < 0.35
    assert abs(m5.coefficients_["cooling_slope"] - 6.0) < 0.35
    assert "heating_balance_point" in m5.knot_ci_ and "cooling_balance_point" in m5.knot_ci_

    # Verify all 5 ASHRAE variants fit cleanly and recover high R2 on corresponding planted signals
    y_3c = 80.0 + 5.0 * np.maximum(0.0, temp - 18.0) + rng.normal(0.0, 0.5, size=temp.size)
    m3c = F2ChangePointBaseline("3P-cooling", n_grid=35, n_boot_knots=15, seed=42).fit(X, y_3c)
    assert abs(m3c.knots_[0] - 18.0) < 0.8 and m3c.train_metrics_["r2"] > 0.99

    y_3h = 80.0 + 4.0 * np.maximum(0.0, 15.0 - temp) + rng.normal(0.0, 0.5, size=temp.size)
    m3h = F2ChangePointBaseline("3P-heating", n_grid=35, n_boot_knots=15, seed=42).fit(X, y_3h)
    assert abs(m3h.knots_[0] - 15.0) < 0.8 and m3h.train_metrics_["r2"] > 0.99

    for mtype in SUPPORTED_F2_TYPES:
        m_any = F2ChangePointBaseline(mtype, n_grid=20, n_boot_knots=0).fit(
            X, y_5p if mtype != "2P" else 50.0 + 2.0 * temp
        )
        assert m_any.predict(X).shape == (temp.size,)


def test_f3_regularized_path_outlier_robustness_and_above_3000_rows() -> None:
    rng = np.random.default_rng(303)
    n = 3400  # Exceeds legacy 3,000-row QuantileRegressor cap
    X = pd.DataFrame(
        {
            "driver_true": rng.normal(0.0, 1.5, size=n),
            "noise_1": rng.normal(0.0, 1.0, size=n),
            "noise_2": rng.normal(0.0, 1.0, size=n),
        }
    )
    y_clean = 75.0 + 4.0 * X["driver_true"].to_numpy() + rng.normal(0.0, 0.5, size=n)
    y_contam = y_clean.copy()
    spike_idx = rng.choice(n, size=180, replace=False)
    y_contam[spike_idx] += 350.0 * np.sign(X["driver_true"].to_numpy()[spike_idx] - 2.5)

    huber = F3RegularizedBaseline("huber", alpha=0.01).fit(X, y_contam)
    quant = F3RegularizedBaseline("quantile", alpha=0.0, quantile=0.5).fit(X, y_contam)
    ridge = F3RegularizedBaseline("ridge", alpha=1.0).fit(X, y_clean)
    lasso = F3RegularizedBaseline("lasso", alpha=0.25).fit(X, y_clean)
    enet = F3RegularizedBaseline("elasticnet", alpha=0.2, l1_ratio=0.5).fit(X, y_clean)

    assert abs(huber.coefficients_["driver_true"] - 4.0) < 0.25
    assert abs(quant.coefficients_["driver_true"] - 4.0) < 0.20
    assert abs(ridge.coefficients_["driver_true"] - 4.0) < 0.15
    assert abs(lasso.coefficients_["noise_1"]) < 0.05
    assert enet.train_metrics_["r2"] > 0.95

    path = compute_regularization_path(X.iloc[:400], y_clean[:400], variant="lasso", alphas=[0.001, 0.1, 2.0, 20.0])
    assert path["nonzero_counts"][0] >= path["nonzero_counts"][-1]


def test_f4_basis_splines_polynomial_and_interactions_planted_recovery() -> None:
    rng = np.random.default_rng(404)
    n = 260
    x1 = rng.uniform(-3.0, 3.0, size=n)
    x2 = rng.uniform(-2.0, 2.0, size=n)
    X = pd.DataFrame({"x1": x1, "x2": x2})

    # Planted interaction model
    y_inter = 30.0 + 2.5 * x1 - 1.5 * x2 + 1.8 * (x1 - np.mean(x1)) * (x2 - np.mean(x2)) + rng.normal(0.0, 0.25, size=n)
    m_int = F4BasisBaseline("interaction", alpha=0.0).fit(X, y_inter)
    assert abs(m_int.coefficients_["x1*x2"] - 1.8) < 0.10
    assert m_int.train_metrics_["r2"] > 0.99

    # Planted smooth nonlinear additive model for GAM B-spline & polynomial
    y_smooth = 100.0 + 3.0 * (x1**2) + 4.0 * np.sin(x2) + rng.normal(0.0, 0.35, size=n)
    m_gam = F4BasisBaseline("gam_spline", degree=3, n_knots=7, alpha=1e-3).fit(X, y_smooth)
    m_poly = F4BasisBaseline("polynomial", degree=3, alpha=1e-3).fit(X, y_smooth)
    assert m_gam.train_metrics_["r2"] > 0.98
    assert m_poly.train_metrics_["r2"] > 0.97
    pe = m_gam.partial_effect("x1", np.array([-2.0, 0.0, 2.0]))
    assert pe[0] > pe[1] and pe[2] > pe[1]  # Symmetric U-shaped quadratic recovery


@pytest.mark.parametrize("variant", SUPPORTED_F5_VARIANTS)
def test_f5_ensembles_planted_recovery_and_strict_monotonic_constraints(variant: str) -> None:
    rng = np.random.default_rng(505)
    n = 240
    cdd = rng.uniform(0.0, 20.0, size=n)
    eff_index = rng.uniform(0.0, 10.0, size=n)
    # Planted monotone increasing in cdd (+1), monotone decreasing in eff_index (-1)
    y = 120.0 + 5.0 * cdd + 0.12 * (cdd**2) - 3.5 * eff_index + rng.normal(0.0, 1.5, size=n)
    X = pd.DataFrame({"cdd": cdd, "eff_index": eff_index})

    est = F5EnsembleBaseline(
        variant,
        monotonic_constraints={"cdd": 1, "eff_index": -1},
        n_estimators=80,
        max_depth=7,
        seed=42,
    ).fit(X, y)
    r2_floor = 0.82 if variant == "extra_trees" else 0.94
    assert est.train_metrics_["r2"] > r2_floor
    if variant == "extra_trees":
        est_unconstrained = F5EnsembleBaseline("extra_trees", n_estimators=80, max_depth=8, seed=42).fit(X, y)
        assert est_unconstrained.train_metrics_["r2"] > 0.98

    # Verify strict monotonicity along cdd (+1) holding eff_index fixed
    grid_pos = pd.DataFrame({"cdd": np.linspace(0.0, 20.0, 40), "eff_index": np.full(40, 5.0)})
    preds_pos = est.predict(grid_pos)
    assert np.all(np.diff(preds_pos) >= -1e-7), f"{variant} violated +1 monotonicity on cdd"

    # Verify strict monotonicity along eff_index (-1) holding cdd fixed
    grid_neg = pd.DataFrame({"cdd": np.full(40, 10.0), "eff_index": np.linspace(0.0, 10.0, 40)})
    preds_neg = est.predict(grid_neg)
    assert np.all(np.diff(preds_neg) <= 1e-7), f"{variant} violated -1 monotonicity on eff_index"
    assert not Path("catboost_info").exists()


def test_f6_neural_mlp_and_pytorch_lstm_planted_recovery_and_sample_size_skip() -> None:
    rng = np.random.default_rng(606)
    n = 180
    idx = pd.date_range("2025-01-01", periods=n, freq="h")
    x1 = np.sin(np.linspace(0.0, 8.0 * np.pi, n))
    x2 = rng.normal(0.0, 1.0, size=n)
    y = 50.0 + 12.0 * x1 + 4.5 * x2 + rng.normal(0.0, 0.6, size=n)
    X = pd.DataFrame({"x1": x1, "x2": x2}, index=idx)

    mlp = F6NeuralBaseline("mlp", min_samples=60, hidden_sizes=(32, 16), max_epochs=140, lr=8e-3, seed=42).fit(X, y)
    lstm = F6NeuralBaseline(
        "lstm", min_samples=60, hidden_sizes=(24,), seq_len=4, max_epochs=120, lr=1e-2, seed=42
    ).fit(X, y)
    assert mlp.train_metrics_["r2"] > 0.90
    assert lstm.train_metrics_["r2"] > 0.85

    # Warm-up OOF timestamp alignment contract (Phase 4 §9 Risk 1)
    s_mlp = mlp.predict_series(X)
    s_lstm_trim = lstm.predict_series(X, drop_warmup=True)
    common_idx, y_al, aligned = align_oof_predictions({"mlp": s_mlp, "lstm": s_lstm_trim}, pd.Series(y, index=idx))
    assert len(common_idx) == n - (lstm.seq_len - 1)
    assert aligned["mlp"].shape == aligned["lstm"].shape == y_al.shape

    # Small-data skip gate & Lite-profile clean skip
    with pytest.raises(SampleSizeGateSkippedError):
        F6NeuralBaseline("mlp", min_samples=100).fit(X.iloc[:40], y[:40])
    with pytest.raises(OptionalBackendSkippedError):
        F6NeuralBaseline("lstm", min_samples=30, force_lite_profile=True).fit(X, y)

    # Autocorrelation-aware embargo gap (Phase 4 §9 Risk 2)
    ar_series = np.cumsum(rng.normal(0.0, 1.0, size=300))
    gap_high = recommend_autocorr_embargo_gap(ar_series, steps_per_day=4.0, max_lag=2)
    gap_wn = recommend_autocorr_embargo_gap(rng.normal(0.0, 1.0, size=300), steps_per_day=4.0, max_lag=2)
    assert gap_high > gap_wn >= 4


def test_f7_sarimax_arimax_and_ets_planted_recovery() -> None:
    rng = np.random.default_rng(707)
    n = 120
    idx = pd.date_range("2020-01-01", periods=n, freq="ME")
    temp = 15.0 + 10.0 * np.sin(2.0 * np.pi * np.arange(n) / 12.0) + rng.normal(0.0, 1.2, size=n)
    u = np.zeros(n, dtype=float)
    for t in range(1, n):
        u[t] = 0.5 * u[t - 1] + rng.normal(0.0, 0.7)
    y = 200.0 + 3.4 * temp + u
    X = pd.DataFrame({"temp": temp}, index=idx)

    arx = F7TimeSeriesBaseline("arimax", order=(1, 0, 0)).fit(X.iloc[:96], y[:96])
    assert abs(arx.exog_params_["temp"] - 3.4) < 0.25
    fc_arx = arx.predict(X.iloc[96:])
    assert np.sqrt(np.mean((y[96:] - fc_arx) ** 2)) < 3.0

    ets = F7TimeSeriesBaseline("ets", seasonal_periods=12, trend="add", seasonal="add").fit(X.iloc[:96], y[:96])
    fc_ets = ets.predict(X.iloc[96:])
    assert np.sqrt(np.mean((y[96:] - fc_ets) ** 2)) < 4.0


def test_f8_regime_segmented_recovery_and_kendalls_w_rank_agreement() -> None:
    rng = np.random.default_rng(808)
    n = 200
    idx = pd.date_range("2025-03-01", periods=n, freq="D")
    temp = rng.uniform(5.0, 30.0, size=n)
    prod = rng.uniform(50.0, 150.0, size=n)
    noise = rng.normal(0.0, 0.5, size=n)
    mode = np.where(idx.dayofweek >= 5, "standby", "production")
    # Distinct regime physics: production slope on prod = 2.2, standby slope on prod = 0.2
    y = np.where(mode == "production", 100.0 + 2.2 * prod + 1.5 * temp, 40.0 + 0.2 * prod + 0.5 * temp) + noise
    X = pd.DataFrame({"prod": prod, "temp": temp, "plant_mode": mode}, index=idx)

    f8 = F8RegimeBaseline(regime_col="plant_mode", seed=42).fit(X, y)
    assert f8.train_metrics_["r2"] > 0.99
    b_prod = f8.regime_models_["production"][1]
    b_stby = f8.regime_models_["standby"][1]
    assert abs(float(b_prod[0]) - 2.2) < 0.10
    assert abs(float(b_stby[0]) - 0.2) < 0.10
    assert f8.kendalls_w_ is not None

    # Calendar-regime variant
    f8_cal = F8RegimeBaseline(calendar_regime="weekday_weekend", seed=42).fit(X[["prod", "temp"]], y)
    assert f8_cal.train_metrics_["r2"] > 0.99

    # Kendall's W concordance: strong agreement vs conflicting rankings
    w_high = compute_kendalls_w(
        {
            "pearson": [0.92, 0.65, 0.30, 0.05],
            "spearman": [0.90, 0.61, 0.28, 0.08],
            "ols": [4.5, 2.1, 0.9, 0.1],
            "rf": [0.55, 0.28, 0.13, 0.04],
        },
        item_names=["prod", "temp", "wind", "noise"],
    )
    assert w_high.w == pytest.approx(1.0, abs=1e-9)
    assert w_high.consensus_order == ("prod", "temp", "wind", "noise")
    assert w_high.p_value < 0.01

    w_low = compute_kendalls_w(
        {
            "m1": [1, 2, 3, 4],
            "m2": [4, 3, 2, 1],
            "m3": [2, 4, 1, 3],
            "m4": [3, 1, 4, 2],
        },
        item_names=["a", "b", "c", "d"],
        input_is_ranks=True,
    )
    assert w_low.w == pytest.approx(0.0, abs=1e-9)

    X_cons = pd.DataFrame({"prod": prod, "temp": temp, "pure_noise": rng.normal(0.0, 1.0, size=n)})
    cons = compute_feature_consensus_f8(X_cons, 50.0 + 3.0 * prod + 1.2 * temp + rng.normal(0.0, 0.5, size=n))
    assert cons["kendalls_w"]["w"] > 0.85
    assert cons["kendalls_w"]["consensus_order"][0] == "prod"


def test_f9_decomposition_hybrid_planted_recovery_and_exact_variable_breakdown() -> None:
    rng = np.random.default_rng(909)
    n = 144
    t = np.arange(n, dtype=float)
    hdd = rng.uniform(0.0, 15.0, size=n)
    occ = rng.uniform(0.2, 1.0, size=n)
    seasonal = 18.0 * np.sin(2.0 * np.pi * t / 12.0) - 9.0 * np.cos(2.0 * np.pi * t / 12.0)
    y = (
        250.0
        + 0.35 * (t - np.mean(t))
        + seasonal
        + 5.2 * (hdd - np.mean(hdd))
        + 30.0 * (occ - np.mean(occ))
        + rng.normal(0.0, 0.6, size=n)
    )
    X = pd.DataFrame({"hdd": hdd, "occ": occ})

    f9 = F9DecompositionBaseline("stl_fourier", period=12, fourier_order=2).fit(X, y)
    assert abs(float(f9.exog_coef_[0]) - 5.2) < 0.15
    assert abs(float(f9.exog_coef_[1]) - 30.0) < 0.90
    assert abs(f9.trend_slope_ - 0.35) < 0.05
    assert f9.stl_seasonal_strength_ > 0.5

    bd = f9.decompose_predict(X)
    assert list(bd.columns) == ["base_intercept", "trend", "seasonal", "hdd", "occ", "predicted_total"]
    summed = bd[["base_intercept", "trend", "seasonal", "hdd", "occ"]].sum(axis=1).to_numpy()
    assert np.allclose(summed, bd["predicted_total"].to_numpy(), atol=1e-9)
    assert np.allclose(f9.predict(X), bd["predicted_total"].to_numpy(), atol=1e-9)
    assert "F9:stl_fourier" in f9.formula_text()

    with pytest.raises(OptionalBackendSkippedError):
        F9DecompositionBaseline("prophet", period=12).fit(X, y)


def test_f10_convex_stacking_simplex_weights_and_oof_blend() -> None:
    rng = np.random.default_rng(1010)
    n = 200
    x1 = rng.uniform(-3.0, 3.0, size=n)
    x2 = rng.uniform(0.0, 10.0, size=n)
    # Hybrid linear + nonlinear planted process where convex combination beats individual misspecified learners
    y = 80.0 + 4.0 * x1 + 2.5 * (x1**2) + 3.2 * x2 + rng.normal(0.0, 0.8, size=n)
    X = pd.DataFrame({"x1": x1, "x2": x2})

    f10 = F10ConvexStackingBaseline(cv_folds=4, gap=1, seed=42).fit(X, y)
    assert np.all(f10.weights_ >= -1e-12)
    assert abs(float(np.sum(f10.weights_)) - 1.0) < 1e-9
    assert f10.oof_metrics_["r2"] > 0.95
    assert f10.train_metrics_["r2"] > 0.97
    assert "simplex sum=1.0000" in f10.formula_text()


def test_gated_f1_f10_catalog_requires_vsg_tcp_and_builds_complete_register() -> None:
    df = pd.read_csv("samples/monthly_building.csv")
    vsg = VariableSelectionGate(
        df,
        targets=["Energy_kWh"],
        time_col="Month",
        dataset_sha256="phase5_gate_test_sha",
    )
    tcp = TestConfigPanel(vsg)

    # PIPE-1 auto-fail check: inferential F1..F10 evaluation MUST refuse before VSG & TCP acceptance
    with pytest.raises(ComplianceGateError):
        evaluate_f1_f10_catalog(df, target_col="Energy_kWh", vsg=vsg, tcp=tcp)

    vsg.accept()
    with pytest.raises(ComplianceGateError):
        evaluate_f1_f10_catalog(df, target_col="Energy_kWh", vsg=vsg, tcp=tcp)

    tcp.lock_preregistration()
    entries = evaluate_f1_f10_catalog(df, target_col="Energy_kWh", vsg=vsg, tcp=tcp, lite_profile=True, seed=42)
    fam_ids = {e.family_id for e in entries}
    assert fam_ids == {f"F{k}" for k in range(1, 11)}
    by_name = {e.model_name: e for e in entries}
    assert by_name["F6_LSTM"].status == "skipped"
    assert by_name["F9_Prophet_Optional"].status == "skipped"
    assert any(e.in_mcs for e in entries if e.status in ("shortlisted", "qualified"))
