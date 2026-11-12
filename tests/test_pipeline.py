import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SAMPLES = Path(__file__).resolve().parents[1] / "samples"
FAST = {"hpo_trials": "2", "nn_max_epochs": "15"}


@pytest.fixture(scope="module")
def db(eb_home):
    import energybaseline.db.session as ses
    ses.reset_engine()
    ses.init_database()
    return ses


@pytest.fixture(scope="module")
def monthly(db):
    from energybaseline.core.ingest import guess_time_column, load_file
    from energybaseline.core.pipeline import run_analysis
    df = load_file(str(SAMPLES / "monthly_building.csv"))
    tc = guess_time_column(df)
    run, bundles = run_analysis(df, tc, ["Energy_kWh"], dict(FAST), dataset_sha="x", dataset_info={"name": "monthly", "rows": len(df), "cols": df.shape[1]})
    return df, tc, run, bundles


def test_monthly_run_shortlist_and_formulas(monthly):
    _, _, run, _ = monthly
    tr = run.targets["Energy_kWh"]
    assert tr.status == "ok", tr.error
    assert 1 <= len(tr.shortlist) <= 5
    for s in tr.shortlist:
        assert s["formula"]["text"] and "ŷ" in s["formula"]["text"] or "NN" in s["formula"]["text"]
        assert set(["r2", "cvrmse", "nmbe", "mae", "mape", "smape"]) <= set(s["metrics"])
    assert tr.thresholds["max_cvrmse"] == 15.0 and tr.thresholds["max_nmbe"] == 5.0  # ASHRAE monthly
    names = {r["model"] for r in tr.register}
    assert any(r["status"] == "rejected" for r in tr.register) or len(tr.leaderboard) == len(tr.shortlist)
    assert all(a["rule"] and a["setting"] for a in run.auto)
    assert any(a["setting"] == "weight_profile" and a["value"] == "interpretable" for a in run.auto)  # n=36 < 200


def test_nn_explicitly_rejected_for_small_data(monthly):
    tr = monthly[2].targets["Energy_kWh"]
    nn = [r for r in tr.register if "MLP" in r["model"] or "LSTM" in r["model"]]
    assert nn and all("insufficient" in r["reason"] for r in nn)


def test_determinism(monthly, db):
    from energybaseline.core.pipeline import run_analysis
    df, tc, run1, _ = monthly
    run2, _ = run_analysis(df, tc, ["Energy_kWh"], dict(FAST), dataset_sha="x")
    a, b = run1.targets["Energy_kWh"], run2.targets["Energy_kWh"]
    assert run1.seed == run2.seed
    assert [s["name"] for s in a.shortlist] == [s["name"] for s in b.shortlist]
    assert a.shortlist[0]["metrics"]["cvrmse"] == pytest.approx(b.shortlist[0]["metrics"]["cvrmse"], rel=1e-9)
    assert [(x["setting"], x["value"]) for x in run1.auto] == [(x["setting"], x["value"]) for x in run2.auto]


