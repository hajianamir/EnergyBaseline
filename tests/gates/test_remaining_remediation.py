"""Behavioral proofs for remaining evaluator, contract and origin-trust work."""

from __future__ import annotations

import hashlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge

from energybaseline.contracts import AnalysisRequest
from energybaseline.core.cache import CachedComplianceRunner
from energybaseline.core.evaluation import RawEvaluationPlan
from energybaseline.core.integrity import run_integrity_checks
from energybaseline.core.models import Candidate, ColsDesign, Ctx, build_portfolio, make_folds
from energybaseline.core.schema import QuantityType, suggest_dataset_schema
from energybaseline.core.tcp import TestConfigPanel as Panel
from energybaseline.core.temporal import granularity
from energybaseline.core.vsg import ComplianceGateError, VariableSelectionGate


def frame(n=120):
    rng = np.random.default_rng(7721)
    x = rng.normal(size=n)
    return pd.DataFrame(
        {"setting": x, "target": 20 + x + rng.normal(size=n)}, index=pd.date_range("2010-01-01", periods=n, freq="MS")
    )


def evaluator(**config):
    return RawEvaluationPlan(
        frame(),
        "target",
        ["target"],
        granularity("monthly"),
        {"domain_pack": "neutral", "lags": [], "rolling_windows": [], **config},
        42,
    )


def gates():
    data = frame().reset_index(names="time")
    vsg = VariableSelectionGate(data, targets=["target"], time_col="time", config={"domain_pack": "neutral"})
    vsg.accept()
    panel = Panel(vsg)
    panel.accept()
    return data, vsg, panel


@pytest.mark.parametrize("method", ["execute_tests", "run_compliance_stage_dag", "execute_catalog_suite"])
def test_every_direct_cached_executor_rejects_reused_input_authorization(method):
    data, vsg, panel = gates()
    panel.lock_preregistration()
    data.loc[0, "setting"] += 50
    runner = CachedComplianceRunner()
    with pytest.raises(ComplianceGateError, match="input"):
        getattr(runner, method)(data, vsg, panel)
    assert runner.engine_call_count == 0
    assert not any(runner.stage_call_counts.values())


def test_legacy_direct_execution_freezes_exact_accepted_panel_before_compute():
    data, vsg, panel = gates()
    runner = CachedComplianceRunner()
    assert not panel.is_locked
    with pytest.warns(DeprecationWarning, match="freeze"):
        results = runner.execute_tests(data, vsg, panel)
    assert panel.is_locked
    assert results and runner.engine_call_count > 0


def test_direct_mutation_after_acceptance_is_not_silently_preregistered():
    data, vsg, panel = gates()
    panel.alpha = 0.007
    runner = CachedComplianceRunner()
    with pytest.raises(ComplianceGateError, match="changed since acceptance"):
        runner.execute_tests(data, vsg, panel)
    assert runner.engine_call_count == 0


def test_portfolio_regularizers_have_no_hidden_estimator_cv():
    context = Ctx("target", enable_nn=False, energy_policy=False)
    candidates, _ = build_portfolio(context, 120)
    regularizers = [
        c
        for c in candidates
        if c.name
        in {
            "Ridge regression",
            "Lasso regression",
            "Elastic Net",
            "Polynomial regression (deg 2)",
            "GAM (B-spline additive)",
        }
    ]
    assert len(regularizers) == 5
    for candidate in regularizers:
        estimator = candidate.est({}, context)
        assert not hasattr(estimator, "cv")
        assert not type(estimator).__name__.endswith("CV")
        assert candidate.space is not None
        assert estimator.alpha > 0


def test_compatibility_hpo_refits_design_inside_exact_unstrided_folds():
    data = frame(2601)
    fits = []

    class RecordingDesign(ColsDesign):
        def fit(self, X, y):
            fits.append(X.index.copy())
            return super().fit(X, y)

    context = Ctx("target", hpo_trials=1, gap=37, cv_scheme="forward_chaining")
    candidate = Candidate(
        "test",
        "linear",
        1,
        "linear",
        lambda c: RecordingDesign(),
        lambda p, c: Ridge(**p),
        space=lambda t: {"alpha": t.suggest_float("alpha", 0.1, 1.0)},
    )
    model = candidate.fit(data[["setting"]], data.target.to_numpy(), context)
    expected = make_folds(len(data), 3, context.cv_scheme, context.gap)
    assert len(fits) == len(expected) + 1
    for observed, (train, valid) in zip(fits, expected):
        assert observed.equals(data.index[train])
        assert train[-1] < valid[0] - context.gap
    assert fits[-1].equals(data.index)
    assert "fold-local design/scaling" in model.hpo_note


