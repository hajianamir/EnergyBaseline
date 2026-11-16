"""Execute five real Stage 6 CLI/native jobs on fixed public synthetic fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def run(executable=None):
    prefix = [str(Path(executable).resolve()), "cli"] if executable else [sys.executable, "-m", "energybaseline.cli"]
    examples = ROOT / "examples/catalog"
    records = []
    with tempfile.TemporaryDirectory(prefix="eb-catalog-") as directory:
        for example in json.loads((examples / "manifest.json").read_text()):
            request = examples / example["request"]
            spec = json.loads(request.read_text())
            output = Path(directory) / example["request"]
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    prefix
                    + [
                        "workbench",
                        str(request),
                        "--dataset",
                        str(examples / example["dataset"]),
                        "--out",
                        str(output),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    "Actual catalog fixture subprocess exceeded its 120-second execution budget."
                ) from None
            if completed.returncode != 0 or not output.is_file():
                # Inputs are exclusively public repository fixtures; never run this oracle on user data.
                message = example["request"] + ": actual catalog execution failed\n" + completed.stderr[-6000:]
                print(
                    "::error title=CatalogExecutionFailure::"
                    + message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"),
                    flush=True,
                )
                raise RuntimeError("Actual catalog execution failed; no successful receipt produced.")
            result = json.loads(output.read_text())
            payload = result["result"]
            if completed.stdout.strip() and json.loads(completed.stdout) != result:
                raise RuntimeError("CLI standard output and authoritative output file disagree.")
            if (
                result["operation"] != spec["operation"]
                or payload["baseline_changed"]
                or payload["p_values_or_confidence_intervals_computed"]
            ):
                raise RuntimeError("Catalog operation/baseline/inference contract mismatch.")
            operation = spec["operation"]
            if operation == "text-classification":
                passed = payload["accuracy"] == 1.0 and payload["class_count"] == 3
            elif operation == "text-topics":
                passed = payload["topic_count"] == 3 and payload["test_reconstruction_frobenius"] < 0.01
            elif operation == "graph-node-classification":
                passed = (
                    payload["test_accuracy"] == 1.0
                    and payload["final_train_cross_entropy"] < payload["initial_train_cross_entropy"] / 10
                )
            else:
                passed = payload["optimizer_converged"] and payload["holdout_harrell_concordance"] > 0.7
            if not passed:
                raise RuntimeError("Actual catalog semantic oracle failed.")
            if any(word in json.dumps(result) for word in ("orchard", "PRIVATE", "class-0")):
                raise RuntimeError("Catalog plaintext privacy oracle failed.")
            records.append(
                {
                    "request": example["request"],
                    "operation": operation,
                    "subprocess_exit_code": completed.returncode,
                    "elapsed_seconds": time.monotonic() - started,
                    "stdout_emitted": bool(completed.stdout.strip()),
                    "request_sha256": hashlib.sha256(request.read_bytes()).hexdigest(),
                    "output": result,
                }
            )
    return {
        "schema_version": 1,
        "platform": sys.platform,
        "runtime_kind": "frozen_executable" if executable else "python_cli",
        "executable_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest() if executable else None,
        "mocked": False,
        "synthetic_input_only": True,
        "jobs": records,
        "passed": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    receipt = run(args.executable)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
