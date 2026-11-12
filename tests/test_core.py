import numpy as np
import pandas as pd
import pytest

from energybaseline.core.auto import AutoLog
from energybaseline.core.temporal import TemporalError, detect_sampling, prepare_temporal
from energybaseline.core.secrets import decrypt_text, encrypt_text
from energybaseline.core import sqlsource as sq
from energybaseline.core.sqlsource import SourceError, validate_select, ConnectionProfile, run_query


def _hourly(n=24 * 30):
    idx = pd.date_range("2023-01-01", periods=n, freq="h")
    return pd.DataFrame({"ts": idx, "Energy_kWh": np.arange(n, dtype=float), "T": np.sin(np.arange(n))})


def test_detects_hourly():
    df = _hourly()
    assert detect_sampling(pd.DatetimeIndex(df["ts"]))["label"] == "hourly"


def test_aggregates_when_finer_than_declared():
    out, rep, g = prepare_temporal(_hourly(), "ts", AutoLog({"sampling_period": "daily"}))
    assert g.label == "daily" and len(out) == 30
    assert out["Energy_kWh"].iloc[0] == sum(range(24))  # energy-like column is summed
    assert rep.aggregation["Energy_kWh"] == "sum" and rep.aggregation["T"] == "mean"


def test_refuses_to_disaggregate():
    daily = _hourly().iloc[::24]
    with pytest.raises(TemporalError, match="disaggregate"):
        prepare_temporal(daily, "ts", AutoLog({"sampling_period": "hourly"}))


def test_gaps_and_duplicates_flagged():
    df = _hourly(100)
    df = pd.concat([df.drop(index=range(10, 20)), df.iloc[[5]]])
    out, rep, _ = prepare_temporal(df, "ts", AutoLog({}))
    assert rep.n_duplicates == 1 and rep.n_gaps_filled_as_missing == 10 and rep.needs_confirmation


def test_auto_decisions_are_logged_and_overrides_recorded():
    a = AutoLog({"timezone": "AUTO", "baseline_window": "2023-01-02,2023-01-10"})
    prepare_temporal(_hourly(), "ts", a)
    modes = {r.setting: r.mode for r in a.records}
    assert modes["sampling_period"] == "AUTO" and modes["baseline_window"] == "USER_OVERRIDE"
    assert all(r.rule for r in a.records)


def test_secret_roundtrip(eb_home):
    tok = encrypt_text("p@ss word")
    assert "p@ss" not in tok and decrypt_text(tok) == "p@ss word"


def test_select_only():
    assert validate_select("SELECT 1;") == "SELECT 1"
    for bad in ("DROP TABLE x", "select 1; delete from t", "update t set a=1", "select * into x from y"):
        with pytest.raises(SourceError):
            validate_select(bad)


def test_sqlite_source_and_offline_handling(tmp_path, eb_home):
    import sqlite3
    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    con.execute("create table m(a real, b real)")
    con.executemany("insert into m values(?,?)", [(1, 2), (3, 4)])
    con.commit(); con.close()
    p = ConnectionProfile("t", "SQLite", database=str(db))
    assert sq.test_connection(p)[0]
    assert len(run_query(p, "SELECT a,b FROM m")) == 2
    bad = ConnectionProfile("off", "PostgreSQL", host="127.0.0.1", port=1, database="x", username="u", password="p")
    ok, msg = sq.test_connection(bad)
    assert not ok and ("OFFLINE" in msg or "not installed" in msg)
    with pytest.raises(SourceError):
        run_query(bad, "SELECT 1")