def test_fold_cache_has_bounded_retained_matrices_and_exact_recomputation():
    disabled = evaluator(evaluation_cache_mb=0)
    first = disabled.prepare(np.arange(70), np.arange(80, 90))
    assert not disabled.cache and disabled.cache_bytes == 0
    required = disabled._fold_bytes(first)
    bounded = evaluator(evaluation_cache_mb=(required + 50) / 1024**2)
    a = bounded.prepare(np.arange(70), np.arange(80, 90))
    bounded.prepare(np.arange(10, 80), np.arange(90, 100))
    assert bounded.cache_bytes <= bounded.cache_budget_bytes
    assert bounded.cache_evictions >= 1
    pd.testing.assert_frame_equal(a.valid_X, bounded.prepare(np.arange(70), np.arange(80, 90)).valid_X)


def test_cancelled_cached_preparation_stops_before_reusing_cache():
    plan = evaluator()
    plan.prepare(np.arange(70), np.arange(80, 90))
    plan.cancel = lambda: True
    with pytest.raises(InterruptedError, match="cancelled"):
        plan.prepare(np.arange(70), np.arange(80, 90))


@pytest.mark.parametrize("budget", [-1, float("nan"), float("inf")])
def test_invalid_fold_cache_budgets_are_rejected(budget):
    with pytest.raises(ValueError, match="evaluation_cache_mb"):
        evaluator(evaluation_cache_mb=budget)


def test_neutral_schema_has_no_energy_name_unit_or_quantity_inference():
    data = frame().reset_index(names="time").rename(columns={"target": "Energy_kWh", "setting": "production_units"})
    neutral = suggest_dataset_schema(data, time_col="time", targets=["Energy_kWh"], domain_pack="neutral")
    assert all(c.quantity_type == QuantityType.QUALITY and not c.unit for c in neutral.columns if c.name != "time")
    energy = suggest_dataset_schema(data, time_col="time", targets=["Energy_kWh"], domain_pack="energy")
    assert next(c for c in energy.columns if c.name == "Energy_kWh").quantity_type == QuantityType.FLOW


def test_meter_name_is_not_universal_leakage_but_affine_identity_stays_blocked():
    rng = np.random.default_rng(81)
    y = rng.normal(size=1000)
    data = pd.DataFrame({"target": y, "submeter_kwh": y + rng.normal(scale=0.06, size=1000)})
    neutral = run_integrity_checks(data, target="target", config={"domain_pack": "neutral"})
    energy = run_integrity_checks(data, target="target", config={"domain_pack": "energy"})
    assert not neutral.blocking_conflicts
    assert any(c.evidence.get("kind") == "energy_submeter_leakage" for c in energy.blocking_conflicts)
    data["submeter_kwh"] = data.target * 2
    assert run_integrity_checks(data, target="target", config={"domain_pack": "neutral"}).blocking_conflicts


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"targets": "target"},
        {"targets": ["target", "target"]},
        {"targets": ["time"]},
        {"domain_pack": "legacy_energy"},
        {"extra": True},
    ],
)
def test_public_request_rejects_ambiguous_or_unsupported_contracts(changes):
    with pytest.raises((ValueError, TypeError)):
        AnalysisRequest.from_dict({"schema_version": 1, "time_col": "time", "targets": ["target"], **changes})


def test_public_request_snapshots_nested_configuration():
    config = {"lags": [1, 3]}
    request = AnalysisRequest("time", ("target",), config=config)
    config["lags"].append(8)
    assert request.to_dict()["config"]["lags"] == [1, 3]
    assert AnalysisRequest.from_dict(request.to_dict()) == request


