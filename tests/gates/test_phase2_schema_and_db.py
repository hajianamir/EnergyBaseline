"""Phase 2 gate tests: Column Schema (DAT-1..DAT-8) and reversible Alembic migration 0002."""

from __future__ import annotations

import json
from pathlib import Path

from alembic import command
import pandas as pd
import pytest
from sqlalchemy import inspect

from energybaseline.core import ingest, store
from energybaseline.core.schema import (
    Availability,
    ColumnRole,
    ColumnSpec,
    DatasetSchema,
    Inclusion,
    NonRoutineEventSpec,
    OutlierActionPolicy,
    QuantityType,
    SchemaIntegrityError,
    SchemaValidationError,
    load_schema_from_db,
    save_schema_to_db,
    suggest_dataset_schema,
)
from energybaseline.db.migrate import alembic_config
from energybaseline.db.session import _make_sqlite_engine, init_database, reset_engine


def test_column_spec_and_dataset_schema_invariants():
    cols = [
        ColumnSpec(name="ts", role=ColumnRole.TIME),
        ColumnSpec(
            name="elec_kwh",
            role=ColumnRole.TARGET,
            quantity_type=QuantityType.FLOW,
            inclusion=Inclusion.OBSERVED_ONLY,
            unit="kWh",
            availability=Availability.ALWAYS,
            suggested_by_heuristic=False,
        ),
        ColumnSpec(
            name="temp_c",
            role=ColumnRole.PREDICTOR,
            quantity_type=QuantityType.QUALITY,
            inclusion=Inclusion.KNOWN_IN_ADVANCE,
            unit="degC",
            availability=Availability.REAL_TIME,
        ),
        ColumnSpec(
            name="demand_kw",
            role=ColumnRole.PREDICTOR,
            quantity_type=QuantityType.RATE,
            inclusion=Inclusion.OBSERVED_ONLY,
            unit="kW",
        ),
        ColumnSpec(
            name="tank_m3",
            role=ColumnRole.PREDICTOR,
            quantity_type=QuantityType.STOCK,
            unit="m3",
        ),
        ColumnSpec(
            name="tariff_idx",
            role=ColumnRole.STATIC_FACTOR,
            quantity_type=QuantityType.INDEX,
            inclusion=Inclusion.KNOWN_IN_ADVANCE,
        ),
        ColumnSpec(name="shift_code", role=ColumnRole.GROUP),
        ColumnSpec(name="hours_weight", role=ColumnRole.WEIGHT, quantity_type=QuantityType.FLOW),
        ColumnSpec(name="row_note", role=ColumnRole.IGNORE),
    ]
    nre = NonRoutineEventSpec(
        name="Chiller retrofit",
        start="2024-06-01T00:00:00",
        end="2024-06-15T23:59:59",
        action="exclude",
        affected_targets=("elec_kwh",),
        reason="Temporary chiller bypass",
    )
    schema = DatasetSchema(
        columns=cols,
        dataset_name="plant.csv",
        dataset_sha256="a" * 64,
        timezone="Europe/Berlin",
        outlier_policy=OutlierActionPolicy.ASK_USER,
        non_routine_events=[nre],
    )
    assert schema.time_column == "ts"
    assert schema.target_columns == ["elec_kwh"]
    assert set(schema.predictor_columns + schema.static_factor_columns) == {
        "temp_c",
        "demand_kw",
        "tank_m3",
        "tariff_idx",
    }
    assert schema.group_columns == ["shift_code"]
    assert schema.weight_column == "hours_weight"
    assert schema.ignored_columns == ["row_note"]
    assert schema.known_in_advance_columns == ["temp_c", "tariff_idx"]
    assert schema.compute_sha256() == DatasetSchema.from_dict(schema.to_dict()).compute_sha256()


def test_dataset_schema_rejects_invalid_configurations():
    # Zero time columns
    with pytest.raises(SchemaValidationError, match="time"):
        DatasetSchema(columns=[ColumnSpec(name="elec", role="target", quantity_type="flow")])

    # Multiple time columns
    with pytest.raises(SchemaValidationError, match="time"):
        DatasetSchema(
            columns=[
                ColumnSpec(name="t1", role="time"),
                ColumnSpec(name="t2", role="time"),
                ColumnSpec(name="elec", role="target", quantity_type="flow"),
            ]
        )

    # Zero target columns
    with pytest.raises(SchemaValidationError, match="target"):
        DatasetSchema(
            columns=[
                ColumnSpec(name="t1", role="time"),
                ColumnSpec(name="temp", role="predictor", quantity_type="quality"),
            ]
        )

    # Invalid quantity_type
    with pytest.raises(SchemaValidationError, match="quantity_type"):
        ColumnSpec(name="elec", role="target", quantity_type="not_a_quantity_type")

    # Invalid IANA timezone
    with pytest.raises(SchemaValidationError, match="timezone"):
        DatasetSchema(
            columns=[
                ColumnSpec(name="t1", role="time"),
                ColumnSpec(name="elec", role="target", quantity_type="flow"),
            ],
            timezone="Mars/Olympus_Mons",
        )

    # Invalid NRE interval (end < start)
    with pytest.raises(SchemaValidationError, match="end"):
        NonRoutineEventSpec(name="bad", start="2024-05-10", end="2024-05-01")

    # Missing column in DataFrame check
    schema = DatasetSchema(
        columns=[
            ColumnSpec(name="t1", role="time"),
            ColumnSpec(name="elec", role="target", quantity_type="flow"),
        ]
    )
    with pytest.raises(SchemaValidationError, match="absent"):
        schema.validate(df_columns=["t1", "other_col"])


