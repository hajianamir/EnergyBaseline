"""Phase 2 gate tests: Streamed Ingestion, Parquet Cache, Unit Registry, and Read-Only SQL AST/Driver (ING-1..ING-9, SEC-4)."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from energybaseline.core import ingest
from energybaseline.core.ingest import (
    UNIT_REGISTRY,
    IngestError,
    UnitConversionError,
    convert_units,
    detect_csv_dialect,
    detect_text_encoding,
    load_file,
    normalize_units,
    read_parquet_cache,
    select_best_sheet,
    sha256_dataframe,
    stream_csv_chunks,
    write_parquet_cache,
)
from energybaseline.core.sqlsource import ConnectionProfile, SourceError, _engine, run_query, validate_select


def test_unit_registry_is_immutable_and_converts_accurately():
    with pytest.raises(TypeError):
        UNIT_REGISTRY["fake"] = None  # type: ignore[index]

    # Energy: 2 MWh -> 2000 kWh; 3.6 GJ -> 1000 kWh
    assert np.allclose(convert_units(pd.Series([2.0]), "MWh"), [2000.0])
    assert np.allclose(convert_units(pd.Series([3.6]), "GJ"), [1000.0])

    # Temperature affine: 32 degF -> 0 degC, 212 degF -> 100 degC; 273.15 K -> 0 degC
    assert np.allclose(convert_units(pd.Series([32.0, 212.0]), "degF"), [0.0, 100.0])
    assert np.allclose(convert_units(pd.Series([273.15]), "K"), [0.0])

    # Explicit cross-dimension conversion raises UnitConversionError
    with pytest.raises(UnitConversionError, match="cannot convert"):
        convert_units(pd.Series([20.0]), "degC", to_unit="kWh")

    df = pd.DataFrame({"Elec": [1.5, 2.0], "Temp": [68.0, 77.0]})
    out, msgs = normalize_units(df, {"Elec": "MWh", "Temp": "degF"})
    assert np.allclose(out["Elec"], [1500.0, 2000.0])
    assert np.allclose(out["Temp"], [20.0, 25.0])
    assert len(msgs) == 2


def test_encoding_delimiter_european_decimal_and_tsv_ingest(tmp_path: Path):
    # CP1252 semicolon-separated file with European decimals ("1.234,56") and degree symbol
    lines = [
        "Timestamp;Elec_kWh;Temp_°C",
        "2024-01-01 00:00;1.234,50;12,5",
        "2024-01-01 01:00;2.000,25;13,0",
        "2024-01-01 02:00;980,00;11,75",
    ]
    eu_csv = tmp_path / "eu_cp1252.csv"
    eu_csv.write_bytes("\n".join(lines).encode("cp1252"))

    enc = detect_text_encoding(eu_csv)
    assert enc in ("cp1252", "latin-1")
    delim, dec = detect_csv_dialect(eu_csv, encoding=enc)
    assert delim == ";"
    assert dec == ","

    df = load_file(eu_csv)
    assert len(df) == 3
    assert np.allclose(df["Elec_kWh"].to_numpy(dtype=float), [1234.50, 2000.25, 980.0])
    assert np.allclose(df.iloc[:, 2].to_numpy(dtype=float), [12.5, 13.0, 11.75])

    # UTF-16 LE BOM TSV file
    tsv_path = tmp_path / "utf16_meter.tsv"
    tsv_text = "timestamp\tkwh\n2024-02-01 00:00\t42.5\n2024-02-01 01:00\t44.0\n2024-02-01 02:00\t41.0\n"
    tsv_path.write_bytes(b"\xff\xfe" + tsv_text.encode("utf-16-le"))
    assert detect_text_encoding(tsv_path) == "utf-16-le"
    df_tsv = load_file(tsv_path)
    assert list(df_tsv.columns) == ["timestamp", "kwh"]
    assert np.allclose(df_tsv["kwh"].to_numpy(dtype=float), [42.5, 44.0, 41.0])


def test_excel_multisheet_best_sheet_heuristic(tmp_path: Path):
    xlsx_path = tmp_path / "workbook.xlsx"
    cover = pd.DataFrame({"Report": ["Facility Energy Audit", "Prepared 2024", "Confidential"]})
    ts_sheet = pd.DataFrame(
        {
            "Timestamp": pd.date_range("2024-01-01", periods=24, freq="h"),
            "Chiller_kWh": np.linspace(50.0, 80.0, 24),
            "OAT_C": np.linspace(15.0, 28.0, 24),
        }
    )
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        cover.to_excel(writer, sheet_name="Cover_Notes", index=False)
        ts_sheet.to_excel(writer, sheet_name="Meter_Intervals", index=False)

    assert select_best_sheet(xlsx_path) == "Meter_Intervals"
    loaded = load_file(xlsx_path)
    assert "Chiller_kWh" in loaded.columns and len(loaded) == 24


def test_streamed_csv_chunking_and_parquet_cache_tamper_invalidation(tmp_path: Path):
    n_rows = 120_000
    big_csv = tmp_path / "large_meter.csv"
    rng = np.random.default_rng(7)
    df_src = pd.DataFrame(
        {
            "timestamp": pd.date_range("2020-01-01", periods=n_rows, freq="15min").strftime("%Y-%m-%d %H:%M:%S"),
            "kwh": np.round(rng.uniform(10.0, 200.0, size=n_rows), 3),
            "temp_c": np.round(rng.normal(15.0, 6.0, size=n_rows), 2),
        }
    )
    df_src.to_csv(big_csv, index=False)

    chunks_iter, _ = stream_csv_chunks(big_csv, chunk_rows=50_000)
    chunk_lengths = [len(ch) for ch in chunks_iter]
    assert chunk_lengths == [50_000, 50_000, 20_000]

    cache_dir = tmp_path / "cache"
    df_loaded = load_file(big_csv, cache_dir=cache_dir)
    assert len(df_loaded) == n_rows

    src_sha = ingest.sha256_file(big_csv)
    cached = read_parquet_cache(src_sha, cache_dir=cache_dir)
    assert cached is not None
    assert sha256_dataframe(cached) == sha256_dataframe(df_loaded)
    assert cached.equals(df_loaded)

    # Direct .parquet load via load_file
    pq_file = cache_dir / f"{src_sha}_default.parquet"
    direct_pq = load_file(pq_file)
    assert direct_pq.equals(df_loaded)

    # Tamper with Parquet file bytes -> read_parquet_cache must invalidate and return None
    orig_bytes = pq_file.read_bytes()
    pq_file.write_bytes(orig_bytes + b"tampered_trailing_bytes")
    assert read_parquet_cache(src_sha, cache_dir=cache_dir) is None
    assert not pq_file.exists(), "Tampered Parquet cache file must be purged on mismatch"

    # Re-create cache and tamper with manifest parquet_sha256
    pq_file2, _ = write_parquet_cache(df_loaded, source_sha256=src_sha, cache_dir=cache_dir)
    mf_path = pq_file2.with_name(f"{src_sha}_default.manifest.json")
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    mf["parquet_sha256"] = "0" * 64
    mf_path.write_text(json.dumps(mf), encoding="utf-8")
    assert read_parquet_cache(src_sha, cache_dir=cache_dir) is None

    # Corrupt Parquet file loaded directly must raise IngestError
    bad_pq = tmp_path / "corrupt.parquet"
    bad_pq.write_bytes(b"NOT_A_PARQUET_FILE")
    with pytest.raises(IngestError, match="Parquet"):
        load_file(bad_pq)


@pytest.mark.parametrize(
    "good_sql",
    [
        "SELECT ts, kwh, temp_c FROM meters WHERE kwh >= 0 ORDER BY ts",
        "WITH hourly AS (SELECT ts, SUM(kwh) AS kwh FROM meters GROUP BY ts) SELECT * FROM hourly",
        "SELECT ts, kwh FROM meters_a UNION ALL SELECT ts, kwh FROM meters_b;",
    ],
)
def test_sqlglot_ast_accepts_pure_select_and_cte_queries(good_sql: str):
    cleaned = validate_select(good_sql)
    assert "SELECT" in cleaned.upper()


@pytest.mark.parametrize(
    "bad_sql",
    [
        "",
        "SELECT 1; SELECT 2",
        "SELECT * FROM meters; DROP TABLE meters",
        "INSERT INTO meters VALUES ('2024-01-01', 10)",
        "UPDATE meters SET kwh = 0",
        "DELETE FROM meters WHERE 1=1",
        "DROP TABLE meters",
        "ALTER TABLE meters ADD COLUMN x INT",
        "CREATE TABLE hacked (id INT)",
        "TRUNCATE TABLE meters",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE 'evil.db' AS evil",
        "VACUUM",
        "WITH del AS (DELETE FROM meters RETURNING *) SELECT * FROM del",
        "SELECT * INTO new_tbl FROM meters",
        "SELECT * FROM meters FOR UPDATE",
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT pg_read_file('postgresql.conf')",
        "EXEC xp_cmdshell 'whoami'",
    ],
)
def test_sqlglot_ast_rejects_adversarial_sql_constructs(bad_sql: str):
    with pytest.raises(SourceError):
        validate_select(bad_sql)


def test_sqlite_driver_enforces_readonly_mode_even_with_spaces_in_path(tmp_path: Path):
    spaced_dir = tmp_path / "dir with spaces"
    spaced_dir.mkdir(parents=True, exist_ok=True)
    db_path = spaced_dir / "facility meters.sqlite"

    con = sqlite3.connect(db_path)
    try:
        con.execute("CREATE TABLE meters (ts TEXT, kwh REAL)")
        con.executemany(
            "INSERT INTO meters VALUES (?, ?)",
            [(f"2024-01-01 {h:02d}:00:00", 10.0 + h) for h in range(25)],
        )
        con.commit()
    finally:
        con.close()

    prof = ConnectionProfile(name="ro_test", driver="SQLite", database=str(db_path))
    df = run_query(prof, "SELECT ts, kwh FROM meters ORDER BY ts", max_rows=10, timeout_seconds=5)
    assert len(df) == 10
    assert float(df["kwh"].iloc[0]) == 10.0

    # Verify C-driver level read-only protection even if AST check were bypassed
    eng = _engine(prof, timeout_seconds=5)
    try:
        with pytest.raises(Exception, match="readonly|read-only"):
            with eng.begin() as conn:
                conn.execute(text("INSERT INTO meters VALUES ('2024-02-01 00:00:00', 999.0)"))
    finally:
        eng.dispose()
