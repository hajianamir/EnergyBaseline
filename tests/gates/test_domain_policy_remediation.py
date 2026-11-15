"""Domain policy regression proofs, including a non-energy process target."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energybaseline.core.auto import AutoLog
from energybaseline.core.domain import resolve_domain
from energybaseline.core.features import build_spec
from energybaseline.core.schema import default_config
from energybaseline.core.scoring import thresholds, analyze_ranking_sensitivity
from energybaseline.core.temporal import Granularity


def granularity(tier="monthly"):
    return Granularity(tier, "MS" if tier == "monthly" else "D", 86400, 1, 12, tier)


def test_product_defaults_resolve_to_neutral_not_column_inference():
    assert resolve_domain(default_config()).key == "neutral"
    assert resolve_domain({"domain_pack": "neutral"}).energy is False
    with pytest.raises(ValueError):
        resolve_domain({"domain_pack": "unknown"})


@pytest.mark.parametrize("tier", ["monthly", "daily", "weekly", "subdaily"])
def test_neutral_has_no_universal_engineering_acceptance_limits(tier):
    limits = thresholds(granularity(tier), AutoLog({"domain_pack": "neutral"}))
    assert limits == {"max_cvrmse": None, "max_nmbe": None, "min_r2": None}


def test_user_limits_and_explicit_energy_selection():
    assert thresholds(granularity(), AutoLog({"domain_pack": "energy"}))["max_cvrmse"] == 15
    assert thresholds(granularity("daily"), AutoLog({"domain_pack": "energy"}))["max_cvrmse"] is None
    assert thresholds(granularity(), AutoLog({"domain_pack": "neutral", "max_cvrmse": 8}))["max_cvrmse"] == 8
    with pytest.raises(ValueError):
        thresholds(granularity(), AutoLog({"domain_pack": "neutral", "max_cvrmse": float("nan")}))


def test_degree_day_learning_requires_energy_pack():
    idx = pd.date_range("2020-01-01", periods=48, freq="MS")
    t = np.linspace(-10, 35, len(idx))
    df = pd.DataFrame({"temperature_C": t, "yield": 5 + np.maximum(0, 15 - t)}, index=idx)
    spec, _, _ = build_spec(df, "yield", ["temperature_C"], [], granularity(), AutoLog({"domain_pack": "neutral"}))
    assert spec.temp_col is None and spec.balance_heat is None
    spec, _, _ = build_spec(df, "yield", ["temperature_C"], [], granularity(), AutoLog({"domain_pack": "energy"}))
    assert spec.temp_col == "temperature_C" and spec.balance_heat is not None


def test_sensitivity_uses_actual_weights_and_disables_inapplicable_criteria():
    results = [
        {"name": "A", "status": "ok", "qualified": True, "criteria": {"mae": 1.0, "physics": 0.0}},
        {"name": "B", "status": "ok", "qualified": True, "criteria": {"mae": 0.5, "physics": 1.0}},
    ]
    report = analyze_ranking_sensitivity(results, {"mae": 1.0, "physics": 0.0})
    assert report["baseline_top1"] == "A" and report["top1_flip_rate"] == 0
    assert report["base_weights"] == {"mae": 1.0, "physics": 0.0}
    assert analyze_ranking_sensitivity(results, {"mae": 0.0, "physics": 1.0})["baseline_top1"] == "B"


def test_real_non_energy_sdk_analysis_has_no_energy_acceptance_or_models(monkeypatch):
    import scipy.stats
    import energybaseline.tests_catalog as catalog

    def forbidden(*args, **kwargs):
        pytest.fail("No inferential engine may run before accepted VSG and locked TCP")

    for name in ("shapiro", "kendalltau", "jarque_bera"):
        monkeypatch.setattr(scipy.stats, name, forbidden)
    monkeypatch.setattr(catalog, "run_full_test_catalog", forbidden)
    from energybaseline.sdk import analyze

    rng = np.random.default_rng(822)
    n = 48
    x = rng.normal(size=n)
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2020-01-01", periods=n, freq="MS"),
            "process_setting": x,
            "quality_offset": -8 + 2 * x + rng.normal(0, 0.2, n),
        }
    )
    run, bundles = analyze(
        df,
        "timestamp",
        ["quality_offset"],
        {
            "model_families": ["linear"],
            "enable_nn": False,
            "enable_mining": False,
            "hpo_trials": 0,
            "lags": [1, 7],
            "rolling_windows": [3],
            "include_importance": False,
            "include_eda_attachment": False,
        },
    )
    target = run.targets["quality_offset"]
    assert target.status == "ok", target.error
    assert target.shortlist and bundles["quality_offset"]
    assert target.purge_gap_spec["purge_gap"] >= 7
    assert target.test_catalog and all(item["p_raw"] is None for item in target.test_catalog)
    assert target.eda["distribution"]["shapiro_p"] is None
    assert target.eda["trend"]["kendall_p"] is None
    assert "degree_days" not in target.eda
    assert run.config["domain_policy"]["key"] == "neutral"
    assert all(value is None for value in target.thresholds.values())
    assert target.scoring["weights"]["ashrae"] == target.scoring["weights"]["plausibility"] == 0
    assert target.scoring["acceptance_evaluated"] is False
    assert not {"Degree-day regression", "TOWT (time-of-week & temperature)"} & {
        row["model"] for row in target.register
    }
    assert target.weight_sensitivity_20pct["baseline_top1"] == target.shortlist[0]["name"]
