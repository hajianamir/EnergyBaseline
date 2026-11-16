"""Direct diagnostics exclusively for fixed public repository catalog fixtures.

Never accepts caller paths or user data; detailed exceptions must not escape the
production workbench. This workflow evidence does not replace normal windowed CI.
"""

from __future__ import annotations

import json
from pathlib import Path
import traceback
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from energybaseline.workbench import OPERATIONS  # noqa: E402


def run():
    records = []
    examples = ROOT / "examples/catalog"
    for fixture in json.loads((examples / "manifest.json").read_text()):
        request = json.loads((examples / fixture["request"]).read_text())
        frame = pd.read_csv(examples / fixture["dataset"])
        try:
            result = OPERATIONS[request["operation"]](frame, request["parameters"])
            records.append({"fixture": fixture["request"], "success": True, "result": result})
        except Exception as exc:
            detail = traceback.format_exc() + "\nInner diagnostic: " + repr(exc.__context__)
            records.append({"fixture": fixture["request"], "success": False, "traceback": detail})
            message = fixture["request"] + "\n" + detail[-8000:]
            print(
                "::error title=CatalogSyntheticDirectDiagnostic::"
                + message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"),
                flush=True,
            )
    out = ROOT / "artifacts/capabilities/catalog-stage6/ci-direct-diagnostics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"synthetic_fixed_repository_fixtures_only": True, "mocked": False, "records": records},
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    return int(not all(row["success"] for row in records))


if __name__ == "__main__":
    raise SystemExit(run())