CREATE_ARTIFACT = r"""
import hashlib,joblib,sys
from pathlib import Path
class Executable:
    def __reduce__(self):
        return eval, ("__import__('pathlib').Path("+repr(sys.argv[2])+").write_text('executed')",)
p=Path(sys.argv[1])
joblib.dump(Executable() if sys.argv[3]=='executable' else {'approved':True},p)
digest=hashlib.sha256(p.read_bytes()).hexdigest()
Path(str(p)+'.sha256').write_text(digest+'  '+p.name+'\n')
print(digest)
"""


@pytest.mark.parametrize("kind", ["executable", "data"])
def test_ordinary_run_bundle_loader_requires_independent_external_approval(tmp_path, kind):
    from energybaseline.core.store import _load_approved_artifact
    from energybaseline.lock import ArtifactIntegrityError

    artifact, marker = tmp_path / "foreign.joblib", tmp_path / "must-not-exist"
    result = subprocess.run(
        [sys.executable, "-c", CREATE_ARTIFACT, str(artifact), str(marker), kind],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    digest = result.stdout.strip()
    assert digest == hashlib.sha256(artifact.read_bytes()).hexdigest()
    with pytest.raises(ArtifactIntegrityError, match="independently trusted"):
        _load_approved_artifact(artifact)
    assert not marker.exists()
    if kind == "data":
        assert _load_approved_artifact(artifact, trusted_artifact_sha256=digest) == {"approved": True}
    assert not marker.exists()


@pytest.mark.parametrize("pack", ["water", "manufacturing"])
def test_contrasting_domain_packs_use_public_contract_without_energy_rules(pack):
    from energybaseline.sdk import analyze_request
    from energybaseline.core.monitor import predict_deviation
    from energybaseline.tests_catalog.tc_assumptions import TestContractResult

    data = frame(72).reset_index(names="time")
    # Signed process measurements are legitimate; packs impose no consumption rule.
    data["target"] -= 20
    response = analyze_request(
        data,
        AnalysisRequest(
            "time",
            ("target",),
            pack,
            {
                "model_families": ["linear"],
                "hpo_trials": 0,
                "enable_nn": False,
                "enable_mining": False,
                "include_importance": False,
                "include_eda_attachment": False,
            },
        ),
    )
    result = response.run.targets["target"]
    assert result.status == "ok", result.error
    assert response.to_summary()["schema_version"] == 1
    assert response.to_summary()["domain_policy"]["key"] == pack
    assert not response.to_summary()["domain_policy"]["energy_features"]
    assert all(v is None for v in result.thresholds.values())
    assert "degree_days" not in result.eda
    for row in result.test_catalog:
        kwargs = {k: v for k, v in row.items() if k != "schema_version"}
        TestContractResult(**kwargs)
        assert row["p_raw"] is None
    bundle = next(iter(response.bundles["target"].values()))
    predictions = predict_deviation(data, bundle, "time")
    assert "actual_to_baseline_ratio" in predictions
    assert not {"enpi_ratio", "savings", "cumulative_savings"} & set(predictions)
    assert bundle["spec"].temp_col is None


def test_process_unit_conversions_use_declared_dimensions():
    from energybaseline.core.ingest import normalize_units, convert_units, UnitConversionError

    normalized, _ = normalize_units(
        pd.DataFrame({"pressure": [2.0], "mass": [500.0]}), {"pressure": "bar", "mass": "g"}
    )
    assert normalized.pressure.iloc[0] == pytest.approx(200.0)
    assert normalized.mass.iloc[0] == pytest.approx(0.5)
    with pytest.raises(UnitConversionError):
        convert_units(np.array([1.0]), "bar", "kg")


def test_real_driver_failure_traceback_omits_bound_values(tmp_path):
    import sqlite3
    import traceback
    from energybaseline.core.sqlsource import ConnectionProfile, QuerySpec, SourceError, run_query

    path = tmp_path / "failure.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE readings(value TEXT)")
    secret = "PRIVATE_BOUND_VALUE_77218"
    profile = ConnectionProfile(name="trace-redaction", driver="SQLite", database=str(path))
    with pytest.raises(SourceError) as captured:
        run_query(profile, QuerySpec("SELECT unknown_function(:value) FROM readings", (("value", secret),)))
    rendered = "".join(traceback.format_exception(captured.type, captured.value, captured.tb))
    assert secret not in rendered
    assert "sqlalchemy.exc" not in rendered


