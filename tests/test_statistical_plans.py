"""Real, gate-bound statistical procedures: numerical oracles, privacy and preregistration fidelity."""

from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
from scipy import stats
from statsmodels.stats.multitest import multipletests

from energybaseline.core.authorization import assert_bound_inference
from energybaseline.core.schema import ColumnRole, ColumnSpec, Inclusion, suggest_dataset_schema
from energybaseline.core.tcp import PreregistrationLockedError, TestConfigPanel as Panel
from energybaseline.core.vsg import ComplianceGateError, VariableSelectionGate
from energybaseline.statistics import METHODS, correct_family, run_statistical_plan, validate_plan
from energybaseline.workbench.common import digest


def procedure(method, names, **parameters):
    return {"id": "H1", "method": method, "columns": names, "parameters": parameters}


def plan_for(method, names, *, targets=None, metadata=None, sampling="independent", **parameters):
    return {
        "version": 1,
        "time": "time",
        "targets": targets or [names[0]],
        "metadata": metadata or [],
        "sampling": sampling,
        "assumption_justification": "Synthetic independent experimental units; model assumptions are explicit.",
        "correction": "tcp",
        "tests": [procedure(method, names, **parameters)],
    }


def authorize(frame, plan, *, lock=True, register=True):
    schema = suggest_dataset_schema(frame, time_col="time", targets=plan["targets"])
    for name in plan["metadata"]:
        schema = schema.with_column(ColumnSpec(name=name, role=ColumnRole.GROUP, inclusion=Inclusion.OBSERVED_ONLY))
    vsg = VariableSelectionGate(frame, targets=plan["targets"], time_col="time", schema=schema)
    vsg.accept()
    tcp = Panel(vsg, config={"tcp_alpha": 0.05, "tcp_correction": "holm", "tcp_confidence": "95"})
    if register:
        tcp.register_extension_plan(plan)
    if lock:
        tcp.lock_preregistration()
    return vsg, tcp


def execute(frame, plan):
    original, original_plan = frame.copy(deep=True), copy.deepcopy(plan)
    vsg, tcp = authorize(frame, plan)
    fingerprint = tcp.preregistration_sha256
    output = run_statistical_plan(plan, frame, vsg=vsg, tcp=tcp)
    assert output["spec_sha256"] == digest(plan)
    assert output["preregistration_sha256"] == fingerprint == tcp.preregistration_sha256
    assert not output["baseline_changed"]
    assert_frame_equal(frame, original)
    assert plan == original_plan
    encoded = json.dumps(output, allow_nan=False)
    assert "PRIVATE" not in encoded and "Synthetic independent" not in encoded
    body = {k: v for k, v in output.items() if k != "evidence_sha256"}
    assert digest(body) == output["evidence_sha256"]
    return output


@pytest.fixture
def frame():
    rng = np.random.default_rng(73)
    n = 120
    x = rng.normal(size=n)
    y = 1 + 2 * x + rng.normal(size=n)
    return pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=n, freq="h"),
            "x": x,
            "y": y,
            "z": y + rng.normal(0.2, 0.5, n),
            "group": ["PRIVATE-A"] * 60 + ["PRIVATE-B"] * 60,
            "a": rng.integers(0, 2, n),
            "b": rng.integers(0, 2, n),
            "c": rng.integers(0, 2, n),
        }
    )


