"""Gate G7 executable contracts: real MAPIE, reporting fuzz, read-only drift and snapshots."""

from __future__ import annotations
import hashlib
import importlib.util
import json
import zipfile

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression

from energybaseline.core.vsg import VariableSelectionGate, ComplianceGateError
from energybaseline.core.tcp import TestConfigPanel as TCP
from energybaseline.uq import ConformalIntervals, run_uq_catalog, fractional_savings_uncertainty, savings_uncertainty
from energybaseline.uq.calibration import interval_diagnostics
from energybaseline.uq.parametric import ols_hac_intervals, quantile_intervals, crosscheck_intervals
from energybaseline.monitoring import (
    ReportingSchema,
    ReportingInputError,
    validate_reporting,
    predict_reporting,
    MonitoringHistory,
    cusum_chart,
    ewma_chart,
    NonRoutineAdjustment,
    run_drift_monitor,
)
from energybaseline.monitoring.adjustments import apply_adjustments
from energybaseline.monitoring.drift import covariate_drift, residual_regime, miscoverage_monitor
from energybaseline.monitoring.safety import assert_safe_payload, inspect_export_zip
from energybaseline.monitoring.prediction import frame_input_hash
from energybaseline.scoring import fit_validity_domain_envelope
from energybaseline.lock.baseline_lock import BaselineLockManager, monitoring_snapshot, ArtifactIntegrityError
from energybaseline.causal import run_causal_mv, kernel_balance, optional_backend_effect, CausalBackendUnavailable


@pytest.fixture
def tcp():
    rng = np.random.default_rng(123)
    X = rng.normal(size=200)
    df = pd.DataFrame({"x": X, "energy": 20 + 3 * X + rng.normal(size=200)})
    gate = VariableSelectionGate(df, targets=["energy"])
    gate.accept()
    config = TCP(gate)
    config.lock_preregistration()
    return config


def energy_series(n=1800, seed=2026, rho=0.3):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 2))
    e = rng.normal(size=n)
    for i in range(1, n):
        e[i] += rho * e[i - 1]
    y = 100 + 3 * X[:, 0] + 2 * X[:, 1] + e
    return X, y


@pytest.mark.parametrize("method", ["split", "cross", "jackknife", "quantile", "enbpi", "aci"])
def test_uq1_real_mapie_backends(method):
    X, y = energy_series(400)
    levels = (0.95,) if method == "quantile" else (0.8, 0.95)
    original = LinearRegression().fit(X[:200], y[:200])
    before = original.coef_.copy()
    adapter = ConformalIntervals(original, method=method, levels=levels).fit(X[:200], y[:200], X[200:300], y[200:300])
    pred, bounds = adapter.predict(X[300:])
    assert pred.shape == (100,) and bounds.shape == (100, 2, len(levels))
    assert np.isfinite(bounds).all() and np.all(bounds[:, 0, :] <= bounds[:, 1, :])
    assert "mapie" in type(adapter.backend_).__module__
    np.testing.assert_array_equal(original.coef_, before)
    if method in ("enbpi", "aci"):
        p0 = adapter.backend_.estimator_.single_estimator_.coef_.copy()
        adapter.observe(X[300:301], y[300:301])
        np.testing.assert_array_equal(adapter.backend_.estimator_.single_estimator_.coef_, p0)
    else:
        with pytest.raises(ValueError):
            adapter.observe(X[300:301], y[300:301])


@pytest.mark.parametrize("method", ["enbpi", "aci"])
def test_uq3_ac16_serial_holdout_calibration(method, tcp):
    X, y = energy_series()
    adapter = ConformalIntervals(method=method).fit(X[:800], y[:800])
    result = run_uq_catalog(adapter, X[800:], y[800:], tcp=tcp, sequential=True, gamma=0.005)
    assert result["ok"], result["calibration"]
    for d in result["calibration"]:
        assert d["kupiec_p"] > 0.01, d
        assert d["christoffersen_independence_p"] > 0.01, d
        assert d["christoffersen_conditional_p"] > 0.01, d
    # A future outcome injection cannot change already-emitted intervals.
    changed = y.copy()
    changed[-1] += 1000
    other = ConformalIntervals(method=method).fit(X[:800], y[:800])
    out = run_uq_catalog(other, X[800:], changed[800:], tcp=tcp, sequential=True, gamma=0.005)
    np.testing.assert_array_equal(result["intervals"], out["intervals"])


