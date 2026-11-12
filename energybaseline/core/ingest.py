"""Data ingestion: Excel / CSV / Access / manual; schema inference; units; import validation."""
from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import ensure_dirs
from .temporal import parse_timestamps


class IngestError(RuntimeError):
    pass


UNIT_TO_CANON = {  # (from_unit -> (canonical unit, factor, offset))
    "wh": ("kWh", 0.001, 0.0), "kwh": ("kWh", 1.0, 0.0), "mwh": ("kWh", 1000.0, 0.0),
    "gj": ("kWh", 277.7778, 0.0), "mj": ("kWh", 0.2777778, 0.0), "kbtu": ("kWh", 0.293071, 0.0),
    "mmbtu": ("kWh", 293.0711, 0.0), "therm": ("kWh", 29.30711, 0.0),
    "w": ("kW", 0.001, 0.0), "kw": ("kW", 1.0, 0.0), "mw": ("kW", 1000.0, 0.0),
    "degc": ("degC", 1.0, 0.0), "c": ("degC", 1.0, 0.0), "degf": ("degC", 5 / 9, -32 * 5 / 9), "f": ("degC", 5 / 9, -32 * 5 / 9),
    "k": ("degC", 1.0, -273.15), "m3": ("m3", 1.0, 0.0), "l": ("m3", 0.001, 0.0), "ft3": ("m3", 0.0283168, 0.0),
}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def retain_raw_copy(path: str | Path) -> Path:
    """Raw-data retention: a byte-identical copy under data/uploads/."""
    p = Path(path)
    dest = ensure_dirs()["uploads"] / f"{sha256_file(p)[:12]}_{p.name}"
    if not dest.exists():
        shutil.copy2(p, dest)
    return dest


def list_sheets(path: str) -> list[str]:
    if Path(path).suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return list(pd.ExcelFile(path).sheet_names)
    return []


def list_access_tables(path: str) -> list[str]:
    conn = _access_conn(path)
    try:
        cur = conn.cursor()
        return [r.table_name for r in cur.tables(tableType="TABLE")]
    finally:
        conn.close()


def _access_conn(path: str):
    try:
        import pyodbc
    except Exception as exc:
        raise IngestError("Microsoft Access import needs the 'pyodbc' package and the Microsoft Access ODBC driver "
                          "(install the free 'Microsoft Access Database Engine 2016 Redistributable'). Details: %s" % exc)
    drivers = [d for d in pyodbc.drivers() if "Access" in d]
    if not drivers:
        raise IngestError("No Microsoft Access ODBC driver found. Install the Access Database Engine (64-bit) or export the table to CSV/Excel.")
    return pyodbc.connect(f"DRIVER={{{drivers[0]}}};DBQ={path};")


def load_file(path: str, sheet: str | None = None, table: str | None = None) -> pd.DataFrame:
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in (".csv", ".txt", ".tsv"):
            raw = p.read_bytes()
            for enc in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            df = pd.read_csv(io.StringIO(text), sep=None, engine="python")
        elif ext in (".xlsx", ".xlsm", ".xls"):
            df = pd.read_excel(p, sheet_name=sheet or 0)
        elif ext in (".mdb", ".accdb"):
            tables = list_access_tables(path)
            table = table or (tables[0] if tables else None)
            if not table:
                raise IngestError("The Access database contains no tables.")
            conn = _access_conn(path)
            try:
                df = pd.read_sql(f"SELECT * FROM [{table}]", conn)
            finally:
                conn.close()
        else:
            raise IngestError(f"Unsupported file type '{ext}'. Use .csv, .xlsx, .xls, .mdb or .accdb.")
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"Could not read '{p.name}': {exc}") from exc
    df.columns = [str(c).strip() for c in df.columns]
    return coerce_numeric(df)


def coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Convert text columns that are >=90 % numeric (also decimal commas) to numbers."""
    df = df.copy()
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_bool_dtype(s):
            if pd.api.types.is_bool_dtype(s):
                df[c] = s.astype(float)
            continue
        txt = s.astype("string")
        num = pd.to_numeric(txt.str.replace(",", ".", regex=False).str.strip(), errors="coerce")
        nn = s.notna().sum()
        if nn and num.notna().sum() / nn >= 0.9:
            df[c] = num
    return df


def guess_time_column(df: pd.DataFrame) -> str | None:
    best, best_score = None, 0.0
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_datetime64_any_dtype(s):
            continue
        name_hit = any(k in c.lower() for k in ("time", "date", "stamp", "datetime", "period"))
        if pd.api.types.is_datetime64_any_dtype(s):
            ok = 1.0
        else:
            sample = s.dropna().head(200)
            if sample.empty:
                continue
            ok = float(parse_timestamps(sample).notna().mean())
        score = ok + (0.5 if name_hit else 0)
        if ok >= 0.9 and score > best_score:
            best, best_score = c, score
    return best


def data_dictionary(df: pd.DataFrame, time_col: str | None = None) -> pd.DataFrame:
    rows = []
    for c in df.columns:
        s = df[c]
        is_num = pd.api.types.is_numeric_dtype(s)
        role = "timestamp" if c == time_col else ("numeric" if is_num else "categorical/text")
        rows.append({
            "column": c, "dtype": str(s.dtype), "role": role,
            "missing": int(s.isna().sum()), "missing_%": round(100 * s.isna().mean(), 2), "unique": int(s.nunique(dropna=True)),
            "min": float(s.min()) if is_num and s.notna().any() else None,
            "max": float(s.max()) if is_num and s.notna().any() else None,
            "mean": float(s.mean()) if is_num and s.notna().any() else None,
            "std": float(s.std()) if is_num and s.notna().sum() > 1 else None,
        })
    return pd.DataFrame(rows)


def normalize_units(df: pd.DataFrame, units: dict[str, str]) -> tuple[pd.DataFrame, list[str]]:
    """Convert declared column units to canonical units. Returns (df, conversion log)."""
    df = df.copy()
    log: list[str] = []
    for col, unit in units.items():
        if col not in df.columns or not unit:
            continue
        key = unit.strip().lower().replace("°", "").replace("deg ", "deg")
        if key not in UNIT_TO_CANON:
            log.append(f"{col}: unit '{unit}' unknown - left unchanged")
            continue
        canon, factor, offset = UNIT_TO_CANON[key]
        if factor == 1.0 and offset == 0.0:
            log.append(f"{col}: unit '{unit}' already canonical ({canon})")
            continue
        df[col] = df[col] * factor + offset
        log.append(f"{col}: converted {unit} -> {canon} (x{factor:g}{offset:+g})")
    return df, log


def import_report(df: pd.DataFrame, time_col: str | None) -> dict:
    num = df.select_dtypes("number")
    const = [c for c in num.columns if num[c].nunique(dropna=True) <= 1]
    rep = {
        "rows": int(len(df)), "columns": int(df.shape[1]), "time_column": time_col,
        "duplicate_rows": int(df.duplicated().sum()),
        "columns_with_missing": {c: int(v) for c, v in df.isna().sum().items() if v},
        "constant_columns": const,
        "non_numeric_columns": [c for c in df.columns if c not in num.columns and c != time_col],
        "warnings": [],
    }
    if time_col:
        ts = parse_timestamps(df[time_col])
        rep["unparseable_timestamps"] = int(ts.isna().sum())
        rep["duplicate_timestamps"] = int(ts.dropna().duplicated().sum())
        if ts.notna().any():
            rep["time_span"] = [str(ts.min()), str(ts.max())]
        if ts.dropna().is_monotonic_decreasing is False and not ts.dropna().is_monotonic_increasing:
            rep["warnings"].append("Timestamps are not sorted - they will be sorted.")
    else:
        rep["warnings"].append("No timestamp column identified.")
    if len(df) < 24:
        rep["warnings"].append("Very few rows (<24): only simple models will be viable.")
    return rep


def manual_frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns)
    return coerce_numeric(df.replace("", np.nan))