def test_gates_plan_input_and_lock_must_all_match_before_compute(frame):
    plan = plan_for("one_sample_t", ["y"], null=0)
    with pytest.raises(ComplianceGateError):
        run_statistical_plan(plan, frame)
    vsg, tcp = authorize(frame, plan, lock=False)
    with pytest.raises(ComplianceGateError):
        run_statistical_plan(plan, frame, vsg=vsg, tcp=tcp)
    tcp.lock_preregistration()
    with pytest.raises(PreregistrationLockedError):
        tcp.register_extension_plan(plan)
    assert_bound_inference(frame, "time", ["y"], vsg, tcp)
    changed = copy.deepcopy(plan)
    changed["tests"][0]["parameters"]["null"] = 2
    with pytest.raises(ComplianceGateError):
        run_statistical_plan(changed, frame, vsg=vsg, tcp=tcp)
    data = frame.copy()
    data.loc[0, "y"] += 0.01
    with pytest.raises(ComplianceGateError):
        run_statistical_plan(plan, data, vsg=vsg, tcp=tcp)
    vsg2, tcp2 = authorize(frame, plan, register=False)
    with pytest.raises(ComplianceGateError):
        run_statistical_plan(plan, frame, vsg=vsg2, tcp=tcp2)
    tcp._extension_plan_hashes = ()
    with pytest.raises(ComplianceGateError):
        run_statistical_plan(plan, frame, vsg=vsg, tcp=tcp)


def test_empty_extension_does_not_change_legacy_canonical_hash(frame):
    plan = plan_for("one_sample_t", ["y"], null=0)
    vsg, tcp = authorize(frame, plan, lock=False, register=False)
    assert "extension_plan_hashes" not in tcp.canonical_spec_dict()
    before = tcp.tcp_sha256
    tcp.register_extension_plan(plan)
    assert before != tcp.tcp_sha256
    assert tcp.extension_plan_hashes == (digest(plan),)
    clone = tcp.clone_for_what_if()
    assert clone.extension_plan_hashes == () and not clone.is_locked
    with pytest.raises(ComplianceGateError):
        run_statistical_plan(plan, frame, vsg=vsg, tcp=clone)


@pytest.mark.parametrize("alternative", ["two-sided", "greater", "less"])
def test_one_sample_t_statistic_shifted_interval_and_direction_match_scipy(frame, alternative):
    plan = plan_for("one_sample_t", ["y"], null=0.7, alternative=alternative)
    row = execute(frame, plan)["frequentist"][0]
    oracle = stats.ttest_1samp(frame.y, 0.7, alternative=alternative)
    ci = oracle.confidence_interval(0.95)
    assert row["statistic"] == pytest.approx(oracle.statistic)
    assert row["p_input"] == pytest.approx(oracle.pvalue) == row["p_adjusted"]
    assert row["effect"] == pytest.approx(frame.y.mean() - 0.7)
    if np.isfinite(ci.low):
        assert row["interval"]["lower"] == pytest.approx(ci.low - 0.7)
    else:
        assert row["interval"]["lower_unbounded"]
    if np.isfinite(ci.high):
        assert row["interval"]["upper"] == pytest.approx(ci.high - 0.7)
    else:
        assert row["interval"]["upper_unbounded"]


@pytest.mark.parametrize("method", ["pooled_t", "welch_t"])
def test_independent_group_t_matches_scipy(frame, method):
    plan = plan_for(method, ["y", "group"], metadata=["group"], levels=["PRIVATE-A", "PRIVATE-B"])
    row = execute(frame, plan)["frequentist"][0]
    oracle = stats.ttest_ind(frame.y[:60], frame.y[60:], equal_var=method == "pooled_t")
    assert row["statistic"] == pytest.approx(oracle.statistic)
    assert row["p_input"] == pytest.approx(oracle.pvalue)
    assert row["n_by_group"] == [60, 60]


@pytest.mark.parametrize("method", ["paired_t", "wilcoxon", "sign_test"])
def test_paired_procedures_match_oracles_and_preserve_pair_alignment(frame, method):
    plan = plan_for(method, ["y", "z"], targets=["y", "z"], sampling="independent_subjects")
    row = execute(frame, plan)["frequentist"][0]
    difference = frame.y.to_numpy() - frame.z.to_numpy()
    if method == "paired_t":
        oracle = stats.ttest_rel(frame.y, frame.z)
    elif method == "wilcoxon":
        oracle = stats.wilcoxon(difference)
    else:
        oracle = stats.binomtest(int((difference > 0).sum()), int((difference != 0).sum()), 0.5)
    assert row["p_input"] == pytest.approx(oracle.pvalue)