def test_uq2_parametric_quantile_conformal_crosscheck():
    X, y = energy_series(600, rho=0)
    param = ols_hac_intervals(X[:300], y[:300], X[450:])
    qlo, qhi = quantile_intervals(X[:300], y[:300], X[450:])
    cf = ConformalIntervals(method="split", levels=(0.95,)).fit(X[:300], y[:300], X[300:450], y[300:450])
    _, b = cf.predict(X[450:])
    for lo, hi in [(param["lower"], param["upper"]), (qlo, qhi)]:
        assert crosscheck_intervals(lo, hi, b[:, 0, 0], b[:, 1, 0])["disjoint_fraction"] < 0.05
        assert interval_diagnostics(y[450:], lo, hi)["within_tolerance"]
    with pytest.raises(ValueError):
        ols_hac_intervals(np.ones((30, 2)), y[:30], X[:5])
    with pytest.raises(ValueError):
        crosscheck_intervals([2], [1], [0], [3])


def test_uq4_uq5_cumulative_serial_uncertainty_and_fsu():
    rng = np.random.default_rng(123)
    iid = rng.normal(size=2000)
    ar = iid.copy()
    for i in range(1, len(ar)):
        ar[i] += 0.8 * ar[i - 1]
    ar = ar / np.std(ar) * np.std(iid)
    saving = np.full(90, 10.0)
    a = savings_uncertainty(saving, iid)
    b = savings_uncertainty(saving, ar)
    assert b["half_width"] > a["half_width"] * 1.5
    assert a["lower"] < 900 < a["upper"]
    assert (
        fractional_savings_uncertainty(saving, iid, level=0.95)["fsu"]
        > fractional_savings_uncertainty(saving, iid, level=0.90)["fsu"]
    )
    assert fractional_savings_uncertainty([0], iid)["fsu"] is None
    assert savings_uncertainty(saving, iid, static_factor_sd=2)["half_width"] > a["half_width"]
    for bad in [0, 1, -0.5, float("nan")]:
        with pytest.raises(ValueError):
            savings_uncertainty(saving, iid, level=bad)
    with pytest.raises(ValueError):
        savings_uncertainty(saving, iid, measurement_sd=-1)


@pytest.fixture
def monitoring_case(tmp_path):
    rng = np.random.default_rng(10)
    X = pd.DataFrame({"x": rng.uniform(0, 10, 300)})
    y = 100 + 5 * X.x + rng.normal(size=300)
    model = LinearRegression().fit(X, y)
    envelope = fit_validity_domain_envelope(X)
    manager = BaselineLockManager(tmp_path / "locks")
    record = manager.lock_baseline(
        run_id="phase7",
        target="energy",
        model_name="OLS",
        family="F1",
        is_parametric=True,
        fitted_artifact=model,
        formula_or_architecture="b0+b1*x",
        selected_features=["x"],
        validity_envelope=envelope.to_dict(),
    )
    schema = ReportingSchema(("x",), "energy", (("x", "C"), ("energy", "kWh")), 86400.0)
    frame = pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=60, freq="D"), "x": rng.uniform(0, 10, 60)})
    frame["energy"] = model.predict(frame[["x"]]) - 3 + rng.normal(size=60)
    residuals = y.to_numpy() - model.predict(X)
    return schema, frame, model, envelope, residuals, record


def test_pm1_reporting_fuzz_corpus(monitoring_case):
    schema, good, *_ = monitoring_case
    valid = validate_reporting(good, schema, units=dict(schema.units))
    assert len(valid) == 60
    corpus = [
        None,
        [],
        {},
        pd.DataFrame(),
        good.drop(columns="x"),
        good.assign(extra=1),
        good.iloc[::-1],
        pd.concat([good.iloc[:1], good]),
        good.assign(timestamp="broken"),
        good.assign(timestamp=pd.NaT),
        good.assign(x="1"),
        good.assign(x=True),
        good.assign(x=np.nan),
        good.assign(x=np.inf),
        good.rename(columns={"x": "energy"}),
    ]
    for i in range(100):
        bad = good.copy()
        if i % 3 == 0:
            bad.loc[i % 60, "x"] = np.nan
        elif i % 3 == 1:
            bad.loc[i % 60, "energy"] = -np.inf
        else:
            bad.loc[i % 60, "timestamp"] = pd.NaT
        corpus.append(bad)
    for bad in corpus:
        with pytest.raises(ReportingInputError):
            validate_reporting(bad, schema, units=dict(schema.units))
    with pytest.raises(ReportingInputError):
        validate_reporting(good, schema, units={"x": "F", "energy": "kWh"})
    with pytest.raises(ReportingInputError):
        validate_reporting(good.iloc[::2], schema, units=dict(schema.units))


