"""Reproducible E2 evidence from the Phase 7 product APIs, without mock results."""

from __future__ import annotations

# ruff: noqa: E402  # Direct-script entry point bootstraps the repository import root.

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from energybaseline.core.vsg import VariableSelectionGate
from energybaseline.core.tcp import TestConfigPanel
from energybaseline.uq import ConformalIntervals, run_uq_catalog, fractional_savings_uncertainty
from energybaseline.uq.parametric import ols_hac_intervals, quantile_intervals, crosscheck_intervals
from energybaseline.monitoring import ReportingSchema, MonitoringHistory, predict_reporting, run_drift_monitor
from energybaseline.monitoring.prediction import ReportingInputError, validate_reporting
from energybaseline.monitoring.safety import assert_safe_payload
from energybaseline.scoring import fit_validity_domain_envelope
from energybaseline.lock.baseline_lock import BaselineLockManager
from energybaseline.causal import run_causal_mv, optional_backend_effect, CausalBackendUnavailable


def emit(out, name, value):
    assert_safe_payload(value)
    (out / name).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def produce(out):
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(2026)
    n = 1800
    X = rng.normal(size=(n, 2))
    e = rng.normal(size=n)
    for i in range(1, n):
        e[i] += 0.3 * e[i - 1]
    y = 100 + 3 * X[:, 0] + 2 * X[:, 1] + e
    train = pd.DataFrame({"x": X[:800, 0], "x2": X[:800, 1], "energy": y[:800]})
    vsg = VariableSelectionGate(train, targets=["energy"])
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    tcp.lock_preregistration()
    uq_results = {}
    for method in ("enbpi", "aci"):
        adapter = ConformalIntervals(method=method).fit(X[:800], y[:800])
        uq_results[method] = run_uq_catalog(adapter, X[800:], y[800:], tcp=tcp, sequential=True, gamma=0.005)
    emit(out, "uq_calibration.json", uq_results)
    adapter = ConformalIntervals(method="split", levels=(0.95,)).fit(X[:500], y[:500], X[500:800], y[500:800])
    _, bounds = adapter.predict(X[800:])
    parametric = ols_hac_intervals(X[:500], y[:500], X[800:])
    qlo, qhi = quantile_intervals(X[:500], y[:500], X[800:])
    emit(
        out,
        "parametric_crosscheck.json",
        {
            "ols_hac": crosscheck_intervals(parametric["lower"], parametric["upper"], bounds[:, 0, 0], bounds[:, 1, 0]),
            "quantile": crosscheck_intervals(qlo, qhi, bounds[:, 0, 0], bounds[:, 1, 0]),
        },
    )
    model = LinearRegression().fit(train[["x", "x2"]], train.energy)
    calibration = y[500:800] - model.predict(pd.DataFrame(X[500:800], columns=["x", "x2"]))
    envelope = fit_validity_domain_envelope(train[["x", "x2"]])
    schema = ReportingSchema(("x", "x2"), "energy", (("x", "C"), ("x2", "unit"), ("energy", "kWh")), 86400)
    frame = pd.DataFrame(X[800:920], columns=["x", "x2"])
    frame.insert(0, "timestamp", pd.date_range("2025-01-01", periods=len(frame), freq="D"))
    frame["energy"] = y[800:920] - 3

    def predict(data):
        return predict_reporting(
            data,
            schema=schema,
            units=dict(schema.units),
            model=model,
            envelope=envelope,
            calibration_residuals=calibration,
        )

    full = predict(frame)
    emit(out, "prediction.json", full)
    emit(
        out,
        "fsu.json",
        {
            str(level): fractional_savings_uncertainty(
                [row["savings"] for row in full["rows"]], calibration, level=level
            )
            for level in (0.9, 0.95)
        },
    )
    before = hashlib.sha256(model.coef_.tobytes()).hexdigest()
    drivers = pd.DataFrame({"x": rng.normal(size=400)})
    residuals = rng.normal(size=400)
    emit(
        out,
        "drift_monitor.json",
        {
            "stable": run_drift_monitor(drivers, drivers.copy(), residuals, residuals.copy(), tcp=tcp),
            "shifted": run_drift_monitor(drivers, drivers + 3, residuals, residuals + 3, tcp=tcp),
            "possible_improvement": run_drift_monitor(drivers, drivers.copy(), residuals, residuals - 3, tcp=tcp),
            "model_coefficients_unchanged": before == hashlib.sha256(model.coef_.tobytes()).hexdigest(),
        },
    )
    with tempfile.TemporaryDirectory(prefix="eb_phase7_") as directory:
        manager = BaselineLockManager(Path(directory) / "locks")
        baseline = manager.lock_baseline(
            run_id="phase7-evidence",
            target="energy",
            model_name="OLS",
            family="F1",
            is_parametric=True,
            fitted_artifact=model,
            formula_or_architecture="b0+b1*x+b2*x2",
            selected_features=schema.drivers,
            validity_envelope=envelope.to_dict(),
            vsg_snapshot={"accepted": True, "sha256": vsg.vsg_sha256},
            tcp_snapshot={"accepted": True, "sha256": tcp.preregistration_sha256},
        )
        history = MonitoringHistory(Path(directory) / "history.jsonl")
        for i, batch in enumerate((frame.iloc[:60].copy(), frame.iloc[60:].copy())):
            snapshot = history.append(
                baseline=baseline,
                frame=batch,
                result=predict(batch),
                batch_id=f"batch-{i}",
                schema=schema,
                units=dict(schema.units),
                calibration_residuals=calibration,
            )
        emit(out, "locked_baseline_snapshot.json", snapshot)
        emit(out, "monitoring_audit.json", history.read())
    corpus = [
        None,
        {},
        pd.DataFrame(),
        frame.drop(columns="x"),
        frame.assign(x=np.nan),
        frame.assign(x=np.inf),
        frame.assign(timestamp="invalid"),
        frame.iloc[::-1],
        frame.iloc[::2],
        frame.assign(extra=0),
    ]
    rejected = 0
    for bad in corpus:
        try:
            validate_reporting(bad, schema, units=dict(schema.units))
        except ReportingInputError:
            rejected += 1
    emit(
        out,
        "reporting_fuzz.json",
        {
            "malformed_cases": len(corpus),
            "rejected": rejected,
            "valid_rows_accepted": len(validate_reporting(frame, schema, units=dict(schema.units))),
            "ok": rejected == len(corpus),
        },
    )
    crng = np.random.default_rng(42)
    time = np.repeat(np.arange(400), 2).astype(float)
    treatment = np.tile([0, 1], 400)
    post = (time >= 200).astype(int)
    cov = crng.normal(size=(800, 1))
    outcome = 100 + 2 * cov[:, 0] + 0.02 * time + 3 * treatment - 8 * treatment * post + crng.normal(0, 0.3, 800)
    causal = run_causal_mv(outcome, cov, treatment, post, time, tcp=tcp)
    backend_data = pd.DataFrame(
        {"x": cov[:, 0], "g": treatment, "y": 100 + 2 * cov[:, 0] - 8 * treatment + crng.normal(0, 0.3, 800)}
    )
    for backend in ("econml", "dowhy"):
        try:
            causal[backend] = optional_backend_effect(
                backend_data, treatment="g", outcome="y", common_causes=["x"], tcp=tcp, backend=backend
            )
        except CausalBackendUnavailable as exc:
            causal[backend] = {"status": "skipped_optional_backend", "reason": str(exc)}
    emit(out, "causal_mv.json", causal)
    for command, filename in (("system.health", "system_health.json"), ("system.sbom", "sbom.json")):
        subprocess.run(
            [sys.executable, "-m", "energybaseline.cli", command, "--out", str(out / filename)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.glob("*.json"))
        if p.name != "manifest.json"
    }
    emit(
        out,
        "manifest.json",
        {
            "source_commit": commit,
            "files_sha256": manifest,
            "all_uq_within_tolerance": all(v["ok"] for v in uq_results.values()),
        },
    )
    if not all(v["ok"] for v in uq_results.values()) or rejected != len(corpus):
        raise RuntimeError("Phase 7 evidence quality gate failed")
    print(json.dumps({"phase": 7, "output": str(out), "source_commit": commit, "ok": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "artifacts/phase7")
    produce(parser.parse_args().out_dir.resolve())