def test_sign_test_zero_pairs_are_explicit_not_silently_lost(frame):
    frame.loc[:9, "z"] = frame.loc[:9, "y"]
    row = execute(frame, plan_for("sign_test", ["y", "z"], targets=["y", "z"], sampling="independent_subjects"))[
        "frequentist"
    ][0]
    assert row["zero_differences"] == 10 and row["n_nonzero"] == 110


@pytest.mark.parametrize("method", ["ks_fixed_normal", "lilliefors"])
def test_normality_distinguishes_fixed_null_from_fitted_null(frame, method):
    params = {"location": 0, "scale": 1} if method == "ks_fixed_normal" else {}
    row = execute(frame, plan_for(method, ["x"], **params))["frequentist"][0]
    if method == "ks_fixed_normal":
        oracle = stats.kstest(frame.x, stats.norm.cdf)
        assert row["statistic"] == pytest.approx(oracle.statistic)
        assert row["p_input"] == pytest.approx(oracle.pvalue)
    else:
        from statsmodels.stats.diagnostic import lilliefors

        statistic, probability = lilliefors(frame.x)
        assert row["statistic"] == pytest.approx(statistic)
        assert row["p_input"] == pytest.approx(probability)


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_uniform_associations_with_intervals_and_raw_adjusted_p(frame, method):
    plan = plan_for(
        method,
        ["x", "y"],
        targets=["x", "y"],
        **({"bootstrap_replicates": 199, "seed": 18} if method != "pearson" else {}),
    )
    row = execute(frame, plan)["frequentist"][0]
    oracle = {"pearson": stats.pearsonr, "spearman": stats.spearmanr, "kendall": stats.kendalltau}[method](
        frame.x, frame.y
    )
    assert row["effect"] == pytest.approx(oracle.statistic)
    assert row["p_input"] == pytest.approx(oracle.pvalue)
    assert row["interval"]["lower"] <= row["effect"] <= row["interval"]["upper"]


@pytest.mark.parametrize("method", ["fisher_exact", "mcnemar", "cochran_q", "chi_square"])
def test_categorical_tests_are_distinct_real_procedures(frame, method):
    names = ["a", "b", "c"] if method == "cochran_q" else ["a", "b"]
    plan = plan_for(
        method,
        names,
        targets=names,
        sampling="independent_subjects" if method in ("mcnemar", "cochran_q") else "independent",
    )
    row = execute(frame, plan)["frequentist"][0]
    table = pd.crosstab(frame.a, frame.b).to_numpy()
    if method == "fisher_exact":
        expected = stats.fisher_exact(table).pvalue
    elif method == "chi_square":
        expected = stats.chi2_contingency(table, correction=False).pvalue
    elif method == "mcnemar":
        from statsmodels.stats.contingency_tables import mcnemar

        expected = mcnemar(table, exact=True).pvalue
    else:
        from statsmodels.stats.contingency_tables import cochrans_q

        expected = cochrans_q(frame[names].to_numpy()).pvalue
    assert row["p_input"] == pytest.approx(expected)


def test_plan_multiplicity_is_one_complete_expanded_family_and_alpha_bound(frame):
    plan = plan_for("one_sample_t", ["y"], null=0)
    plan["tests"] = [
        procedure("one_sample_t", ["y"], null=value) | {"id": f"H{i}"} for i, value in enumerate([0, 0.5, 1, 1.5])
    ]
    for correction in ("tcp", "fdr_tsbh", "fdr_tsbky"):
        plan["correction"] = correction
        output = execute(frame, plan)
        rows = output["frequentist"]
        raw = [r["p_input"] for r in rows]
        oracle = multipletests(raw, alpha=0.05, method="holm" if correction == "tcp" else correction, maxiter=1)[1]
        assert [r["p_adjusted"] for r in rows] == pytest.approx(oracle)
        assert all(r["family_size"] == 4 and r["correction_alpha"] == 0.05 for r in rows)
    raw = [0.001, 0.02, 0.04, 0.06, 0.15, 0.5]
    for correction in ("fdr_tsbh", "fdr_tsbky"):
        assert correct_family(raw, correction, 0.01) == pytest.approx(
            multipletests(raw, alpha=0.01, method=correction)[1]
        )
        assert correct_family(raw, correction, 0.2) == pytest.approx(
            multipletests(raw, alpha=0.2, method=correction)[1]
        )


