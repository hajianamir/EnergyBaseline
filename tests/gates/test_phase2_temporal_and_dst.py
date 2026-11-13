"""Phase 2 gate tests: Temporal Aggregation Dispatch, No-Disaggregation, and 4-Zone DST Engine (TMP-1..TMP-9, SEC-5, E1)."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hypothesis import HealthCheck, given, settings, strategies as st
import numpy as np
import pandas as pd
import pytest

from energybaseline.core.auto import AutoLog
from energybaseline.core.schema import ColumnRole, ColumnSpec, DatasetSchema, QuantityType
from energybaseline.core.temporal import (
    _AGGREGATION_DISPATCH,
    DataRefusedError,
    TemporalError,
    classify_gaps,
    normalize_timestamps_utc,
    prepare_temporal,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_DST_ZONES = ("Europe/Berlin", "America/New_York", "Australia/Lord_Howe", "UTC")


def test_aggregation_dispatch_is_immutable_and_getattr_free():
    # MappingProxyType must reject runtime mutation
    with pytest.raises(TypeError):
        _AGGREGATION_DISPATCH["injected"] = lambda rs: rs.sum()  # type: ignore[index]

    expected_keys = {"sum", "mean", "median", "min", "max", "first", "last", "time_weighted"}
    assert expected_keys.issubset(set(_AGGREGATION_DISPATCH.keys()))

    # AST check: temporal.py must not invoke getattr(...) for method dispatch
    tree = ast.parse((REPO_ROOT / "energybaseline" / "core" / "temporal.py").read_text(encoding="utf-8"))
    getattr_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
    ]
    assert getattr_calls == [], f"Forbidden dynamic getattr calls found in temporal.py: {getattr_calls}"


def test_quantity_type_aware_aggregation_math():
    # Four 15-minute intervals in 1 hour + four in the next hour
    idx = pd.date_range("2024-05-01 00:00:00", periods=8, freq="15min")
    df = pd.DataFrame(
        {
            "ts": idx,
            "elec_kwh_flow": [2.5, 2.5, 2.5, 2.5, 5.0, 5.0, 5.0, 5.0],
            "tank_level_stock": [50.0, 52.0, 55.0, 60.0, 58.0, 54.0, 51.0, 49.0],
            "power_kw_rate": [10.0, 10.0, 20.0, 20.0, 30.0, 30.0, 30.0, 30.0],
            "temp_c_quality": [18.0, 19.0, 20.0, 23.0, 24.0, 24.0, 22.0, 22.0],
            "cpi_index": [101.1, 101.2, 101.3, 101.5, 101.6, 101.7, 101.8, 102.0],
        }
    )
    schema = DatasetSchema(
        columns=[
            ColumnSpec(name="ts", role=ColumnRole.TIME),
            ColumnSpec(name="elec_kwh_flow", role=ColumnRole.TARGET, quantity_type=QuantityType.FLOW),
            ColumnSpec(name="tank_level_stock", role=ColumnRole.PREDICTOR, quantity_type=QuantityType.STOCK),
            ColumnSpec(name="power_kw_rate", role=ColumnRole.PREDICTOR, quantity_type=QuantityType.RATE),
            ColumnSpec(name="temp_c_quality", role=ColumnRole.PREDICTOR, quantity_type=QuantityType.QUALITY),
            ColumnSpec(name="cpi_index", role=ColumnRole.STATIC_FACTOR, quantity_type=QuantityType.INDEX),
        ]
    )
    auto = AutoLog({"sampling_period": "hourly"})
    out, rep, gran = prepare_temporal(df, "ts", auto, schema=schema)

    assert gran.label == "hourly"
    assert len(out) == 2
    # FLOW -> sum (4 * 2.5 = 10.0, 4 * 5.0 = 20.0)
    assert np.allclose(out["elec_kwh_flow"].values, [10.0, 20.0])
    # STOCK -> last ([60.0, 49.0])
    assert np.allclose(out["tank_level_stock"].values, [60.0, 49.0])
    # RATE -> time_weighted ([15.0, 30.0])
    assert np.allclose(out["power_kw_rate"].values, [15.0, 30.0])
    # QUALITY -> mean ([20.0, 23.0])
    assert np.allclose(out["temp_c_quality"].values, [20.0, 23.0])
    # INDEX -> mean ([101.275, 101.775])
    assert np.allclose(out["cpi_index"].values, [101.275, 101.775])
    assert rep.aggregation["elec_kwh_flow"] == "sum"
    assert rep.aggregation["tank_level_stock"] == "last"
    assert rep.aggregation["power_kw_rate"] == "time_weighted"


def test_time_weighted_irregular_subinterval_integral():
    # Hour 00:00..01:00 has 10 kW for 15 min (900s) and 50 kW for 45 min (2700s) -> weighted mean = 40.0 kW
    df = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2024-05-01 00:00:00",
                    "2024-05-01 00:15:00",
                    "2024-05-01 01:00:00",
                    "2024-05-01 01:30:00",
                    "2024-05-01 02:00:00",
                ]
            ),
            "demand_kw": [10.0, 50.0, 20.0, 20.0, 20.0],
        }
    )
    schema = DatasetSchema(
        columns=[
            ColumnSpec(name="ts", role="time"),
            ColumnSpec(name="demand_kw", role="target", quantity_type="rate"),
        ]
    )
    out, _, _ = prepare_temporal(df, "ts", AutoLog({"sampling_period": "hourly"}), schema=schema)
    assert abs(float(out.loc["2024-05-01 00:00:00", "demand_kw"]) - 40.0) < 1e-9


def test_disaggregation_refused_with_data_refused_error():
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=30, freq="D"),
            "elec_kwh": np.linspace(100.0, 130.0, 30),
        }
    )
    with pytest.raises(DataRefusedError) as exc_info:
        prepare_temporal(df, "ts", AutoLog({"sampling_period": "hourly"}))
    assert isinstance(exc_info.value, TemporalError)
    assert exc_info.value.code == "DATA_REFUSED"


@pytest.mark.parametrize(
    ("tz_name", "utc_start", "n_hours", "expected_day_hours"),
    [
        # Europe/Berlin spring-forward: 2024-03-31 has 23 local hours
        ("Europe/Berlin", "2024-03-30T00:00:00Z", 72, {"2024-03-31": 23}),
        # Europe/Berlin fall-back: 2024-10-27 has 25 local hours
        ("Europe/Berlin", "2024-10-26T00:00:00Z", 72, {"2024-10-27": 25}),
        # America/New_York spring-forward: 2024-03-10 has 23 local hours
        ("America/New_York", "2024-03-09T05:00:00Z", 72, {"2024-03-10": 23}),
        # America/New_York fall-back: 2024-11-03 has 25 local hours
        ("America/New_York", "2024-11-02T04:00:00Z", 72, {"2024-11-03": 25}),
        # Australia/Lord_Howe 30-min DST transitions (half-hour shift!)
        ("Australia/Lord_Howe", "2024-04-06T00:00:00Z", 72, {}),
        ("Australia/Lord_Howe", "2024-10-05T00:00:00Z", 72, {}),
        # UTC control: always 24h days
        ("UTC", "2024-03-30T00:00:00Z", 72, {"2024-03-31": 24}),
    ],
)
def test_dst_four_zones_wall_clock_disambiguation_and_energy_conservation(
    tz_name: str,
    utc_start: str,
    n_hours: int,
    expected_day_hours: dict[str, int],
):
    zi = ZoneInfo(tz_name)
    t0 = datetime.fromisoformat(utc_start.replace("Z", "+00:00"))
    utc_instants = [t0 + timedelta(minutes=30 * k) for k in range(n_hours * 2)]
    # Format as naive local wall-clock strings in chronological order (including repeated fall-back wall times!)
    wall_strings = [dt.astimezone(zi).strftime("%Y-%m-%d %H:%M:%S") for dt in utc_instants]
    kwh_vals = [1.25 + 0.05 * (k % 11) for k in range(len(utc_instants))]

    df = pd.DataFrame({"ts": wall_strings, "elec_kwh": kwh_vals})
    schema = DatasetSchema(
        columns=[
            ColumnSpec(name="ts", role="time"),
            ColumnSpec(name="elec_kwh", role="target", quantity_type="flow", unit="kWh"),
        ],
        timezone=tz_name,
    )

    # 1. Verify chronological PEP-495 fold disambiguation preserves every half-hour reading with zero loss
    norm = normalize_timestamps_utc(df["ts"], tz_name=tz_name, dst_handling="wall_clock")
    assert len(norm.utc_timestamps.dropna()) == len(utc_instants)
    recovered_epoch = norm.utc_timestamps.astype("int64").to_numpy() // 1_000_000_000
    expected_epoch = np.array([int(dt.timestamp()) for dt in utc_instants], dtype="int64")
    assert np.array_equal(recovered_epoch, expected_epoch)

    # 2. Aggregate to daily in local calendar timezone and verify energy conservation
    out_daily, rep, _ = prepare_temporal(
        df,
        "ts",
        AutoLog({"sampling_period": "daily", "timezone": tz_name}),
        schema=schema,
    )
    assert abs(float(out_daily["elec_kwh"].sum()) - float(sum(kwh_vals))) < 1e-9
    assert rep.n_duplicates == 0

    # Check exact local-day hour count on spring-forward (23h) / fall-back (25h) / UTC (24h)
    local_dates = [dt.astimezone(zi).strftime("%Y-%m-%d") for dt in utc_instants]
    counts_by_day = pd.Series(kwh_vals).groupby(local_dates).count()
    for day_str, exp_hours in expected_day_hours.items():
        assert int(counts_by_day[day_str]) == exp_hours * 2


def test_spring_forward_nonexistent_wall_time_audited_not_silently_corrupted():
    # 2024-03-31 02:30 does not exist in Europe/Berlin (clocks jump 02:00 -> 03:00)
    s = pd.Series(
        [
            "2024-03-31 01:00:00",
            "2024-03-31 02:30:00",  # nonexistent!
            "2024-03-31 03:00:00",
            "2024-03-31 04:00:00",
        ]
    )
    norm_drop = normalize_timestamps_utc(s, tz_name="Europe/Berlin", dst_handling="drop_ambiguous")
    assert norm_drop.n_nonexistent == 1
    assert pd.isna(norm_drop.utc_timestamps.iloc[1])


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    tz_name=st.sampled_from(list(REQUIRED_DST_ZONES)),
    offset_hours=st.integers(min_value=0, max_value=8000),
    span_hours=st.integers(min_value=26, max_value=96),
    kwh_seq=st.lists(
        st.floats(min_value=0.1, max_value=500.0, allow_nan=False, allow_infinity=False),
        min_size=96,
        max_size=96,
    ),
)
def test_hypothesis_four_zones_utc_monotonicity_and_flow_conservation(
    tz_name: str,
    offset_hours: int,
    span_hours: int,
    kwh_seq: list[float],
):
    zi = ZoneInfo(tz_name)
    base_utc = datetime(2024, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")) + timedelta(hours=offset_hours)
    instants = [base_utc + timedelta(hours=i) for i in range(span_hours)]
    vals = kwh_seq[:span_hours]
    # Use ISO timestamps with explicit local offset so even Lord Howe's 30-min fall-back is exact on hourly steps
    iso_local = [dt.astimezone(zi).isoformat() for dt in instants]

    df = pd.DataFrame({"ts": iso_local, "elec_kwh": vals})
    schema = DatasetSchema(
        columns=[
            ColumnSpec(name="ts", role="time"),
            ColumnSpec(name="elec_kwh", role="target", quantity_type="flow"),
        ],
        timezone=tz_name,
    )
    out, rep, _ = prepare_temporal(
        df,
        "ts",
        AutoLog({"sampling_period": "daily", "timezone": tz_name}),
        schema=schema,
    )
    assert out.index.is_monotonic_increasing
    assert rep.n_duplicates == 0
    assert abs(float(out["elec_kwh"].sum()) - float(sum(vals))) < 1e-6


def test_gap_classification_single_multi_and_extended_outage():
    # Hourly series with: 1-step gap (03:00), 5-step medium gap (07:00..11:00), and 30-step shutdown (>=24h)
    full = pd.date_range("2024-06-01 00:00:00", periods=60, freq="h")
    drop_idx = {3, 7, 8, 9, 10, 11} | set(range(20, 50))
    kept = [ts for i, ts in enumerate(full) if i not in drop_idx]
    gaps = classify_gaps(pd.DatetimeIndex(kept), nominal_seconds=3600.0)

    kinds = [g.kind for g in gaps]
    assert kinds == ["short_gap", "medium_gap", "extended_shutdown"]
    assert gaps[0].missing_steps == 1
    assert gaps[1].missing_steps == 5
    assert gaps[2].missing_steps == 30
