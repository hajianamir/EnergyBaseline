"""Second-pass regressions: reject invalid evidence and share ranking semantics."""

from __future__ import annotations

from copy import deepcopy
import numpy as np
import pytest

from energybaseline.core.auto import AutoLog
from energybaseline.core.scoring import analyze_ranking_sensitivity, build_shortlist, score_models
from energybaseline.core.sqlsource import ConnectionProfile, QuerySpec


def candidate(name="A", qualified=True, score=1.0):
    return dict(
        name=name,
        status="ok",
        qualified=qualified,
        score=score,
        rank=1,
        metrics=dict(cvrmse=10.0, nmbe=1.0, r2=0.8, mae=1.0, smape=2.0),
        criteria={"mae": score / 100},
        criteria_weights={"mae": 1.0},
        threshold_failures=[] if qualified else ["configured limit"],
        n_params=2,
        bic=10.0,
        diagnostics={"tests_passed": 0, "inference_status": "not run"},
        plausibility=1.0,
        plaus_notes=[],
        mean_y=10.0,
        resid_q=(-1.0, 1.0),
        robustness=0.1,
        tier=1,
        kind="linear",
    )


@pytest.mark.parametrize("qualifications", [(True, False, False), (False, False, False), (True, True, True)])
def test_sensitivity_baseline_is_exactly_the_production_shortlist(qualifications):
    rows = [candidate(name, qualified, 50.0) for name, qualified in zip(("Z", "B", "A"), qualifications)]
    expected = [row["name"] for row in build_shortlist(deepcopy(rows))["top"]]
    sensitivity = analyze_ranking_sensitivity(rows, {"mae": 1.0}, draws=0)
    assert sensitivity["baseline_top5"] == expected
    assert sensitivity["baseline_top1"] == expected[0]


@pytest.mark.parametrize("key,value", [("cvrmse", np.nan), ("nmbe", np.inf), ("r2", np.nan)])
def test_nonfinite_acceptance_cannot_silently_pass(key, value):
    row = candidate()
    row["metrics"][key] = value
    score_models([row], 50, {"max_cvrmse": 15.0, "max_nmbe": 5.0, "min_r2": 0.75}, AutoLog({"domain_pack": "energy"}))
    assert not row["qualified"] and row["threshold_failures"]
    assert np.isfinite(row["score"])


def test_credential_carriers_do_not_disclose_secrets_in_repr():
    secret = "RECHECK_ONLY_PASSWORD"
    assert secret not in repr(ConnectionProfile("test", password=secret, odbc_string=secret, queries={"q": secret}))
    assert secret not in repr(QuerySpec("SELECT :p", (("p", secret),)))