@pytest.mark.parametrize("method", ["factorial_anova", "ancova", "manova", "tukey_hsd", "dunn"])
def test_factorial_covariate_multivariate_and_posthoc_designs(method):
    rng = np.random.default_rng(34)
    n = 160
    group = np.repeat(["PRIVATE-A", "PRIVATE-B"], n // 2)
    factor = np.tile(np.repeat(["C", "D"], 40), 2)
    x = rng.normal(size=n)
    y = (group == "PRIVATE-B") * 0.7 + (factor == "D") * 0.5 + 0.4 * x + rng.normal(size=n)
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=n, freq="h"),
            "y": y,
            "y2": 0.5 * y + rng.normal(size=n),
            "group": group,
            "factor": factor,
            "x": x,
        }
    )
    if method == "factorial_anova":
        names = ["y", "group", "factor"]
        meta = ["group", "factor"]
        targets = ["y"]
        params = {}
    elif method == "ancova":
        names = ["y", "group", "x"]
        meta = ["group"]
        targets = ["y", "x"]
        params = {}
    elif method == "manova":
        names = ["y", "y2", "group"]
        meta = ["group"]
        targets = ["y", "y2"]
        params = {"dependent_count": 2}
    else:
        names = ["y", "group"]
        meta = ["group"]
        targets = ["y"]
        params = {}
    rows = execute(frame, plan_for(method, names, targets=targets, metadata=meta, **params))["frequentist"]
    assert rows and all(0 <= r["p_input"] <= 1 for r in rows)
    assert all(r["family_size"] == len(rows) for r in rows)
    if method == "dunn":
        ranks = stats.rankdata(y)
        diff = ranks[:80].mean() - ranks[80:].mean()
        z = diff / np.sqrt(n * (n + 1) / 12 * (1 / 80 + 1 / 80))
        assert rows[0]["statistic"] == pytest.approx(z)
    if method == "tukey_hsd":
        from statsmodels.stats.multicomp import pairwise_tukeyhsd

        oracle = pairwise_tukeyhsd(y, group)
        assert rows[0]["p_input"] == pytest.approx(oracle.pvalues[0])
        assert "within-family" in rows[0]["p_kind"]
    if method == "factorial_anova":
        assert len(rows) == 3


def test_repeated_measures_anova_balanced_and_incomplete_subject_guard():
    rng = np.random.default_rng(8)
    subjects = np.repeat(np.arange(30), 3)
    condition = np.tile(["a", "b", "c"], 30)
    y = np.repeat(rng.normal(size=30), 3) + np.tile([0, 0.4, 0.8], 30) + rng.normal(0, 0.3, 90)
    frame = pd.DataFrame(
        {"time": pd.date_range("2025", periods=90, freq="h"), "y": y, "subject": subjects, "condition": condition}
    )
    plan = plan_for(
        "repeated_anova",
        ["y", "subject", "condition"],
        metadata=["subject", "condition"],
        sampling="independent_subjects",
    )
    row = execute(frame, plan)["frequentist"][0]
    assert row["subjects"] == 30 and row["conditions"] == 3 and row["p_input"] < 0.001
    with pytest.raises(ValueError):
        execute(frame.iloc[1:].copy(), plan)