def test_store_lock_monitor_and_report(monthly, db, tmp_path):
    from energybaseline.core import monitor, store
    from energybaseline.core.report import export_dashboard_excel, export_dashboard_pdf, generate_all
    df, tc, run, _ = monthly
    # re-run through the store to create artifacts
    from energybaseline.core.pipeline import run_analysis
    rid = store.new_run(None, ["Energy_kWh"], dict(FAST))
    run, bundles = run_analysis(df, tc, ["Energy_kWh"], dict(FAST), dataset_sha="x", dataset_info={"name": "monthly", "rows": len(df), "cols": df.shape[1]},
                                artifact_fn=lambda t, m: str(store.artifact_path(rid, t, m)))
    store.save_run(rid, run, bundles)
    tr = run.targets["Energy_kWh"]
    first, second = tr.shortlist[0]["name"], tr.shortlist[-1]["name"]
    assert store.active_selection(rid, "Energy_kWh") is None  # nothing locked yet
    store.lock_baseline(rid, "Energy_kWh", first, "initial")
    store.lock_baseline(rid, "Energy_kWh", second, "changed my mind")
    hist = store.selection_history(rid)
    assert len(hist) == 2 and [h["active"] for h in hist] == [False, True]
    assert store.active_selection(rid, "Energy_kWh")["model"] == second
    b = store.load_bundle(rid, "Energy_kWh", second)

    rep = monitor.schema_report(df.drop(columns=["Avg_Temp_C"]), b, tc)
    assert not rep["ok"] and "Avg_Temp_C" in rep["missing_columns"]
    res = monitor.predict_deviation(df, b, tc)
    assert {"baseline", "actual", "deviation", "deviation_pct", "cum_deviation", "normalized_deviation", "lower", "upper", "exceedance"} <= set(res.columns)
    fit = tr.series["baseline_fit"] if second == tr.shortlist[0]["name"] else None
    assert np.allclose(res["deviation"], res["actual"] - res["baseline"], equal_nan=True)
    k = monitor.kpis(res)
    assert k["rows"] == len(df) and "max_deviation_at" in k
    # an injected +30 % efficiency loss must raise exceedances
    bad = df.copy(); bad["Energy_kWh"] *= 1.3
    kb = monitor.kpis(monitor.predict_deviation(bad, b, tc))
    assert kb["exceedances"] > k["exceedances"] and kb["total_deviation"] > 0
    assert monitor.manual_template(b)[-1]["name"] == "Energy_kWh"

    store.save_snapshot(rid, "Energy_kWh", "t", res, k)
    assert len(store.list_snapshots()) == 1
    assert len(store.load_snapshot(store.list_snapshots()[0]["id"])) == len(res)

    out = generate_all(run, store.selection_history(rid), store.audit_entries(rid), outdir=tmp_path)
    for key in ("pdf", "xlsx", "html"):
        assert Path(out[key]).exists() and Path(out[key]).stat().st_size > 2000
    html = Path(out["html"]).read_text(encoding="utf-8")
    for section in ("Executive Summary", "Top-5 Shortlist", "Rejected Models Register", "AUTO Resolutions Log", "User Selections Log", "Local Storage Confirmation", "changed my mind"):
        assert section in html
    sheets = pd.ExcelFile(out["xlsx"]).sheet_names
    assert "AUTO_Resolutions" in sheets and any(s.startswith("Top5") for s in sheets)
    export_dashboard_pdf({"Energy_kWh": res}, {"Energy_kWh": k}, str(tmp_path / "d.pdf"))
    export_dashboard_excel({"Energy_kWh": res}, {"Energy_kWh": k}, str(tmp_path / "d.xlsx"))
    assert (tmp_path / "d.pdf").stat().st_size > 1000


def test_daily_multitarget_independent(db):
    from energybaseline.core.ingest import guess_time_column, load_file
    from energybaseline.core.pipeline import run_analysis
    df = load_file(str(SAMPLES / "daily_plant_multitarget.xlsx")).iloc[:300]
    tc = guess_time_column(df)
    run, _ = run_analysis(df, tc, ["Electricity_kWh", "Gas_m3"], {"hpo_trials": "0", "nn_max_epochs": "10", "enable_mining": "false"})
    e, g = run.targets["Electricity_kWh"], run.targets["Gas_m3"]
    assert e.status == g.status == "ok"
    # leakage rule per target: the other target is never a predictor
    assert "Gas_m3" not in e.required_columns and "Electricity_kWh" not in g.required_columns
    assert any(x["item"] == "Gas_m3" and x["kind"] == "leakage" for x in e.exclusions)
    assert e.shortlist[0]["formula"]["text"] != g.shortlist[0]["formula"]["text"]
    assert any(a["target"] == "Gas_m3" for a in run.auto) and any(a["target"] == "Electricity_kWh" for a in run.auto)
