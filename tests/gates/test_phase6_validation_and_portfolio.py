"""Phase 6 Gate G6 Tests — Validation, Purge Gap, In-Fold Preprocessing, Canary, [M] Portfolio, NN Gate & Inner-OOF NNLS."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import StandardScaler

from energybaseline.mining.clustering import DescriptiveClusterLabels, RegimeLabelLeakageError
from energybaseline.models import (
    MANDATORY_PORTFOLIO_CLASSES,
    DegreeDayBaselineWrapper,
    ElasticNetBaselineWrapper,
    ExtraTreesWrapper,
    GradientBoostingWrapper,
    InnerFoldNNLSEnsemble,
    LassoBaselineWrapper,
    LightGBMWrapper,
    MLRBaselineWrapper,
    MLPWrapper,
    MeanBaselineWrapper,
    Piecewise1PTo5PWrapper,
    RandomForestWrapper,
    RidgeBaselineWrapper,
    SVRRBFWrapper,
    SampleSizeGateSkippedError,
    SequenceLSTMWrapper,
    TOWTBaselineWrapper,
    XGBoostWrapper,
    evaluate_inner_oof_nnls_ensemble,
    evaluate_nn_gate,
    get_portfolio_registry,
)
from energybaseline.pipeline import InFoldModelPipeline, run_leakage_safe_cv
from energybaseline.validation import (
    DataLeakageDetectedError,
    InFoldPreprocessor,
    TimeSeriesShuffleForbiddenError,
    assert_strictly_chronological_folds,
    chronological_train_val_holdout_split,
    compute_ashrae14_oos_metrics,
    compute_k_max,
    compute_purge_gap,
    compute_purge_gap_spec,
    folds_as_index_tuples,
    generate_walkforward_folds,
    run_future_perturbation_canary,
    run_nested_walkforward_hpo,
)


def _make_physical_energy_dgp(n: int = 160, seed: int = 42) -> tuple[pd.DataFrame, np.ndarray]:
    """Synthetic building thermal + schedule energy DGP with known planted physical effects."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    t_day = np.arange(n, dtype=float)
    temp_c = 16.0 + 13.0 * np.sin(2.0 * np.pi * t_day / 24.0) + rng.normal(0.0, 1.2, size=n)
    occ = np.where(idx.dayofweek < 5, 1.0, 0.25) + rng.normal(0.0, 0.03, size=n)
    prod = 50.0 + 15.0 * np.cos(2.0 * np.pi * t_day / 18.0) + rng.normal(0.0, 1.5, size=n)

    hdd = np.maximum(0.0, 14.5 - temp_c)
    cdd = np.maximum(0.0, temp_c - 19.5)
    y = 220.0 + 8.5 * hdd + 11.0 * cdd + 45.0 * occ + 1.8 * prod + rng.normal(0.0, 2.5, size=n)
    X = pd.DataFrame(
        {
            "outdoor_temp_c": temp_c,
            "occupancy_ratio": occ,
            "production_units": prod,
        },
        index=idx,
    )
    return X, y


def test_purge_gap_grows_strictly_with_larger_lag_and_rolling_window() -> None:
    """Gate G6: purge_gap = max(1 day, 1 step, max lag, max rolling window) must grow with lag/rolling window."""
    # Daily series (1 day = 1 step)
    g0 = compute_purge_gap_spec(steps_per_day=1.0, max_lag=0, max_rolling_window=0)
    g3 = compute_purge_gap_spec(steps_per_day=1.0, lags=[1, 3], max_rolling_window=0)
    g14 = compute_purge_gap_spec(steps_per_day=1.0, lags=[1, 3, 14], rolling_windows=[7])
    g30 = compute_purge_gap_spec(steps_per_day=1.0, lags=[7], rolling_windows=[30])

    assert g0.purge_gap == 1
    assert g3.purge_gap == 3 and g3.governing_term == "max_lag"
    assert g14.purge_gap == 14 and g14.purge_gap > g3.purge_gap
    assert g30.purge_gap == 30 and g30.governing_term == "max_rolling_window"

    # Hourly series (1 day = 24 steps): lag=72h (3 days) must exceed 24h!
    h_base = compute_purge_gap(steps_per_day=24.0, max_lag=6)
    h_lag72 = compute_purge_gap(steps_per_day=24.0, max_lag=72)
    assert h_base == 24
    assert h_lag72 == 72 and h_lag72 > h_base

    # Walk-forward folds must enforce the grown gap between train_end and val_start
    folds_l2, _ = generate_walkforward_folds(120, n_folds=3, mode="expanding", steps_per_day=1.0, max_lag=2)
    folds_l12, _ = generate_walkforward_folds(120, n_folds=3, mode="expanding", steps_per_day=1.0, max_lag=12)
    for f in folds_l2:
        assert int(f.val_idx[0] - f.train_idx[-1]) == 3  # 2 purged indices between tr[-1] and va[0]
    for f in folds_l12:
        assert f.purge_gap == 12
        assert int(f.val_idx[0] - f.train_idx[-1]) == 13