@pytest.mark.parametrize("design", ["one_sample_t", "independent_t", "independent_z", "balanced_anova"])
def test_prospective_power_solves_integer_sample_sizes_not_observed_power(frame, design):
    plan = plan_for("prospective_power", [], targets=["y"], design=design, effect_size=0.5, mode="solve_n", power=0.8)
    output = execute(frame, plan)["prospective"][0]
    assert output["attained_power"] >= 0.8 and not output["observed_power"]
    assert all(type(n) is int and n >= 2 for n in output["group_sample_sizes"])
    parameters = plan["tests"][0]["parameters"]
    parameters.pop("power")
    parameters.update(mode="evaluate", n_per_group=output["group_sample_sizes"][0])
    assert execute(frame, plan)["prospective"][0]["attained_power"] == pytest.approx(output["attained_power"])


@pytest.mark.parametrize("method", ["beta_binomial", "normal_mean"])
def test_conjugate_posteriors_are_not_frequentist_p_values(frame, method):
    if method == "beta_binomial":
        params = dict(prior_alpha=2, prior_beta=3, threshold=0.5)
        name = "a"
    else:
        params = dict(sigma=2, prior_mean=0, prior_sd=3, threshold=0)
        name = "y"
    output = execute(frame, plan_for(method, [name], **params))
    assert not output["frequentist"] and len(output["bayesian"]) == 1
    row = output["bayesian"][0]
    if method == "beta_binomial":
        a, b = 2 + frame.a.sum(), 3 + len(frame) - frame.a.sum()
        expected = a / (a + b)
        variance = a * b / ((a + b) ** 2 * (a + b + 1))
    else:
        variance = 1 / (len(frame) / 4 + 1 / 9)
        expected = variance * frame.y.sum() / 4
    assert row["posterior_mean"] == pytest.approx(expected)
    assert row["posterior_variance"] == pytest.approx(variance)
    assert 0 <= row["posterior_probability_above_threshold"] <= 1
    assert row["credible_interval"]["interpretation"].startswith("Bayesian")


def test_bayesian_model_average_evidence_matches_multivariate_normal(frame):
    models = [dict(prior_mean=0, prior_sd=1, weight=0.3), dict(prior_mean=2, prior_sd=2, weight=0.7)]
    small = frame.iloc[:30].copy()
    row = execute(small, plan_for("normal_model_average", ["y"], sigma=2, models=models))["bayesian"][0]
    logs = [
        stats.multivariate_normal.logpdf(
            small.y,
            mean=np.repeat(m["prior_mean"], len(small)),
            cov=4 * np.eye(len(small)) + m["prior_sd"] ** 2 * np.ones((len(small), len(small))),
        )
        for m in models
    ]
    assert row["log_evidence"] == pytest.approx(logs)
    from scipy.special import softmax

    assert row["posterior_model_probabilities"] == pytest.approx(softmax(np.log([0.3, 0.7]) + logs))
    assert sum(row["posterior_model_probabilities"]) == pytest.approx(1)


@pytest.mark.parametrize("method", ["linear_metropolis", "linear_variational"])
def test_genuine_mcmc_or_variational_linear_inference_matches_known_gaussian_solution(frame, method):
    params = dict(sigma=1, prior_mean=[0, 0], prior_sd=[3, 3])
    if method == "linear_metropolis":
        params.update(proposal_sd=[0.12, 0.12], draws=2000, warmup=1000, chains=4, seed=10)
    row = execute(frame, plan_for(method, ["y", "x"], targets=["y", "x"], **params))["bayesian"][0]
    X = np.column_stack([np.ones(len(frame)), frame.x])
    P = X.T @ X + np.eye(2) / 9
    expected = np.linalg.solve(P, X.T @ frame.y)
    if method == "linear_metropolis":
        assert row["posterior_means"] == pytest.approx(expected, abs=0.04)
        assert row["status"] == "diagnostics_passed"
        assert max(row["split_rhat"]) < 1.05 and min(row["effective_sample_size"]) > 100
        assert 0 < min(row["acceptance_by_chain"]) < max(row["acceptance_by_chain"]) < 1
    else:
        assert row["optimizer_success"] and row["status"] == "optimized"
        assert row["variational_means"] == pytest.approx(expected, abs=1e-5)
        assert row["variational_sd"] == pytest.approx(1 / np.sqrt(np.diag(P)), rel=1e-5)
        assert row["kl_to_exact_gaussian_posterior"] >= 0