def test_pm2_pm3_pm4_pm5_pm6_dom3_prediction(monitoring_case):
    schema, frame, model, envelope, r, _ = monitoring_case
    before = model.coef_.copy()
    event = NonRoutineAdjustment(2.0, "new production line", "engineer", "event-1", 0.3)
    result = predict_reporting(
        frame,
        schema=schema,
        units=dict(schema.units),
        model=model,
        envelope=envelope,
        calibration_residuals=r,
        events=[event],
    )
    rows = pd.DataFrame(result["rows"])
    np.testing.assert_allclose(rows.adjusted_baseline - rows.routine_baseline, 2.0)
    np.testing.assert_allclose(rows.savings, rows.adjusted_baseline - rows.actual)
    np.testing.assert_allclose(rows.cumulative_savings, rows.savings.cumsum())
    np.testing.assert_allclose(rows.enpi_ratio, rows.actual / rows.adjusted_baseline)
    assert np.all(rows.cumulative_lower <= rows.cumulative_savings)
    assert np.all(rows.cumulative_upper >= rows.cumulative_savings)
    assert result["adjustments"]["non_routine"][0]["event_id"] == "event-1"
    np.testing.assert_array_equal(before, model.coef_)
    outside = frame.copy()
    outside["x"] = 100
    out = predict_reporting(
        outside, schema=schema, units=dict(schema.units), model=model, envelope=envelope, calibration_residuals=r
    )
    assert all(v["extrapolation"] for v in out["rows"])
    with pytest.raises(ValueError):
        NonRoutineAdjustment(2.0, "", "engineer", "event")
    with pytest.raises(ValueError):
        apply_adjustments([1], [event, event])
    z = np.r_[np.zeros(20), np.ones(20) * 3]
    assert any(cusum_chart(z)["alarm"][20:])
    assert any(ewma_chart(z)["alarm"][20:])
    assert not any(cusum_chart(np.zeros(40))["alarm"])
    assert not any(ewma_chart(np.zeros(40))["alarm"])
    for fn, kw in [(cusum_chart, {"k": 0}), (ewma_chart, {"smoothing": 2})]:
        with pytest.raises(ValueError):
            fn(z, **kw)


def test_pm7_snapshot_baseline_input_binding_and_integrity(monitoring_case, tmp_path):
    schema, frame, _, _, _, record = monitoring_case
    hist = MonitoringHistory(tmp_path / "monitor.jsonl")
    result = {"savings": 32.0}
    sn = hist.append(
        baseline=record, frame=frame, result=result, batch_id="batch1", schema=schema, units=dict(schema.units)
    )
    assert sn["baseline_version"] == record.version and sn["baseline_sha256"] == record.record_sha256
    assert sn["input_sha256"] == frame_input_hash(validate_reporting(frame, schema, units=dict(schema.units)))
    assert len(hist.read()) == 1
    with pytest.raises(ValueError):
        hist.append(
            baseline=record, frame=frame, result=result, batch_id="batch2", schema=schema, units=dict(schema.units)
        )
    later = frame.copy()
    later["timestamp"] += pd.Timedelta(days=60)
    sn2 = hist.append(
        baseline=record, frame=later, result=result, batch_id="batch2", schema=schema, units=dict(schema.units)
    )
    assert sn2["previous_sha256"] == sn["sha256"] and len(hist.read()) == 2
    hist.path.write_text(hist.path.read_text().replace('"savings": 32.0', '"savings": 33.0'))
    with pytest.raises(ValueError):
        hist.read()
    with pytest.raises(ValueError):
        monitoring_snapshot(record, "invalid", {})
    with pytest.raises(ValueError):
        monitoring_snapshot(record, "a" * 64, {"password": "not-for-export"})
    artifact = __import__("pathlib").Path(record.artifact_path)
    artifact.write_bytes(artifact.read_bytes() + b"x")
    with pytest.raises(ArtifactIntegrityError):
        monitoring_snapshot(record, "a" * 64, {})


