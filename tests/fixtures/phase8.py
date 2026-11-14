"""Physical synthetic energy evidence built from fitted regressions, not renderer mocks."""

from __future__ import annotations
import numpy as np
from sklearn.linear_model import LinearRegression
from energybaseline.core.report_adapter import assemble_report
from energybaseline.report.provenance import provenance_manifest, derive_seed


def report_fixture():
    rng = np.random.default_rng(901)
    X = rng.uniform(0, 20, (160, 2))
    actual = 80 + 3 * X[:, 0] + 2 * X[:, 1] + rng.normal(0, 1.4, 160)
    estimator = LinearRegression().fit(X[:120], actual[:120])
    prediction = estimator.predict(X[120:])
    truth = actual[120:]
    res = truth - prediction
    n = len(truth)
    rmse = float(np.sqrt(np.mean(res**2)))
    nmbe = float(100 * res.mean() / truth.mean())
    cvrmse = 100 * rmse / float(truth.mean())
    r2 = float(1 - np.sum(res**2) / np.sum((truth - truth.mean()) ** 2))
    mae = float(np.mean(abs(res)))
    mape = float(100 * np.mean(abs(res / truth)))
    smape = float(200 * np.mean(abs(res) / (abs(truth) + abs(prediction))))
    aic = float(n * np.log(np.mean(res**2)) + 2 * 3)
    bic = float(n * np.log(np.mean(res**2)) + 3 * np.log(n))
    entry = {
        "name": "OLS",
        "rank": 1,
        "family": "F1",
        "score": 100 - cvrmse,
        "n_params": 3,
        "metrics": {
            "rmse": rmse,
            "cvrmse": cvrmse,
            "nmbe": nmbe,
            "r2": r2,
            "mae": mae,
            "mape": mape,
            "smape": smape,
            "aic": aic,
            "bic": bic,
        },
        "importance": {
            "pdp": {
                "Temperature (C)": {
                    "x": [0.0, 10.0, 20.0],
                    "y": estimator.predict(np.column_stack([[0.0, 10.0, 20.0], [10.0, 10.0, 10.0]])).tolist(),
                }
            }
        },
        "formula": {"intercept": float(estimator.intercept_), "coefficients": estimator.coef_.tolist()},
    }
    rows = [
        {"actual": float(a), "predicted": float(p), "lower": float(p - 3.0), "upper": float(p + 3.0)}
        for a, p in zip(truth, prediction)
    ]
    sha = "a" * 64
    target = {
        "name": "Electricity",
        "unit": "kWh",
        "n_obs": n,
        "shortlist": [entry],
        "quality": {"score": 100.0, "completeness": 1.0, "validity": 1.0, "uniqueness": float(len(set(truth)) / n)},
        "selected_features": ["Temperature", "Production"],
        "leaderboard": [{"model": "OLS", "qualified": True}],
        "series": rows,
        "test_catalog": [{"test_id": "T7", "decision": "not run (user)", "p_raw": None, "p_adj": None}],
        "register": [
            {"model": "example optional family", "status": "skipped", "reason": "optional backend unavailable"}
        ],
        "notes": ["WARNING: dependent residual coverage requires empirical review"],
        "selected_baseline": [{"model": "OLS", "version": 1}],
        "weight_sensitivity_20pct": {"stable": True},
    }
    provenance = provenance_manifest(
        sha, {"seed": 42}, commit_sha="b" * 40, target_seeds={"Electricity": derive_seed(sha, "Electricity", 42)}
    )
    return assemble_report(
        {
            "targets": [target],
            "auto": [{"setting": "seed", "value": 42}],
            "selections": [{"target": "Electricity", "note": "Engineer documented selection override"}],
        },
        provenance=provenance,
    )