def test_mcmc_bad_mixing_is_reported_not_silently_certified(frame):
    params = dict(
        sigma=1,
        prior_mean=[0, 0],
        prior_sd=[3, 3],
        proposal_sd=[1e-10, 1e-10],
        draws=500,
        warmup=200,
        chains=4,
        seed=10,
    )
    row = execute(frame, plan_for("linear_metropolis", ["y", "x"], targets=["y", "x"], **params))["bayesian"][0]
    assert row["status"] == "diagnostic_warning"


@pytest.mark.parametrize(
    "method,params",
    [
        ("one_sample_t", {"null": 0, "unknown": "PRIVATE-SECRET"}),
        ("normal_mean", {"sigma": 0, "prior_mean": 0, "prior_sd": 1, "threshold": 0}),
        (
            "prospective_power",
            {"design": "one_sample_t", "effect_size": 0.5, "mode": "observed_power", "n_per_group": 20},
        ),
    ],
)
def test_invalid_parameters_fail_without_partial_or_private_error_values(frame, method, params):
    plan = plan_for(method, [] if method == "prospective_power" else ["y"], targets=["y"], **params)
    with pytest.raises(ValueError) as exc:
        execute(frame, plan)
    assert "PRIVATE-SECRET" not in str(exc.value)


@pytest.mark.parametrize("change", ["version", "unknown", "id", "method", "metadata", "sampling", "missing"])
def test_invalid_structural_plans_fail_before_authorization(change):
    plan = plan_for("one_sample_t", ["y"], null=0)
    if change == "version":
        plan["version"] = True
    elif change == "unknown":
        plan["execute"] = "os.system"
    elif change == "id":
        plan["tests"][0]["id"] = "bad id"
    elif change == "method":
        plan["tests"][0]["method"] = "__import__"
    elif change == "metadata":
        plan["metadata"] = ["y"]
    elif change == "sampling":
        plan["sampling"] = "autocorrelated_distribution_free"
    else:
        plan.pop("correction")
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_cli_demands_explicit_acceptance_and_executes_real_registered_plan(frame, tmp_path, capsys):
    from energybaseline.cli import main

    plan = plan_for("pooled_t", ["y", "group"], metadata=["group"], levels=["PRIVATE-A", "PRIVATE-B"])
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    data = tmp_path / "data.csv"
    frame.to_csv(data, index=False)
    assert main(["statistical-plan", str(path), str(data)]) == 2
    assert "PRIVATE" not in capsys.readouterr().err
    out = tmp_path / "result.json"
    assert main(["statistical-plan", str(path), str(data), "--accept-and-lock", "--out", str(out)]) == 0
    result = json.loads(out.read_text())
    assert result["frequentist"][0]["method"] == "pooled_t"
    assert json.loads(capsys.readouterr().out) == result


def test_method_registry_is_explicit_and_complete_for_documented_stage():
    assert len(METHODS) == 27 and len(set(METHODS)) == 27


def test_persisted_tcp_retains_only_plan_digest_and_does_not_restore_execution_authority(frame):
    from energybaseline.core import store
    from energybaseline.db import session as ses

    ses.init_database()
    plan = plan_for("one_sample_t", ["y"], null=0)
    plan["assumption_justification"] = "PRIVATE-ASSUMPTION; do not persist plan literals."
    vsg, tcp = authorize(frame, plan)
    vid = store.save_vsg_state(vsg.to_dict())
    tid = store.save_tcp_config(tcp.to_dict(), vsg_id=vid)
    loaded = store.load_tcp_config(tid)
    assert loaded["extension_plan_hashes"] == [digest(plan)]
    assert loaded["preregistration_sha256"] == tcp.preregistration_sha256
    assert "PRIVATE-ASSUMPTION" not in json.dumps(loaded)
    # Serialized accepted/locked flags are records, never live authorization credentials.
    with pytest.raises((ComplianceGateError, AttributeError)):
        run_statistical_plan(plan, frame, vsg=vsg, tcp=loaded)
    replacement_vsg, replacement_tcp = authorize(frame, plan)
    assert replacement_tcp.preregistration_sha256 == tcp.preregistration_sha256
    assert run_statistical_plan(plan, frame, vsg=replacement_vsg, tcp=replacement_tcp)["frequentist"]


