"""Execute actual independent CLI jobs, optionally through the frozen native executable.

Only repository synthetic fixtures are used. Plaintext OCR results are never exported.
Exit nonzero on any engine/runtime/JSON/metric mismatch; no mocked or skipped backends.
"""

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
    examples = ROOT / "examples/external"
    records = []
    with tempfile.TemporaryDirectory(prefix="eb-external-engines-") as directory:
        for example in json.loads((examples / "manifest.json").read_text(encoding="utf-8")):
            request = examples / example["request"]
            operation = json.loads(request.read_text(encoding="utf-8"))["operation"]
            out = Path(directory) / (operation + ".json")
            command = prefix + ["workbench", str(request), "--out", str(out)]
            if example["dataset"]:
                command += ["--dataset", str(examples / example["dataset"])]
            started = time.monotonic()
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
            if completed.returncode != 0 or not out.is_file():
                raise RuntimeError("Actual external engine subprocess failed; no successful receipt produced.")
            output = json.loads(out.read_text(encoding="utf-8"))
            result = output["result"]
            if completed.stdout.strip() and json.loads(completed.stdout) != output:
                raise RuntimeError("External CLI stdout and output-file results disagree.")
            if output["operation"] != operation or result.get("baseline_changed") is not False:
                raise RuntimeError("External result operation/baseline contract mismatch.")
            if operation == "flaml-search":
                assert result["engine"] == "flaml" and result["iterations_executed"] == 4
                assert result["test_rmse"] < 2
            elif operation == "pandera-validation":
                assert result["engine"] == "pandera" and result["passed"]
            elif operation == "pretrained-ocr":
                assert result["pretrained_weights_used"] and not result["recognized_plaintext_exported"]
                assert result["images"][0]["line_count"] == 1
                assert result["images"][0]["lines"][0]["character_count"] == 15
                assert "HELLO" not in json.dumps(output)
            records.append(
                {
                    "operation": operation,
                    "subprocess_exit_code": completed.returncode,
                    "elapsed_seconds": time.monotonic() - started,
                    "stdout_emitted": bool(completed.stdout.strip()),
                    "stdout_equals_output_when_emitted": True,
                    "output": output,
                }
            )
    return {
        "schema_version": 1,
        "platform": sys.platform,
        "runtime_kind": "frozen_executable" if executable else "python_cli",
        "executable_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest() if executable else None,
        "models_are_repository_fixtures": False,
        "input_data_scope": "Repository synthetic fixtures only; real third-party pretrained OCR weights from locked wheel",
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
    print("Actual external and pretrained engine executions passed; receipt written.")


if __name__ == "__main__":
    main()