def test_schema_json_sha256_roundtrip_and_tamper_detection(tmp_path: Path):
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=10, freq="h"),
            "electricity_kWh": [10.0 + i for i in range(10)],
            "outdoor_temp_C": [5.0 + 0.2 * i for i in range(10)],
        }
    )
    suggested = suggest_dataset_schema(df, dataset_name="sample.csv", dataset_sha256="b" * 64)
    assert all(c.suggested_by_heuristic for c in suggested.columns)

    json_path = tmp_path / "schema.json"
    saved = suggested.save_json(json_path)
    assert saved == json_path
    loaded = DatasetSchema.load_json(json_path, verify_sha256=True)
    assert loaded.compute_sha256() == suggested.compute_sha256()
    assert loaded.to_dict() == suggested.to_dict()

    # Tamper with column role inside JSON without updating sha256
    raw_obj = json.loads(json_path.read_text(encoding="utf-8"))
    raw_obj["schema"]["columns"][2]["role"] = "ignore"
    json_path.write_text(json.dumps(raw_obj), encoding="utf-8")

    with pytest.raises(SchemaIntegrityError, match="SHA-256"):
        DatasetSchema.load_json(json_path, verify_sha256=True)


def test_alembic_0002_reversible_ladder_and_db_schema_tamper_detection(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "phase2_ladder.sqlite3"
    db_url = f"sqlite:///{db_file.as_posix()}"
    cfg = alembic_config(db_url)

    command.upgrade(cfg, "0001")
    eng = _make_sqlite_engine(db_url)
    try:
        tabs_v1 = set(inspect(eng).get_table_names())
        assert "datasets" in tabs_v1
        assert "dataset_schemas" not in tabs_v1
        assert "non_routine_events" not in tabs_v1
    finally:
        eng.dispose()

    command.upgrade(cfg, "head")
    eng = _make_sqlite_engine(db_url)
    try:
        tabs_v2 = set(inspect(eng).get_table_names())
        assert "dataset_schemas" in tabs_v2
        assert "non_routine_events" in tabs_v2
    finally:
        eng.dispose()

    # Reversible downgrade to 0001 and re-upgrade to head
    command.downgrade(cfg, "0001")
    eng = _make_sqlite_engine(db_url)
    try:
        tabs_down = set(inspect(eng).get_table_names())
        assert "dataset_schemas" not in tabs_down
        assert "non_routine_events" not in tabs_down
    finally:
        eng.dispose()

    command.upgrade(cfg, "head")

    # Now test full persistence + NRE rows + DB SHA-256 tamper verification via store
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EB_HOME", str(tmp_path / "eb_home"))
    reset_engine()
    init_database()

    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=6, freq="D"),
            "gas_m3": [100.0, 110.0, 95.0, 105.0, 120.0, 98.0],
            "hdd": [12.0, 14.0, 10.0, 11.0, 15.0, 9.0],
        }
    )
    ds_id = store.register_dataset("gas.csv", "", "c" * 64, df)
    schema = suggest_dataset_schema(df, dataset_name="gas.csv", dataset_sha256="c" * 64).with_column(
        ColumnSpec(
            name="hdd",
            role=ColumnRole.PREDICTOR,
            quantity_type=QuantityType.FLOW,
            inclusion=Inclusion.KNOWN_IN_ADVANCE,
            suggested_by_heuristic=False,
        )
    )
    schema = DatasetSchema(
        columns=schema.columns,
        dataset_name=schema.dataset_name,
        dataset_sha256=schema.dataset_sha256,
        outlier_policy=OutlierActionPolicy.FLAG_ONLY,
        non_routine_events=[
            NonRoutineEventSpec(
                name="Boiler maintenance",
                start="2024-01-03",
                end="2024-01-04",
                action="exclude",
                reason="Annual burner inspection",
            )
        ],
    )
    summary = ingest.build_ingestion_summary(df, source_name="gas.csv", source_sha256="c" * 64, time_col="timestamp")
    sid = save_schema_to_db(schema, dataset_id=ds_id, ingestion_summary=summary)

    loaded = load_schema_from_db(schema_id=sid)
    assert loaded is not None
    assert loaded.compute_sha256() == schema.compute_sha256()
    assert loaded.get_column("hdd").suggested_by_heuristic is False
    assert store.load_ingestion_summary(sid)["report"]["rows"] == 6
    nres = store.load_non_routine_events(dataset_id=ds_id)
    assert len(nres) == 1 and nres[0].name == "Boiler maintenance"

    # Corrupt stored schema_json directly in SQLite and verify SchemaIntegrityError
    from sqlalchemy import text

    eng2 = _make_sqlite_engine(db_url)
    try:
        with eng2.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dataset_schemas SET outlier_policy = 'auto_apply', schema_json = replace(schema_json, 'flag_only', 'auto_apply') WHERE id = :sid"
                ),
                {"sid": sid},
            )
    finally:
        eng2.dispose()

    with pytest.raises(SchemaIntegrityError, match="SHA-256"):
        store.load_dataset_schema(schema_id=sid, verify_sha256=True)

    reset_engine()