def test_all_statistical_example_plans_execute_through_cli(tmp_path, capsys):
    from pathlib import Path
    from energybaseline.cli import main

    root = Path(__file__).resolve().parents[1] / "examples" / "statistics"
    observed = set()
    for path in sorted(root.glob("*.json")):
        observed.update(t["method"] for t in json.loads(path.read_text())["tests"])
        output = tmp_path / path.name
        assert (
            main(["statistical-plan", str(path), str(root / "input.csv"), "--accept-and-lock", "--out", str(output)])
            == 0
        )
        assert json.loads(capsys.readouterr().out) == json.loads(output.read_text())
    assert observed == set(METHODS)


def test_dense_design_resource_limits_fail_before_large_library_allocation(frame):
    frame["a"] = np.arange(len(frame))
    frame["b"] = np.arange(len(frame))
    with pytest.raises(ValueError):
        execute(frame, plan_for("chi_square", ["a", "b"], targets=["a", "b"]))
    n = 303
    data = pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=n, freq="h"),
            "y": np.random.default_rng(1).normal(size=n),
            "subject": np.repeat(np.arange(101), 3),
            "condition": np.tile(["a", "b", "c"], 101),
        }
    )
    with pytest.raises(ValueError):
        execute(
            data,
            plan_for(
                "repeated_anova",
                ["y", "subject", "condition"],
                metadata=["subject", "condition"],
                sampling="independent_subjects",
            ),
        )


def test_fisher_infinite_odds_are_explicit_null_not_zero(frame):
    frame["a"] = [0] * 60 + [1] * 60
    frame["b"] = frame.a
    row = execute(frame, plan_for("fisher_exact", ["a", "b"], targets=["a", "b"]))["frequentist"][0]
    assert row["odds_ratio_infinite"] and not row["statistic_defined"]
    assert row["statistic"] is None and row["effect"] is None
    assert row["p_input"] == pytest.approx(stats.fisher_exact([[60, 0], [0, 60]]).pvalue)


def test_direct_primitive_registry_cannot_bypass_exact_plan_and_live_gates(frame):
    from energybaseline.statistics import FREQUENTIST, BAYESIAN

    plan = plan_for("one_sample_t", ["y"], null=0)
    for function in set(FREQUENTIST.values()) | set(BAYESIAN.values()):
        with pytest.raises(ComplianceGateError):
            function(frame, ["y"], {"null": 0, "_method": "one_sample_t"}, 0.95)
    vsg, tcp = authorize(frame, plan)
    function = FREQUENTIST["one_sample_t"]
    assert function(
        frame, ["y"], {"null": 0, "_method": "one_sample_t"}, tcp.confidence_level, plan=plan, vsg=vsg, tcp=tcp
    )
    for parameters in [{"null": 1, "_method": "one_sample_t"}, {"_method": "lilliefors"}]:
        with pytest.raises(ComplianceGateError):
            function(frame, ["y"], parameters, tcp.confidence_level, plan=plan, vsg=vsg, tcp=tcp)
    with pytest.raises(ComplianceGateError):
        function(frame, ["y"], {"null": 0, "_method": "one_sample_t"}, 0.8, plan=plan, vsg=vsg, tcp=tcp)
    with pytest.raises(ComplianceGateError):
        function(
            frame, ["x"], {"null": 0, "_method": "one_sample_t"}, tcp.confidence_level, plan=plan, vsg=vsg, tcp=tcp
        )
