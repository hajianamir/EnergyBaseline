"""Real execution of every isolated engine and pretrained model on this platform; no mocks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import pytest

from energybaseline import assets, isolated
from energybaseline.workbench import run_workbench

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/isolated"
PLATFORM = isolated._platform()
AUTOML = [name for name in ("tpot", "h2o", "autogluon", "autosklearn") if PLATFORM in isolated.ENGINES[name].platforms]
REFITS = {"tpot": True, "h2o": False, "autogluon": True, "autosklearn": True}


def _request(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _frame():
    return pd.read_csv(EXAMPLES / "series.csv")


def _engine_processes():
    """Command lines of live processes that reference a private engine workspace."""
    if sys.platform == "win32":
        command = "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"
        listing = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=120
        ).stdout
    else:
        # Complete command lines straight from the kernel: ``ps`` may truncate them to a column width.
        lines = []
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    lines.append((entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace"))
                except OSError:
                    continue  # the process ended while it was being listed
        listing = "\n".join(lines)
    marker = "EnergyBaselineEngines"
    return [line for line in listing.splitlines() if marker in line and "Get-CimInstance" not in line]


def _wait_no_engine_processes(seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and _engine_processes():
        time.sleep(0.5)
    return _engine_processes()


def test_every_supported_runtime_is_installed_from_its_current_lock(provisioned_home):
    for name, spec in isolated.ENGINES.items():
        status = isolated.engine_status(name)
        if PLATFORM not in spec.platforms:
            assert status["state"] == "unsupported-platform"
            continue
        assert status["state"] == "installed", name
        manifest = json.loads((isolated.runtime_dir(name) / isolated.MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["lock_sha256"] == isolated.lock_sha256(name) and manifest["version"] == spec.version
        assert manifest["python"].startswith(f"{spec.python[0]}.{spec.python[1]}.")
        pins = isolated.locked_distributions(name)
        installed = {k: v for k, v in manifest["distributions"].items() if k in pins}
        assert installed == pins
        assert set(manifest["distributions"]) - set(pins) <= isolated.BOOTSTRAP
    for name in assets.ASSETS:
        assert assets.asset_status(name)["state"] == "installed"
        assert assets.verified_asset(name) == provisioned_home / "model-assets" / name


@pytest.mark.parametrize("engine", AUTOML)
def test_automl_engine_searches_refits_and_predicts_without_seeing_holdout_targets(engine):
    frame, request = _frame(), _request(f"automl-{engine}.json")
    first = run_workbench(request, frame)
    result = first["result"]
    spec = isolated.ENGINES[engine]
    assert result["engine"] == engine and result["engine_version"] == spec.version
    assert result["lock_sha256"] == isolated.lock_sha256(engine)
    assert result["runtime_python"].startswith(f"{spec.python[0]}.{spec.python[1]}.")
    assert (result["search_fit_rows"], result["purge_gap"], result["validation_rows"]) == (190, 2, 48)
    assert (result["train_rows"], result["test_rows"], result["models_requested"]) == (240, 48, 4)
    assert result["final_fit_rows"] == (238 if REFITS[engine] else 190)
    assert ("refit on fitting plus validation" in result["fit_scope"]) == REFITS[engine]
    assert result["candidates_evaluated"] >= 2
    assert 0 <= result["candidates_failed"] < result["candidates_evaluated"]
    assert 0 < result["engine_reported_validation_rmse"] < 1.5
    # y = 2*x1 - x2 + 1.5*x1*x3 + 0.5*sin(3*x2) + noise: a real learner beats the mean by a wide margin.
    assert result["test_rmse"] < 0.6 * result["fit_mean_reference_test_rmse"], result
    assert result["test_mae"] <= result["test_rmse"]
    assert not result["holdout_targets_sent_to_engine"] and not result["model_persisted"]
    assert not result["baseline_changed"]
    exported = json.dumps(first)
    assert "predictions" not in exported and str(frame["y"].iloc[250]) not in exported

    # Same request, same platform: identical predictions.
    assert run_workbench(request, frame)["result"]["prediction_sha256"] == result["prediction_sha256"]

    # Holdout targets and purged rows never reach the engine: changing them cannot change a prediction.
    changed = frame.copy()
    changed.loc[240:, "y"] = changed.loc[240:, "y"] + 100.0
    changed.loc[190:191, ["y", "x1", "x2", "x3"]] = 50.0
    other = run_workbench(request, changed)["result"]
    assert other["prediction_sha256"] == result["prediction_sha256"]
    assert other["test_rmse"] > 50 and other["selected_model"] == result["selected_model"]

    # A different fitting target does change it: the engine really learns from the rows it is given.
    relearned = frame.copy()
    relearned.loc[:189, "y"] = -relearned.loc[:189, "y"]
    assert run_workbench(request, relearned)["result"]["prediction_sha256"] != result["prediction_sha256"]
    assert _wait_no_engine_processes() == []


def test_h2o_reports_the_model_it_could_not_train_and_runs_no_refit():
    result = run_workbench(_request("automl-h2o.json"), _frame())["result"]
    # 190 fitting rows are below H2O's GBM minimum, so one started model fails and is counted.
    assert result["candidates_evaluated"] - result["candidates_failed"] == 4
    assert result["candidates_failed"] >= 1 and "no refit" in result["fit_scope"]
    assert "early stopping" in result["engine_reported_validation_note"]


def test_foundation_forecast_is_zero_shot_deterministic_and_blind_to_the_holdout():
    frame, request = _frame(), _request("chronos-bolt.json")
    result = run_workbench(request, frame)["result"]
    assert result["engine"] == "chronos" and result["engine_version"] == "2.3.2"
    assert result["model"] == "amazon/chronos-bolt-tiny" and result["model_parameters"] == 8_652_672
    assert result["model_sha256"] == {f.name: f.sha256 for f in assets.ASSETS["chronos-bolt-tiny"].files}
    assert result["pretrained_weights_used"] and not result["training_performed"]
    assert not result["runtime_downloads"] and not result["baseline_changed"]
    assert (result["context_rows"], result["horizon"], result["quantile_levels"]) == (240, 48, [0.1, 0.5, 0.9])
    # Daily and half-daily cycles plus trend: the pretrained model must beat persistence clearly.
    assert result["mae_ratio_to_last_value"] < 0.4 and result["median_mae"] < 1.5, result
    assert result["quantile_crossings"] == 0 and 24 <= result["observations_inside_10_90"] <= 48
    assert result["mean_pinball_loss"] < result["median_mae"]
    assert "no coverage guarantee" in result["scope"]

    assert run_workbench(request, frame)["result"]["forecast_sha256"] == result["forecast_sha256"]
    changed = frame.copy()
    changed.loc[240:, "load"] = 0.0
    other = run_workbench(request, changed)["result"]
    assert other["forecast_sha256"] == result["forecast_sha256"] and other["median_mae"] > 40
    history = frame.copy()
    history.loc[:239, "load"] = history.loc[:239, "load"].to_numpy()[::-1]
    assert run_workbench(request, history)["result"]["forecast_sha256"] != result["forecast_sha256"]
    assert _wait_no_engine_processes() == []


def test_pretrained_classifier_recognises_public_domain_photographs():
    request = _request("ghostnet.json")
    first = run_workbench(request)
    result = first["result"]
    assert result["engine"] == "torch" and result["model_parameters"] == 5_182_508
    assert result["model_sha256"] == {f.name: f.sha256 for f in assets.ASSETS["ghostnet-imagenet"].files}
    assert result["pretrained_weights_used"] and not result["training_performed"] and not result["pixels_exported"]
    cat, espresso = result["images"]
    assert (cat["width"], cat["height"]) == (451, 300) and (espresso["width"], espresso["height"]) == (600, 400)
    assert cat["top"][0]["label"] in ("Egyptian cat", "tabby", "tiger cat")
    assert {row["label"] for row in cat["top"]} == {"Egyptian cat", "tabby", "tiger cat"}
    assert sum(row["probability"] for row in cat["top"]) > 0.8
    assert espresso["top"][0]["label"] == "espresso" and espresso["top"][0]["class_index"] == 967
    assert espresso["top"][0]["probability"] > 0.7
    for image in result["images"]:
        scores = [row["probability"] for row in image["top"]]
        assert scores == sorted(scores, reverse=True) and 0 < sum(scores) <= 1 + 1e-6
    again = run_workbench(request)["result"]["images"]
    for before, after in zip(result["images"], again):
        assert [r["class_index"] for r in before["top"]] == [r["class_index"] for r in after["top"]]
        assert np.allclose([r["probability"] for r in before["top"]], [r["probability"] for r in after["top"]])


def test_altered_or_extended_model_assets_are_refused(provisioned_home, tmp_path, monkeypatch):
    home = tmp_path / "home"
    shutil.copytree(provisioned_home / "model-assets", home / "model-assets")
    monkeypatch.setenv("EB_HOME", str(home))
    request = _request("ghostnet.json")
    assert run_workbench(request)["result"]["images"][1]["top"][0]["label"] == "espresso"

    weights = home / "model-assets/ghostnet-imagenet/state_dict_73.98.pth"
    original = weights.read_bytes()
    flipped = bytearray(original)
    flipped[len(flipped) // 2] ^= 1
    weights.write_bytes(bytes(flipped))
    assert assets.asset_status("ghostnet-imagenet")["state"] == "invalid"
    with pytest.raises(isolated.ProvisioningRequired, match="assets install ghostnet-imagenet"):
        run_workbench(request)
    weights.write_bytes(original)
    assert assets.asset_status("ghostnet-imagenet")["state"] == "installed"

    # An unapproved extra file that a model loader could pick up also invalidates the asset.
    extra = home / "model-assets/chronos-bolt-tiny/generation_config.json"
    extra.write_text("{}", encoding="utf-8")
    assert assets.asset_status("chronos-bolt-tiny")["state"] == "invalid"
    with pytest.raises(isolated.ProvisioningRequired, match="assets install chronos-bolt-tiny"):
        run_workbench(_request("chronos-bolt.json"), _frame())
    extra.unlink()

    # Reinstalling from a local directory verifies and restores the approved bytes.
    assets.remove_asset("chronos-bolt-tiny")
    restored = assets.install_asset("chronos-bolt-tiny", provisioned_home / "model-assets/chronos-bolt-tiny")
    assert restored["state"] == "installed"


def test_budget_expiry_kills_the_engine_tree_and_removes_the_workspace():
    frame = _frame()
    x, y = frame[["x1", "x2", "x3"]].to_numpy(), frame["y"].to_numpy()
    arrays = {"x_fit": x[:190], "y_fit": y[:190], "x_val": x[192:240], "y_val": y[192:240], "x_test": x[240:]}
    engine = "h2o"  # A JVM child of the worker: the hardest tree to stop.
    started = time.monotonic()
    with pytest.raises(isolated.EngineFailure, match="wall-clock budget"):
        isolated.run_engine(engine, {"seed": 1, "models": 6}, arrays, timeout=4)
    assert time.monotonic() - started < 60
    assert _wait_no_engine_processes() == []
    from energybaseline.io.paths import resolve_temp_dir

    assert list(resolve_temp_dir("EnergyBaselineEngines").glob(isolated.RUN_PREFIX + "*")) == []


def test_killing_the_application_mid_run_leaves_no_engine_process(tmp_path):
    script = tmp_path / "caller.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import pandas as pd\n"
        "from energybaseline import isolated\n"
        f"frame = pd.read_csv({str(EXAMPLES / 'series.csv')!r})\n"
        "x, y = frame[['x1', 'x2', 'x3']].to_numpy(), frame['y'].to_numpy()\n"
        "isolated.run_engine('h2o', {'seed': 1, 'models': 6}, {'x_fit': x[:190], 'y_fit': y[:190],\n"
        "    'x_val': x[192:240], 'y_val': y[192:240], 'x_test': x[240:]}, timeout=300)\n",
        encoding="utf-8",
    )
    process = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.DEVNULL, env=dict(os.environ))
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not any("h2o.jar" in line for line in _engine_processes()):
            assert process.poll() is None, "The engine finished before it could be interrupted."
            time.sleep(0.25)
        assert any("h2o.jar" in line for line in _engine_processes())
    finally:
        process.kill()
        process.wait(timeout=30)
    assert _wait_no_engine_processes() == []
    if sys.platform != "win32":
        from energybaseline.io.paths import resolve_temp_dir

        deadline = time.monotonic() + 20
        root = resolve_temp_dir("EnergyBaselineEngines")
        while list(root.glob(isolated.RUN_PREFIX + "*")) and time.monotonic() < deadline:
            time.sleep(0.25)
        assert list(root.glob(isolated.RUN_PREFIX + "*")) == []


def test_command_line_writes_the_same_privacy_preserving_contract(tmp_path):
    out = tmp_path / "result.json"
    completed = subprocess.run(
        [sys.executable, "-m", "energybaseline.cli", "workbench", str(EXAMPLES / "automl-autogluon.json"),
         "--dataset", str(EXAMPLES / "series.csv"), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert completed.returncode == 0, completed.stderr
    output = json.loads(out.read_text(encoding="utf-8"))
    direct = run_workbench(_request("automl-autogluon.json"), _frame())
    assert output["result"]["prediction_sha256"] == direct["result"]["prediction_sha256"]
    assert output["spec_sha256"] == direct["spec_sha256"] and output["input_sha256"] == direct["input_sha256"]
    text = out.read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "EnergyBaselineEngines" not in text and "site-packages" not in text
    status = subprocess.run(
        [sys.executable, "-m", "energybaseline.cli", "engines", "status", "autogluon"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert json.loads(status.stdout)["engines"][0]["state"] == "installed"


def test_autosklearn_reports_its_mean_predictor_when_no_configuration_beats_it():
    if "autosklearn" not in AUTOML:
        # auto-sklearn has no Windows release; the platform contract is asserted instead of a run.
        assert isolated.engine_status("autosklearn")["state"] == "unsupported-platform"
        with pytest.raises(isolated.EngineUnavailable, match="unsupported-platform"):
            run_workbench(_request("automl-autosklearn.json"), _frame())
        return
    frame = _frame()
    frame.loc[:189, "y"] = -frame.loc[:189, "y"]
    result = run_workbench(_request("automl-autosklearn.json"), frame)["result"]
    # Fitting and validation targets contradict each other, so every searched model loses to the mean.
    assert result["selected_model"] == "dummy_mean" and result["candidates_failed"] == 0
    assert result["engine_reported_validation_rmse"] > 2 and result["final_fit_rows"] == 238