def test_catalog_cache_keys_include_residual_values_and_return_exact_repeats():
    data, vsg, panel = gates()
    panel.lock_preregistration()
    runner = CachedComplianceRunner(seed=42)
    rng = np.random.default_rng(151)
    residuals = {"target": {"first": rng.normal(size=len(data)), "second": rng.normal(size=len(data))}}
    initial = runner.execute_catalog_suite(data, vsg, panel, oof_errors_by_target=residuals, mode="fast")
    entries = len(runner.cache._stage_entries)
    repeated = runner.execute_catalog_suite(data, vsg, panel, oof_errors_by_target=residuals, mode="fast")
    assert len(runner.cache._stage_entries) == entries
    assert [r.to_dict() for r in initial["target"]] == [r.to_dict() for r in repeated["target"]]
    residuals["target"]["second"] = 20 * rng.normal(size=len(data))
    changed = runner.execute_catalog_suite(data, vsg, panel, oof_errors_by_target=residuals, mode="fast")
    assert len(runner.cache._stage_entries) == entries + 1
    old = next(r for r in initial["target"] if r.test_id == "T10")
    new = next(r for r in changed["target"] if r.test_id == "T10")
    assert old.to_dict() != new.to_dict()


def test_lock_failure_does_not_create_or_replace_active_selection(tmp_path, monkeypatch):
    import joblib
    from energybaseline.core import store
    from energybaseline.db.session import init_database
    from energybaseline.lock import BaselineLockManager

    init_database()
    run_id = store.new_run(None, ["target"], {"domain_pack": "neutral"})
    artifact = store.artifact_path(run_id, "target", "baseline")
    joblib.dump({"target": "target", "model_name": "baseline", "domain_policy": {"key": "neutral"}}, artifact)
    store._write_sha256_sidecar(artifact)
    first = store.lock_baseline(run_id, "target", "baseline", "Verified original selection")
    assert first["version"] == 1
    before = store.selection_history(run_id)

    def fail_write(*args, **kwargs):
        raise OSError("deliberate lock persistence failure")

    monkeypatch.setattr(BaselineLockManager, "lock_baseline", fail_write)
    with pytest.raises(OSError, match="persistence failure"):
        store.lock_baseline(run_id, "target", "baseline", "Must not become active")
    assert store.selection_history(run_id) == before
    assert store.active_selection(run_id, "target")["id"] == first["id"]
    with pytest.raises(FileNotFoundError):
        store.lock_baseline(run_id, "target", "missing", "No artifact is not a baseline")
    assert store.selection_history(run_id) == before


@pytest.mark.parametrize(
    "pack,interval,expected",
    [
        ("neutral", "monthly", {}),
        ("energy", "daily", {}),
        ("energy", "monthly", {"max_cvrmse": 15.0, "max_nmbe": 5.0, "min_r2": 0.75}),
    ],
)
def test_lock_policy_never_infers_energy_acceptance_from_an_arbitrary_target(tmp_path, pack, interval, expected):
    from energybaseline.lock import BaselineLockManager

    record = BaselineLockManager(tmp_path).lock_baseline(
        run_id="policy",
        target="target",
        model_name="test",
        family="linear",
        is_parametric=False,
        fitted_artifact={"values": [1, 2]},
        formula_or_architecture="test",
        domain_pack=pack,
        baseline_window={"granularity": interval},
    )
    assert dict(record.thresholds) == expected
    card = dict(record.iso50006_model_card)
    assert bool(card["governing_standards"]) == (pack == "energy")
    assert card["domain_policy"]["key"] == pack


def test_failed_os_store_readback_cannot_claim_persistent_credentials(monkeypatch):
    from energybaseline.core import secrets

    class NonPersistingStore:
        def get_password(self, *args):
            return None

        def set_password(self, *args):
            pass

    monkeypatch.setattr(secrets, "_secure_keyring", lambda: NonPersistingStore())
    monkeypatch.setattr(secrets, "insecure_credential_file_allowed", lambda: False)
    cipher, scheme = secrets._fernet(create=True)
    assert cipher is not None and scheme == "session-fernet"
    assert not secrets.backend_status()["persistent"]
