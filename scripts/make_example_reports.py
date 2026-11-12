"""Run the pipeline on the sample data and write the example PDF / Excel / HTML reports into examples/."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["EB_HOME"] = tempfile.mkdtemp(prefix="eb_example_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from energybaseline.core import ingest, store  # noqa: E402
from energybaseline.core.pipeline import run_analysis  # noqa: E402
from energybaseline.core.report import generate_all  # noqa: E402
from energybaseline.db.session import init_database  # noqa: E402


def main():
    init_database()
    path = ROOT / "samples" / "daily_plant_multitarget.xlsx"
    df = ingest.load_file(str(path))
    tc = ingest.guess_time_column(df)
    targets = ["Electricity_kWh", "Gas_m3"]
    sha = ingest.sha256_file(path)
    did = store.register_dataset(path.name, str(path), sha, df)
    cfg = {"hpo_trials": "3", "nn_max_epochs": "40"}
    rid = store.new_run(did, targets, cfg)
    run, bundles = run_analysis(df, tc, targets, cfg, dataset_sha=sha, progress=lambda t, p, m: print(f"[{t}] {p:.0f}% {m}"),
                                dataset_info={"name": path.name, "rows": len(df), "cols": df.shape[1], "description": "Synthetic two-year daily plant dataset with two targets (electricity, gas)."},
                                artifact_fn=lambda t, m: str(store.artifact_path(rid, t, m)))
    store.save_run(rid, run, bundles)
    for t, tr in run.targets.items():  # example user selection: rank-1 model of each target
        if tr.shortlist:
            store.lock_baseline(rid, t, tr.shortlist[0]["name"], "example selection (rank 1)")
    out = generate_all(run, store.selection_history(rid), store.audit_entries(rid), ingest.data_dictionary(df, tc), outdir=ROOT / "examples")
    print(out)


if __name__ == "__main__":
    main()