def test_val1_chronological_split_forbids_shuffling_and_non_monotonic_indices() -> None:
    """VAL-1: chronological split succeeds on ordered time series and raises on shuffle or unordered index."""
    X, _ = _make_physical_energy_dgp(100)
    sp = chronological_train_val_holdout_split(X, train_frac=0.70, val_frac=0.15, holdout_frac=0.15, max_lag=4)
    assert sp.purge_gap == 4
    assert sp.train_idx[-1] + 4 < sp.val_idx[0]
    assert sp.val_idx[-1] + 4 < sp.holdout_idx[0]

    with pytest.raises(TimeSeriesShuffleForbiddenError):
        chronological_train_val_holdout_split(X, shuffle=True)

    with pytest.raises(TimeSeriesShuffleForbiddenError):
        generate_walkforward_folds(100, shuffle=True)

    X_shuffled = X.iloc[::-1]
    with pytest.raises(TimeSeriesShuffleForbiddenError):
        chronological_train_val_holdout_split(X_shuffled)

    # Shuffled / non-contiguous validation indices must be rejected
    bad_folds = [(np.array([0, 1, 2, 3, 4, 5]), np.array([8, 10, 12]))]
    with pytest.raises(TimeSeriesShuffleForbiddenError):
        assert_strictly_chronological_folds(bad_folds, min_purge_gap=1)