def test_dr1_dr2_dr3_dr4_dr5_ac32_readonly_advisories(tcp, monitoring_case):
    rng = np.random.default_rng(2026)
    reference = rng.normal(size=400)
    stable = reference.copy()
    assert not covariate_drift(reference, stable)["triggered"]
    assert covariate_drift(reference, stable + 3)["triggered"]
    assert not residual_regime(reference, stable)["triggered"]
    assert residual_regime(reference, stable - 3)["triggered"]
    assert residual_regime(reference, stable * 4)["triggered"]
    assert miscoverage_monitor(np.ones(100) * 4, np.full(100, -2), np.full(100, 2))["degraded"]
    _, _, model, _, _, _ = monitoring_case
    digest = hashlib.sha256(model.coef_.tobytes()).hexdigest()
    drivers = pd.DataFrame({"x": reference})
    improvement = run_drift_monitor(drivers, drivers, reference, stable - 3, tcp=tcp, domain_flags=np.zeros(400, bool))
    assert improvement["possible_efficiency_improvement"]
    breakdown = run_drift_monitor(drivers, drivers + 3, reference, stable + 3, tcp=tcp, domain_flags=np.ones(400, bool))
    assert not breakdown["possible_efficiency_improvement"] and breakdown["advisory"]["rebaseline_review"]
    quiet = run_drift_monitor(drivers, drivers, reference, stable, tcp=tcp)
    assert not quiet["advisory"]["rebaseline_review"]
    seasonal = run_drift_monitor(drivers, drivers + 3, reference, stable, tcp=tcp)
    assert not seasonal["advisory"]["rebaseline_review"]
    assert not improvement["model_mutated"] and digest == hashlib.sha256(model.coef_.tobytes()).hexdigest()


