"""Print engine failures for the repository's synthetic example only.

Product results never export third-party diagnostics, because engine logs can echo data
values. This support script is for the continuous-integration workflow after a failure:
it runs each provisioned AutoML engine once on ``examples/isolated/series.csv`` (synthetic,
public) with ``EB_ENGINE_DIAGNOSTICS`` set and prints the exception and the retained log.
It always exits 0; it explains a failure and is never a substitute for the tests.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import pandas as pd

    from energybaseline import isolated
    from energybaseline.workbench import pretrained

    frame = pd.read_csv(ROOT / "examples" / "isolated" / "series.csv")
    frame["time"] = pd.to_datetime(frame["time"])
    x, y = frame[["x1", "x2", "x3"]].to_numpy(), frame["y"].to_numpy()
    arrays = {"x_fit": x[:190], "y_fit": y[:190], "x_val": x[192:240], "y_val": y[192:240], "x_test": x[240:]}
    with tempfile.TemporaryDirectory() as kept:
        os.environ["EB_ENGINE_DIAGNOSTICS"] = kept
        for engine in sys.argv[1:] or ["tpot", "h2o", "autogluon", "autosklearn", "chronos"]:
            state = isolated.engine_status(engine)["state"]
            print(f"===== {engine}: runtime {state}", flush=True)
            if state != "installed":
                continue
            started = time.monotonic()
            try:
                if engine == "chronos":
                    # The operation itself, called below the layer that masks error details.
                    result = pretrained.foundation_forecast(
                        frame, {"time": "time", "target": "load", "train_rows": 240}
                    )
                else:
                    result = isolated.run_engine(engine, {"seed": 7, "models": 4}, arrays, timeout=180)
            except Exception:
                traceback.print_exc(file=sys.stdout)
            else:
                summary = {k: v for k, v in result.items() if k != "predictions"}
                print(f"{engine} succeeded: {summary}")
            print(f"{engine} elapsed {time.monotonic() - started:.1f} s", flush=True)
            log = Path(kept, f"{engine}-engine.log")
            if log.is_file():
                print(f"----- retained {engine} log (last 30000 characters)")
                print(log.read_text(encoding="utf-8", errors="replace")[-30000:], flush=True)
                log.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
