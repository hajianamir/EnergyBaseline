"""Execute every isolated engine and pretrained model through the real command line.

The application home named by ``EB_HOME`` must already hold the provisioned engine runtimes
and model assets; this script installs nothing. Jobs use only repository fixtures. With
``--executable`` the same jobs run through the frozen native executable. Any missing
runtime, contract mismatch or semantic-oracle failure exits nonzero; nothing is skipped
except engines whose upstream project does not support the current platform, and that
platform contract is itself asserted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples/isolated"
PLATFORM = "win32" if sys.platform == "win32" else "linux"
REFITS = {"tpot": True, "h2o": False, "autogluon": True, "autosklearn": True}


def _call(prefix, arguments, out, *, expect=0, timeout=600):
    command = prefix + arguments + ["--out", str(out)]
    started = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != expect:
        detail = completed.stderr[-3000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=IsolatedEngineFailure::{' '.join(arguments[:3])}: exit {completed.returncode}%0A{detail}")
        raise RuntimeError(f"Command {arguments[:3]} exited {completed.returncode}, expected {expect}.")
    return completed, time.monotonic() - started


def _check_automl(result, engine, registry):
    assert result["engine"] == engine and result["engine_version"] == registry[engine]["version"]
    assert result["lock_sha256"] == registry[engine]["lock_sha256"]
    assert (result["search_fit_rows"], result["purge_gap"], result["validation_rows"]) == (190, 2, 48)
    assert result["final_fit_rows"] == (238 if REFITS[engine] else 190)
    assert 0 <= result["candidates_failed"] < result["candidates_evaluated"]
    assert result["test_rmse"] < 0.6 * result["fit_mean_reference_test_rmse"]
    assert result["holdout_targets_sent_to_engine"] is False and result["model_persisted"] is False


def _check_forecast(result, registry):
    assert result["engine"] == "chronos" and result["engine_version"] == registry["chronos"]["version"]
    assert result["model_parameters"] == 8_652_672 and result["pretrained_weights_used"] is True
    assert result["training_performed"] is False and result["runtime_downloads"] is False
    assert result["mae_ratio_to_last_value"] < 0.4 and result["quantile_crossings"] == 0
    assert 24 <= result["observations_inside_10_90"] <= 48


def _check_vision(result):
    assert result["model_parameters"] == 5_182_508 and result["pretrained_weights_used"] is True
    assert result["pixels_exported"] is False and result["training_performed"] is False
    cat, espresso = result["images"]
    assert cat["top"][0]["label"] in ("Egyptian cat", "tabby", "tiger cat")
    assert espresso["top"][0]["label"] == "espresso" and espresso["top"][0]["probability"] > 0.7


def run(executable=None):
    if not os.environ.get("EB_HOME"):
        raise RuntimeError("EB_HOME must name the provisioned application home.")
    prefix = [str(Path(executable).resolve()), "cli"] if executable else [sys.executable, "-m", "energybaseline.cli"]
    records = []
    with tempfile.TemporaryDirectory(prefix="eb-isolated-evidence-") as directory:
        directory = Path(directory)
        _call(prefix, ["engines", "status"], directory / "engines.json")
        registry = {
            row["engine"]: row for row in json.loads((directory / "engines.json").read_text("utf-8"))["engines"]
        }
        _call(prefix, ["assets", "status"], directory / "assets.json")
        asset_rows = {row["asset"]: row for row in json.loads((directory / "assets.json").read_text("utf-8"))["assets"]}
        for name, row in registry.items():
            expected = "installed" if PLATFORM in row["platforms"] else "unsupported-platform"
            if row["state"] != expected:
                raise RuntimeError(f"Engine {name} is {row['state']}; expected {expected}.")
        if any(row["state"] != "installed" for row in asset_rows.values()):
            raise RuntimeError("A model asset is not installed and verified.")
        for job in json.loads((EXAMPLES / "manifest.json").read_text(encoding="utf-8")):
            request = EXAMPLES / job["request"]
            operation = json.loads(request.read_text(encoding="utf-8"))["operation"]
            out = directory / (request.stem + ".json")
            arguments = ["workbench", str(request)]
            if job.get("dataset"):
                arguments += ["--dataset", str(EXAMPLES / job["dataset"])]
            engine = job.get("engine")
            if engine and PLATFORM not in registry[engine]["platforms"]:
                # Upstream has no release for this platform: the refusal is the contract to verify.
                completed, elapsed = _call(prefix, arguments, out, expect=2)
                assert not out.exists()
                if completed.stderr.strip():
                    assert "unsupported-platform" in completed.stderr
                records.append(
                    {"request": job["request"], "engine": engine, "outcome": "refused: unsupported platform", "exit": 2}
                )
                continue
            outputs = []
            for _ in range(2):
                out.unlink(missing_ok=True)
                completed, elapsed = _call(prefix, arguments, out)
                outputs.append(json.loads(out.read_text(encoding="utf-8")))
                if completed.stdout.strip() and json.loads(completed.stdout) != outputs[-1]:
                    raise RuntimeError("Command-line stdout and output file disagree.")
            output, result = outputs[0], outputs[0]["result"]
            assert output["operation"] == operation and result["baseline_changed"] is False
            if operation == "external-automl":
                _check_automl(result, engine, registry)
                assert outputs[1]["result"]["prediction_sha256"] == result["prediction_sha256"]
            elif operation == "foundation-forecast":
                _check_forecast(result, registry)
                assert outputs[1]["result"]["forecast_sha256"] == result["forecast_sha256"]
            else:
                _check_vision(result)
                assert [i["top"][0]["class_index"] for i in outputs[1]["result"]["images"]] == [
                    i["top"][0]["class_index"] for i in result["images"]
                ]
            text = json.dumps(output)
            assert "EnergyBaselineEngines" not in text and "site-packages" not in text
            records.append(
                {
                    "request": job["request"],
                    "engine": engine,
                    "asset": job.get("asset"),
                    "outcome": "executed twice with identical results",
                    "exit": 0,
                    "last_elapsed_seconds": elapsed,
                    "output": output,
                }
            )
    return {
        "schema_version": 1,
        "platform": sys.platform,
        "runtime_kind": "frozen_executable" if executable else "python_cli",
        "executable_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest() if executable else None,
        "engines": registry,
        "assets": asset_rows,
        "input_data_scope": "Repository synthetic series and two public-domain photographs only.",
        "mocked": False,
        "jobs": records,
        "passed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    receipt = run(args.executable)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    executed = sum(job["exit"] == 0 for job in receipt["jobs"])
    print(f"Isolated engine evidence passed: {executed} jobs executed, {len(receipt['jobs']) - executed} refused.")


if __name__ == "__main__":
    main()