def test_val4_in_fold_preprocessing_and_exact_k_max_rule() -> None:
    """VAL-4 & Gate G6: k_max = min(p, n // 20) and scaler/imputer/selector fit strictly inside folds."""
    assert compute_k_max(10, 40) == 2
    assert compute_k_max(10, 59) == 2
    assert compute_k_max(10, 100) == 5
    assert compute_k_max(3, 200) == 3
    assert compute_k_max(15, 75) == 3

    rng = np.random.default_rng(7)
    n = 120
    X = pd.DataFrame({f"feat_{j}": rng.normal(loc=j * 2.0, scale=1.0, size=n) for j in range(10)})
    # Inject regime shift in second half so full-dataset mean/median would differ markedly from early fold
    X.loc[70:, "feat_0"] += 500.0
    X.loc[5, "feat_1"] = np.nan
    y = 10.0 + 4.0 * X["feat_0"].fillna(0.0) - 2.5 * X["feat_2"] + rng.normal(0.0, 0.5, size=n)

    cv_res = run_leakage_safe_cv(
        lambda: InFoldModelPipeline(lambda: Ridge(alpha=0.1), scale=True, enforce_k_max=True, seed=42),
        X,
        y,
        n_folds=3,
        mode="expanding",
        steps_per_day=1.0,
    )
    # Fold 0 sees only early rows (< 70), so its fitted scaler mean on feat_0 MUST NOT be contaminated by +500 shift!
    f0 = cv_res.fold_summaries[0]
    assert f0.k_max == min(10, f0.n_train // 20)
    assert len(f0.selected_features) == f0.k_max
    if "feat_0" in f0.scaler_means:
        assert abs(f0.scaler_means["feat_0"]) < 5.0  # Full-dataset mean is > +200!


def test_val5_leakage_canary_passes_in_fold_pipeline_and_catches_leaky_pipeline() -> None:
    """VAL-5: future target/predictor spikes never alter historical predictions in InFoldModelPipeline, but catch leaky scaling."""
    X, y = _make_physical_energy_dgp(110, seed=19)
    wf_folds, _ = generate_walkforward_folds(len(y), n_folds=3, mode="expanding", steps_per_day=1.0, max_lag=2)
    idx_folds = folds_as_index_tuples(wf_folds)

    def _safe_predictor(X_full: pd.DataFrame, y_full: np.ndarray, tr: np.ndarray, va: np.ndarray) -> np.ndarray:
        pipe = InFoldModelPipeline(
            lambda: Ridge(alpha=1.0),
            scale=True,
            fit_degree_day_knots=True,
            lags=(1, 2),
            lag_cols=("outdoor_temp_c",),
            enforce_k_max=True,
            seed=42,
        )
        pipe.fit(X_full.iloc[tr], y_full[tr])
        va_end = int(va[-1]) + 1
        return pipe.predict(X_full.iloc[:va_end])[va]

    canary_ok = run_future_perturbation_canary(_safe_predictor, X, y, idx_folds, raise_on_leak=True)
    assert canary_ok.passed is True
    assert canary_ok.max_abs_historical_diff == 0.0

    # Negative control: intentionally leaky global StandardScaler + global target-correlated weight before fold slicing
    def _leaky_predictor(X_full: pd.DataFrame, y_full: np.ndarray, tr: np.ndarray, va: np.ndarray) -> np.ndarray:
        Z_global = StandardScaler().fit_transform(X_full.to_numpy(dtype=float))
        global_y_mean = float(np.mean(y_full))
        reg = LinearRegression().fit(Z_global[tr], y_full[tr] - global_y_mean)
        return reg.predict(Z_global[va]) + global_y_mean

    with pytest.raises(DataLeakageDetectedError):
        run_future_perturbation_canary(_leaky_predictor, X, y, idx_folds, raise_on_leak=True)


def test_val3_nested_hpo_and_val6_ashrae14_metrics() -> None:
    """VAL-3 bounded inner walk-forward Optuna HPO & VAL-6 OOS ASHRAE Guideline 14 metrics."""
    X, y = _make_physical_energy_dgp(90, seed=11)
    hpo_res = run_nested_walkforward_hpo(
        lambda params: Ridge(alpha=float(params["alpha"])),
        lambda trial: {"alpha": trial.suggest_float("alpha", 1e-3, 100.0, log=True)},
        X.iloc[:70],
        y[:70],
        max_trials=5,
        timeout_seconds=10.0,
        inner_folds=3,
        seed=42,
    )
    assert 1 <= hpo_res.n_trials_executed <= 5
    assert "alpha" in hpo_res.best_params
    assert np.isfinite(hpo_res.best_inner_rmse)

    m_monthly = compute_ashrae14_oos_metrics(y[70:], y[70:] * 1.02, n_params=4, granularity="monthly")
    assert m_monthly.cvrmse < 5.0
    assert abs(m_monthly.nmbe) < 5.0
    assert m_monthly.r2 > 0.90
    assert m_monthly.meets_ashrae14 is True
    assert m_monthly.max_cvrmse_limit == 15.0 and m_monthly.max_abs_nmbe_limit == 5.0


def test_planted_effect_recovery_across_all_16_mandatory_portfolio_wrappers() -> None:
    """Gate G6: every single [M] portfolio model family recovers planted physical signal; [M]/[S]/[O] registry complete."""
    reg = get_portfolio_registry()
    tiers = {p.key: p.requirement_tier for p in reg}
    m_keys = {k for k, t in tiers.items() if t == "M"}
    assert m_keys == set(MANDATORY_PORTFOLIO_CLASSES.keys())
    assert len(m_keys) == 16
    assert any(t == "S" for t in tiers.values())
    assert any(t == "O" for t in tiers.values())

    X, y = _make_physical_energy_dgp(150, seed=101)
    X_tr, y_tr = X.iloc[:120], y[:120]
    X_te, y_te = X.iloc[120:], y[120:]

    # 1. Mean (1P): exact mean recovery
    m_mean = MeanBaselineWrapper().fit(X_tr, y_tr)
    p_mean = m_mean.predict(X_te)
    assert np.allclose(p_mean, np.mean(y_tr), atol=1e-10)

    # 2. Degree-Day: recovers strong HDD/CDD signal on pure thermal DGP
    t_vals = X_tr["outdoor_temp_c"].to_numpy()
    y_dd_tr = 150.0 + 9.0 * np.maximum(0.0, 15.0 - t_vals) + 12.0 * np.maximum(0.0, t_vals - 19.0)
    t_te = X_te["outdoor_temp_c"].to_numpy()
    y_dd_te = 150.0 + 9.0 * np.maximum(0.0, 15.0 - t_te) + 12.0 * np.maximum(0.0, t_te - 19.0)
    m_dd = DegreeDayBaselineWrapper().fit(X_tr[["outdoor_temp_c"]], y_dd_tr)
    assert compute_ashrae14_oos_metrics(y_dd_te, m_dd.predict(X_te[["outdoor_temp_c"]])).r2 > 0.92

    # 3. Piecewise 1P..5P explicit variants + auto BIC
    for kind in ("1P", "2P", "3PC", "3PH", "4P", "5P", "auto"):
        pw = Piecewise1PTo5PWrapper(kind=kind, seed=42).fit(X_tr[["outdoor_temp_c"]], y_dd_tr)
        pred_pw = pw.predict(X_te[["outdoor_temp_c"]])
        assert pred_pw.shape == (len(X_te),) and np.all(np.isfinite(pred_pw))
        if kind in ("5P", "auto"):
            assert compute_ashrae14_oos_metrics(y_dd_te, pred_pw).r2 > 0.90

    # 4. Linear & Regularized + TOWT + Trees + GBDT + SVR + MLP + Sequence LSTM on multi-driver DGP
    # Augment with explicit HDD/CDD features so linear/regularized models also see exact thermal hinges
    X_aug = X.copy()
    X_aug["hdd_14_5"] = np.maximum(0.0, 14.5 - X["outdoor_temp_c"])
    X_aug["cdd_19_5"] = np.maximum(0.0, X["outdoor_temp_c"] - 19.5)
    X_atr, X_ate = X_aug.iloc[:120], X_aug.iloc[120:]

    wrappers = [
        MLRBaselineWrapper(seed=42),
        RidgeBaselineWrapper(seed=42),
        LassoBaselineWrapper(seed=42),
        ElasticNetBaselineWrapper(seed=42),
        TOWTBaselineWrapper(n_temp_bins=5, alpha=0.5),
        RandomForestWrapper(n_estimators=120, max_depth=7, seed=42),
        ExtraTreesWrapper(n_estimators=120, max_depth=8, seed=42),
        GradientBoostingWrapper(n_estimators=140, learning_rate=0.06, max_depth=3, seed=42),
        XGBoostWrapper(n_estimators=140, learning_rate=0.06, max_depth=4, seed=42),
        LightGBMWrapper(n_estimators=140, learning_rate=0.06, num_leaves=15, seed=42),
        SVRRBFWrapper(C=30.0, epsilon=0.02),
        MLPWrapper(hidden_layer_sizes=(48, 24), min_samples=50, seed=42),
        SequenceLSTMWrapper(seq_len=4, hidden_size=32, epochs=90, min_samples=60, seed=42),
    ]
    for w in wrappers:
        w.fit(X_atr, y_tr)
        pred = w.predict(X_ate)
        oos = compute_ashrae14_oos_metrics(y_te, pred, n_params=w.n_params, granularity="daily")
        min_r2 = 0.65 if w.model_key == "lstm" else 0.82
        assert oos.r2 >= min_r2, f"{w.model_key} failed planted-effect recovery: R2={oos.r2:.3f} < {min_r2}"


def test_nn_gate_both_directions_and_mt5_cluster_label_rejection() -> None:
    """Gate G6: NN gate tested both ways (skipped/rejected AND accepted) with logged reason + MT-5 guard."""
    # Direction 1a: Sample-size skip
    dec_skip = evaluate_nn_gate("MLP", n_obs=36, n_features=3, min_samples=50)
    assert dec_skip.accepted is False
    assert dec_skip.status == "skipped_sample_size_gate"
    assert "SKIPPED" in dec_skip.reason and "n=36" in dec_skip.reason

    X_small, y_small = _make_physical_energy_dgp(36)
    with pytest.raises(SampleSizeGateSkippedError, match="NN gate SKIPPED"):
        MLPWrapper(min_samples=50).fit(X_small, y_small)
    with pytest.raises(SampleSizeGateSkippedError, match="NN gate SKIPPED"):
        SequenceLSTMWrapper(min_samples=60).fit(X_small, y_small)

    # Direction 1b: Post-fit OOS gain rejection
    dec_rej = evaluate_nn_gate(
        "MLP",
        n_obs=200,
        n_features=4,
        min_samples=50,
        nn_cvrmse=9.8,
        reference_cvrmse=8.5,
        min_relative_gain_pct=1.0,
    )
    assert dec_rej.accepted is False
    assert dec_rej.status == "rejected_nn_gate"
    assert "REJECTED" in dec_rej.reason

    # Direction 2: Accepted when n >= min_samples and OOS CV(RMSE) improves over reference
    dec_acc = evaluate_nn_gate(
        "MLP",
        n_obs=200,
        n_features=4,
        min_samples=50,
        nn_cvrmse=6.2,
        reference_cvrmse=7.5,
        min_relative_gain_pct=2.0,
    )
    assert dec_acc.accepted is True
    assert dec_acc.status == "accepted_nn_gate"
    assert "ACCEPTED" in dec_acc.reason

    # MT-5: exploratory cluster labels MUST raise RegimeLabelLeakageError before reaching any preprocessor/wrapper
    labels = DescriptiveClusterLabels([0, 1] * 18)
    with pytest.raises(RegimeLabelLeakageError):
        InFoldPreprocessor().fit(labels, y_small)
    X_tainted = X_small.copy()
    X_tainted["cluster_regime"] = np.arange(len(X_small)) % 3
    with pytest.raises(RegimeLabelLeakageError):
        MLRBaselineWrapper().fit(X_tainted, y_small)


def test_nnls_ensemble_fitted_strictly_on_inner_oof_and_gated_at_3pct(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate G6: NNLS weights fitted ONLY on inner-fold OOF (never outer OOF) and accepted ONLY when gain > 3%."""
    import energybaseline.models.ensemble as ens_mod

    rng = np.random.default_rng(77)
    n = 140
    x1 = rng.uniform(-3.0, 3.0, size=n)
    x2 = rng.uniform(-3.0, 3.0, size=n)
    # Complementary additive structure: model_a sees ONLY x1, model_b sees ONLY x2 -> 50/50 NNLS slashes variance by >25%!
    y_comp = 100.0 + 12.0 * x1 + 12.0 * x2 + rng.normal(0.0, 1.0, size=n)
    X_comp = pd.DataFrame({"x1": x1, "x2": x2})

    class _ColSubsetOLS:
        def __init__(self, col_idx: int) -> None:
            self.col_idx = col_idx
            self.reg = LinearRegression()

        def fit(self, X_df: pd.DataFrame, y_arr: np.ndarray) -> "_ColSubsetOLS":
            arr = np.asarray(X_df, dtype=float)[:, [self.col_idx]]
            self.reg.fit(arr, y_arr)
            return self

        def predict(self, X_df: pd.DataFrame) -> np.ndarray:
            arr = np.asarray(X_df, dtype=float)[:, [self.col_idx]]
            return self.reg.predict(arr)

    seen_nnls_lengths: list[int] = []
    orig_fit_nnls = ens_mod.fit_nnls_weights_on_inner_oof

    def _spy_nnls(P_inner_oof: np.ndarray, y_inner_oof: np.ndarray, **kwargs: object) -> tuple[np.ndarray, int]:
        seen_nnls_lengths.append(int(len(y_inner_oof)))
        return orig_fit_nnls(P_inner_oof, y_inner_oof, **kwargs)

    monkeypatch.setattr(ens_mod, "fit_nnls_weights_on_inner_oof", _spy_nnls)

    res_acc = evaluate_inner_oof_nnls_ensemble(
        {"only_x1": lambda: _ColSubsetOLS(0), "only_x2": lambda: _ColSubsetOLS(1)},
        X_comp,
        y_comp,
        outer_folds=3,
        inner_folds=3,
        scale_features=False,
        enforce_k_max=False,
        min_gain_pct=3.0,
    )
    # Every outer-fold call to fit_nnls_weights_on_inner_oof had length strictly < n (outer train size ONLY, never outer val!)
    assert len(seen_nnls_lengths) == 4  # 3 outer folds + 1 final fit on full training set
    assert all(L < n for L in seen_nnls_lengths[:-1])
    assert res_acc.fitted_solely_on_inner_oof is True
    assert res_acc.accepted is True
    assert res_acc.relative_cvrmse_gain_pct > 3.0
    assert abs(res_acc.final_weights["only_x1"] - 0.5) < 0.15
    assert abs(res_acc.final_weights["only_x2"] - 0.5) < 0.15

    # Redundant collinear learners -> gain ~ 0% <= 3% -> MUST be rejected!
    y_red = 100.0 + 10.0 * x1 + rng.normal(0.0, 1.5, size=n)
    res_rej = evaluate_inner_oof_nnls_ensemble(
        {"ols": lambda: LinearRegression(), "ridge_tiny": lambda: Ridge(alpha=1e-4)},
        pd.DataFrame({"x1": x1}),
        y_red,
        outer_folds=3,
        inner_folds=3,
        min_gain_pct=3.0,
    )
    assert res_rej.accepted is False
    assert res_rej.status == "rejected_insufficient_ensemble_gain"
    assert res_rej.relative_cvrmse_gain_pct <= 3.0

    # Also verify direct estimator interface InnerFoldNNLSEnsemble
    est_ens = InnerFoldNNLSEnsemble(
        {"only_x1": lambda: _ColSubsetOLS(0), "only_x2": lambda: _ColSubsetOLS(1)},
        scale_features=False,
        enforce_k_max=False,
    ).fit(X_comp.iloc[:100], y_comp[:100])
    preds_hold = est_ens.predict(X_comp.iloc[100:])
    assert preds_hold.shape == (40,) and np.all(np.isfinite(preds_hold))