def causal_series(seed=42, effect=-8.0):
    rng = np.random.default_rng(seed)
    n = 800
    t = np.repeat(np.arange(n // 2), 2).astype(float)
    g = np.tile([0, 1], n // 2)
    post = (t >= n / 4).astype(int)
    X = rng.normal(size=(n, 1))
    y = 100 + 2 * X[:, 0] + 0.02 * t + 3 * g + effect * g * post + rng.normal(0, 0.3, n)
    return y, X, g, post, t


def test_t14_balance_overlap_trends_effect_refutation(tcp):
    data = causal_series()
    result = run_causal_mv(*data, tcp=tcp)
    assert result["eligible"], result
    assert abs(result["effect"] + 8) < 0.5
    assert result["refutation"]["placebo_p"] <= 0.05
    assert result["label"] == "possible savings / non-routine event"
    assert result["effect_interval"][0] < -8 < result["effect_interval"][1]
    assert sum(result["kernel_balance"]["control_weights"]) == pytest.approx(1.0)
    y, X, g, p, t = data
    fail = run_causal_mv(y + 0.5 * g * t, X, g, p, t, tcp=tcp)
    assert not fail["eligible"] and fail["effect"] is None
    Xbad = X + g[:, None] * 10
    fail = run_causal_mv(y, Xbad, g, p, t, tcp=tcp)
    assert not fail["eligible"]
    with pytest.raises(ValueError):
        kernel_balance(X, np.zeros(len(y)))


@pytest.mark.parametrize("backend", ["dowhy", "econml"])
def test_t14_optional_backend_honest_status(tcp, backend):
    if importlib.util.find_spec(backend) is None:
        with pytest.raises(CausalBackendUnavailable):
            optional_backend_effect(
                pd.DataFrame(), treatment="g", outcome="y", common_causes=["x"], tcp=tcp, backend=backend
            )
    else:
        y, X, g, _, _ = causal_series()
        df = pd.DataFrame({"y": y, "g": g, "x": X[:, 0]})
        result = optional_backend_effect(df, treatment="g", outcome="y", common_causes=["x"], tcp=tcp, backend=backend)
        assert np.isfinite(result["effect"])


def test_no_inference_before_vsg_tcp():
    X, y = energy_series(100)
    gate = VariableSelectionGate(pd.DataFrame({"x": X[:, 0], "energy": y}), targets=["energy"])
    config = TCP(gate)
    with pytest.raises(ComplianceGateError):
        run_causal_mv(*causal_series(), tcp=config)
    with pytest.raises(ComplianceGateError):
        run_drift_monitor({}, {}, y, y, tcp=config)
    with pytest.raises(ComplianceGateError):
        run_uq_catalog(None, X, y, tcp=config)


def test_export_safety_zip_and_json(tmp_path):
    for payload in [{"api_key": "x"}, {"notes": "postgresql://user:pw@server/db"}, {"note": "PWD=plaintext"}]:
        with pytest.raises(ValueError):
            assert_safe_payload(payload)
    good = tmp_path / "good.ebproj"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("monitoring.json", json.dumps({"savings": 42}))
    assert inspect_export_zip(good)
    bad = tmp_path / "bad.ebproj"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("monitoring.json", json.dumps({"password": "plaintext"}))
    with pytest.raises(ValueError):
        inspect_export_zip(bad)


def test_incremental_charts_savings_and_snapshot_no_model_mutation(monitoring_case, tmp_path):
    schema, frame, model, envelope, r, record = monitoring_case
    hist = MonitoringHistory(tmp_path / "incremental.jsonl")
    units = dict(schema.units)

    def predict(data):
        return predict_reporting(
            data, schema=schema, units=units, model=model, envelope=envelope, calibration_residuals=r
        )

    first = frame.iloc[:30].copy()
    second = frame.iloc[30:].copy()
    hist.append(
        baseline=record,
        frame=first,
        result=predict(first),
        batch_id="one",
        schema=schema,
        units=units,
        calibration_residuals=r,
    )
    sn = hist.append(
        baseline=record,
        frame=second,
        result=predict(second),
        batch_id="two",
        schema=schema,
        units=units,
        calibration_residuals=r,
    )
    full = predict(frame)
    result = sn["result"]
    assert result["monitored_rows"] == 60
    assert result["rows"][-1]["cumulative_savings"] == pytest.approx(full["rows"][-1]["cumulative_savings"])
    assert result["rows"][-1]["cumulative_lower"] == pytest.approx(full["rows"][-1]["cumulative_lower"])
    np.testing.assert_allclose(result["cusum"]["negative"], full["cusum"]["negative"])
    np.testing.assert_allclose(result["ewma"]["value"], full["ewma"]["value"])
    assert record.verify_integrity()


def test_cli_uq_and_locked_monitoring_real_inputs(monitoring_case, tmp_path, capsys):
    from energybaseline.cli import main

    schema, frame, model, envelope, r, record = monitoring_case
    X, y = energy_series(1000, rho=0.0)
    df = pd.DataFrame(
        {"timestamp": pd.date_range("2021-01-01", periods=len(y), freq="D"), "x": X[:, 0], "x2": X[:, 1], "energy": y}
    )
    source = tmp_path / "uq.csv"
    df.to_csv(source, index=False)
    out = tmp_path / "uq.json"
    assert (
        main(
            [
                "uq-calibrate",
                str(source),
                "--targets",
                "energy",
                "--drivers",
                "x,x2",
                "--time-col",
                "timestamp",
                "--method",
                "split",
                "--out",
                str(out),
            ]
        )
        == 1
    )
    # This small calibration partition undercovers: the CLI must fail honestly,
    # rather than widen intervals using unseen holdout outcomes.
    assert not json.loads(out.read_text())["ok"]
    reporting = tmp_path / "reporting.csv"
    frame.to_csv(reporting, index=False)
    # Calibration observations are earlier than reporting, with a frozen model and driver set.
    reference = tmp_path / "reference.csv"
    rng = np.random.default_rng(123)
    ref = pd.DataFrame({"x": rng.uniform(0, 10, 200)})
    ref["energy"] = model.predict(ref) + rng.normal(size=len(ref))
    ref.insert(0, "timestamp", pd.date_range("2024-01-01", periods=len(ref), freq="D"))
    ref.to_csv(reference, index=False)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"drivers": ["x"], "units": dict(schema.units), "step_seconds": 86400}))
    output = tmp_path / "monitoring.json"
    assert (
        main(
            [
                "monitoring-run",
                str(reporting),
                "--baseline",
                record.manifest_path,
                "--config",
                str(config),
                "--reference",
                str(reference),
                "--history",
                str(tmp_path / "history.jsonl"),
                "--batch-id",
                "batch1",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text())
    assert result["baseline_version"] == 1 and len(result["result"]["rows"]) == 60
    assert not result["result"]["drift"]["model_mutated"]
    capsys.readouterr()


def test_independent_stable_drift_and_injected_mean_variance_shifts():
    # Independent stationary samples, not just identical copies. Practical-effect
    # confirmation prevents PH/CUSUM noise from turning into an engineering advisory.
    for seed in range(10):
        rng = np.random.default_rng(seed)
        a, b = rng.normal(size=400), rng.normal(size=400)
        assert not covariate_drift(a, b)["triggered"]
        assert not residual_regime(a, b)["triggered"]
        assert residual_regime(a, b + 3)["triggered"]
        assert residual_regime(a, b * 4)["triggered"]


def test_aci_updates_alpha_not_locked_parameters_and_backtests_detect_clustered_misses():
    X, y = energy_series(400)
    adapter = ConformalIntervals(method="aci").fit(X[:300], y[:300])
    adapter.predict(X[300:301])
    original = dict(adapter.backend_.current_alpha)
    coefficients = adapter.backend_.estimator_.single_estimator_.coef_.copy()
    adapter.observe(X[300:301], y[300:301] + 100, gamma=0.005)
    assert any(adapter.backend_.current_alpha[k] < v for k, v in original.items())
    np.testing.assert_array_equal(coefficients, adapter.backend_.estimator_.single_estimator_.coef_)
    # Correct marginal miss rate can still hide clustered failures.
    values = np.r_[np.zeros(950), np.ones(50) * 10]
    diagnostic = interval_diagnostics(values, np.full(1000, -2.0), np.full(1000, 2.0))
    assert diagnostic["within_tolerance"]
    assert diagnostic["christoffersen_independence_p"] < 0.01


def test_connection_payload_encrypted_and_migration_preserves_bytes(eb_home, tmp_path):
    from energybaseline.db.session import init_database, session_scope
    from energybaseline.db.models import Connection
    from energybaseline.core.sqlsource import ConnectionProfile, save_profile, load_profiles
    from sqlalchemy import select, create_engine, text
    from alembic import command

    init_database()
    marker = "phase7-test-only-nonproduction-value"
    profile = ConnectionProfile(name="phase7-audit", driver="postgresql", password=marker)
    save_profile(profile)
    with session_scope() as session:
        row = session.execute(select(Connection).where(Connection.name == profile.name)).scalar_one()
        assert marker not in row.protected_payload
    loaded = next(p for p in load_profiles() if p.name == profile.name)
    assert loaded.password == marker
    # Isolated reversible migration, including existing encrypted bytes.
    path = tmp_path / "migration.sqlite"
    engine = create_engine("sqlite:///" + str(path))
    from energybaseline.db.migrate import alembic_config

    config = alembic_config("sqlite:///" + str(path))
    command.upgrade(config, "0003")
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO connections (name,driver,secret_blob) VALUES ('old','sqlite','ciphertext-only')")
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT protected_payload FROM connections")).scalar() == "ciphertext-only"
    command.downgrade(config, "0003")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT secret_blob FROM connections")).scalar() == "ciphertext-only"
    engine.dispose()


def test_snapshot_hash_distinguishes_float_ulps_and_timestamp_nanoseconds():
    original = pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=3, freq="ns"), "x": [1.0, 2.0, 3.0]})
    altered = original.copy()
    altered.loc[0, "x"] = np.nextafter(1.0, 2.0)
    assert frame_input_hash(original) != frame_input_hash(altered)
    altered = original.copy()
    altered.loc[0, "timestamp"] += pd.Timedelta(nanoseconds=1)
    assert frame_input_hash(original) != frame_input_hash(altered)
    assert frame_input_hash(original) == frame_input_hash(original.copy())
