"""Data ingestion: streamed CSV / TSV / Excel / Parquet / Access / manual; units registry; Parquet cache (DAT-1..8)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from ..config import ensure_dirs
from .temporal import parse_timestamps


class IngestError(RuntimeError):
    """Raised on malformed input files, unsupported formats, or tampered Parquet cache entries."""


class UnitConversionError(IngestError):
    """Raised when converting between unknown or physically incompatible units (DAT-6)."""


# ---------------------------------------------------------------------------
# Physical Unit Metadata Registry & Deterministic Conversion (DAT-6)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UnitDef:
    symbol: str
    canonical: str
    dimension: str
    factor: float
    offset: float = 0.0


_RAW_UNITS: tuple[tuple[str, UnitDef], ...] = (
    # Energy (canonical: kWh)
    ("wh", UnitDef("Wh", "kWh", "energy", 0.001, 0.0)),
    ("kwh", UnitDef("kWh", "kWh", "energy", 1.0, 0.0)),
    ("mwh", UnitDef("MWh", "kWh", "energy", 1000.0, 0.0)),
    ("gwh", UnitDef("GWh", "kWh", "energy", 1_000_000.0, 0.0)),
    ("gj", UnitDef("GJ", "kWh", "energy", 277.7777777778, 0.0)),
    ("mj", UnitDef("MJ", "kWh", "energy", 0.2777777778, 0.0)),
    ("kj", UnitDef("kJ", "kWh", "energy", 0.0002777777778, 0.0)),
    ("kbtu", UnitDef("kBtu", "kWh", "energy", 0.29307107, 0.0)),
    ("mmbtu", UnitDef("MMBtu", "kWh", "energy", 293.07107, 0.0)),
    ("therm", UnitDef("therm", "kWh", "energy", 29.307107, 0.0)),
    ("therms", UnitDef("therm", "kWh", "energy", 29.307107, 0.0)),
    # Power / Energy Rate (canonical: kW)
    ("w", UnitDef("W", "kW", "power", 0.001, 0.0)),
    ("kw", UnitDef("kW", "kW", "power", 1.0, 0.0)),
    ("mw", UnitDef("MW", "kW", "power", 1000.0, 0.0)),
    ("ton_ref", UnitDef("ton_ref", "kW", "power", 3.5168525, 0.0)),
    # Temperature (canonical: degC)
    ("degc", UnitDef("degC", "degC", "temperature", 1.0, 0.0)),
    ("c", UnitDef("degC", "degC", "temperature", 1.0, 0.0)),
    ("celsius", UnitDef("degC", "degC", "temperature", 1.0, 0.0)),
    ("degf", UnitDef("degF", "degC", "temperature", 5.0 / 9.0, -32.0 * 5.0 / 9.0)),
    ("f", UnitDef("degF", "degC", "temperature", 5.0 / 9.0, -32.0 * 5.0 / 9.0)),
    ("fahrenheit", UnitDef("degF", "degC", "temperature", 5.0 / 9.0, -32.0 * 5.0 / 9.0)),
    ("k", UnitDef("K", "degC", "temperature", 1.0, -273.15)),
    ("kelvin", UnitDef("K", "degC", "temperature", 1.0, -273.15)),
    # Volume (canonical: m3)
    ("m3", UnitDef("m3", "m3", "volume", 1.0, 0.0)),
    ("l", UnitDef("L", "m3", "volume", 0.001, 0.0)),
    ("liter", UnitDef("L", "m3", "volume", 0.001, 0.0)),
    ("ft3", UnitDef("ft3", "m3", "volume", 0.0283168466, 0.0)),
    ("ccf", UnitDef("ccf", "m3", "volume", 2.83168466, 0.0)),
    ("gal", UnitDef("gal", "m3", "volume", 0.00378541178, 0.0)),
    # Volumetric Flow Rate (canonical: m3/h)
    ("m3/h", UnitDef("m3/h", "m3/h", "flow_rate", 1.0, 0.0)),
    ("m3_h", UnitDef("m3/h", "m3/h", "flow_rate", 1.0, 0.0)),
    ("l/s", UnitDef("L/s", "m3/h", "flow_rate", 3.6, 0.0)),
    ("l_s", UnitDef("L/s", "m3/h", "flow_rate", 3.6, 0.0)),
    ("cfm", UnitDef("cfm", "m3/h", "flow_rate", 1.6990108, 0.0)),
    ("gpm", UnitDef("gpm", "m3/h", "flow_rate", 0.2271247, 0.0)),
    # Dimensionless / Ratio (canonical: ratio)
    ("ratio", UnitDef("ratio", "ratio", "dimensionless", 1.0, 0.0)),
    ("%", UnitDef("%", "ratio", "dimensionless", 0.01, 0.0)),
    ("pct", UnitDef("%", "ratio", "dimensionless", 0.01, 0.0)),
    ("percent", UnitDef("%", "ratio", "dimensionless", 0.01, 0.0)),
)

UNIT_REGISTRY = MappingProxyType(dict(_RAW_UNITS))
UNIT_TO_CANON: dict[str, tuple[str, float, float]] = {
    k: (u.canonical, u.factor, u.offset) for k, u in UNIT_REGISTRY.items()
}


def normalize_unit_key(unit: str) -> str:
    return unit.strip().lower().replace("°", "").replace("deg ", "deg").replace(" ", "")


def lookup_unit(unit: str) -> UnitDef | None:
    if not unit or not isinstance(unit, str):
        return None
    return UNIT_REGISTRY.get(normalize_unit_key(unit))


def convert_units(
    values: pd.Series | np.ndarray | float,
    from_unit: str,
    to_unit: str | None = None,
) -> pd.Series | np.ndarray | float:
    """Deterministically convert ``values`` from ``from_unit`` to ``to_unit`` (or canonical unit if ``None``)."""
    src = lookup_unit(from_unit)
    if src is None:
        raise UnitConversionError(f"Unknown source unit '{from_unit}'.")
    if to_unit is None:
        return values * src.factor + src.offset
    dst = lookup_unit(to_unit)
    if dst is None:
        raise UnitConversionError(f"Unknown target unit '{to_unit}'.")
    if src.dimension != dst.dimension:
        raise UnitConversionError(
            f"Incompatible physical dimensions: cannot convert '{from_unit}' ({src.dimension}) to '{to_unit}' ({dst.dimension})."
        )
    canonical_vals = values * src.factor + src.offset
    return (canonical_vals - dst.offset) / dst.factor


def normalize_units(df: pd.DataFrame, units: dict[str, str]) -> tuple[pd.DataFrame, list[str]]:
    """Convert declared column units to canonical units. Returns ``(df, conversion_log)``."""
    df = df.copy()
    log: list[str] = []
    for col, unit in units.items():
        if col not in df.columns or not unit:
            continue
        udef = lookup_unit(unit)
        if udef is None:
            log.append(f"{col}: unit '{unit}' unknown - left unchanged")
            continue
        if udef.factor == 1.0 and udef.offset == 0.0:
            log.append(f"{col}: unit '{unit}' already canonical ({udef.canonical})")
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce") * udef.factor + udef.offset
        log.append(f"{col}: converted {unit} -> {udef.canonical} (x{udef.factor:g}{udef.offset:+g})")
    return df, log


# ---------------------------------------------------------------------------
# SHA-256 Fingerprinting & Raw Data Retention (DAT-8)
# ---------------------------------------------------------------------------
def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dataframe(df: pd.DataFrame) -> str:
    """Deterministic SHA-256 content hash over a DataFrame's columns, dtypes, and cell values."""
    h = hashlib.sha256()
    meta = json.dumps(
        {"columns": [str(c) for c in df.columns], "dtypes": [str(t) for t in df.dtypes], "rows": int(len(df))},
        sort_keys=True,
    ).encode("utf-8")
    h.update(meta)
    if not df.empty:
        row_hashes = pd.util.hash_pandas_object(df, index=True).to_numpy(dtype="uint64", copy=False)
        h.update(row_hashes.tobytes())
    return h.hexdigest()


def retain_raw_copy(path: str | Path) -> Path:
    """Raw-data retention: a byte-identical copy under ``data/uploads/``."""
    p = Path(path)
    dest = ensure_dirs()["uploads"] / f"{sha256_file(p)[:12]}_{p.name}"
    if not dest.exists():
        shutil.copy2(p, dest)
    return dest


# ---------------------------------------------------------------------------
# Encoding, Delimiter, Decimal, and Worksheet Auto-Detection (DAT-1, AC-28)
# ---------------------------------------------------------------------------
def detect_text_encoding(sample: bytes | str | Path) -> str:
    """Detect text encoding deterministically from BOMs, UTF-8 validation, and CP1252/Latin-1 fallback."""
    if isinstance(sample, (str, Path)):
        with Path(sample).open("rb") as fh:
            sample = fh.read(65536)
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if sample.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if sample.startswith(b"\xfe\xff"):
        return "utf-16-be"
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        sample.decode("cp1252")
        return "cp1252"
    except UnicodeDecodeError:
        return "latin-1"


_EURO_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(?:\.\d{3})+,\d+$")
_DECIMAL_COMMA_RE = re.compile(r"^[+-]?\d+,\d+(?:[eE][+-]?\d+)?$")


def detect_csv_dialect(
    sample_text: str | Path,
    ext: str = ".csv",
    *,
    encoding: str | None = None,
) -> tuple[str, str]:
    """Detect ``(delimiter, decimal_char)`` from header and data lines without loading the entire file."""
    if isinstance(sample_text, Path) or (isinstance(sample_text, str) and "\n" not in sample_text and Path(sample_text).is_file()):
        p = Path(sample_text)
        ext = p.suffix.lower() or ext
        enc = encoding or detect_text_encoding(p)
        with p.open("rb") as fh:
            sample_text = fh.read(65536).decode(enc, errors="replace")
    lines = [ln for ln in sample_text.splitlines() if ln.strip()][:25]
    if not lines:
        return (("\t" if ext == ".tsv" else ","), ".")

    candidates = ("\t", ";", ",", "|") if ext == ".tsv" else (",", ";", "\t", "|")
    best_sep = candidates[0]
    best_score = -1.0

    for sep in candidates:
        counts = [ln.count(sep) for ln in lines[:10]]
        if max(counts, default=0) == 0:
            continue
        non_zero = [c for c in counts if c > 0]
        consistency = sum(1 for c in non_zero if c == counts[0]) / max(1, len(non_zero))
        score = counts[0] * consistency + (0.25 if (ext == ".tsv" and sep == "\t") else 0.0)
        if score > best_score:
            best_score = score
            best_sep = sep

    decimal = "."
    if best_sep in (";", "\t", "|") and len(lines) > 1:
        comma_nums = 0
        dot_nums = 0
        for ln in lines[1:15]:
            for cell in ln.split(best_sep):
                tok = cell.strip().strip('"').strip("'")
                if _DECIMAL_COMMA_RE.match(tok) or _EURO_THOUSANDS_RE.match(tok):
                    comma_nums += 1
                elif re.match(r"^[+-]?\d+\.\d+$", tok):
                    dot_nums += 1
        if comma_nums > dot_nums:
            decimal = ","
    return best_sep, decimal


def list_sheets(path: str) -> list[str]:
    if Path(path).suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        with pd.ExcelFile(path) as xf:
            return list(xf.sheet_names)
    return []


def select_best_sheet(path: str | Path) -> str | int:
    """Auto-detect the primary data sheet in a multi-sheet workbook (DAT-1)."""
    with pd.ExcelFile(path) as xf:
        names = list(xf.sheet_names)
        if not names:
            return 0
        if len(names) == 1:
            return names[0]
        for name in names:
            low = name.strip().lower()
            if low in ("readme", "notes", "cover", "metadata", "info"):
                continue
            preview = xf.parse(sheet_name=name, nrows=5)
            if len(preview.columns) >= 2 and len(preview) >= 1:
                return name
        return names[0]


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
        raise IngestError(
            "Microsoft Access import needs the 'pyodbc' package and the Microsoft Access ODBC driver "
            f"(install the free 'Microsoft Access Database Engine 2016 Redistributable'). Details: {exc}"
        ) from exc
    drivers = [d for d in pyodbc.drivers() if "Access" in d]
    if not drivers:
        raise IngestError(
            "No Microsoft Access ODBC driver found. Install the Access Database Engine (64-bit) or export the table to CSV/Excel."
        )
    return pyodbc.connect(f"DRIVER={{{drivers[0]}}};DBQ={path};")


# ---------------------------------------------------------------------------
# Verified Parquet Cache (Gate G2)
# ---------------------------------------------------------------------------
def _parquet_cache_paths(
    source_sha256: str,
    selector: str = "",
    *,
    cache_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    cdir = Path(cache_dir) if cache_dir is not None else (ensure_dirs()["cache"] / "parquet")
    cdir.mkdir(parents=True, exist_ok=True)
    sel_hash = hashlib.sha256(selector.encode("utf-8")).hexdigest()[:10] if selector else "default"
    stem = f"{source_sha256}_{sel_hash}"
    return cdir / f"{stem}.parquet", cdir / f"{stem}.manifest.json"


def write_parquet_cache(
    df: pd.DataFrame,
    *,
    source_sha256: str,
    selector: str = "",
    metadata: dict[str, Any] | None = None,
    cache_dir: str | Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Atomically write ``df`` to a Snappy-compressed Parquet cache file + SHA-256 manifest."""
    pq_path, mf_path = _parquet_cache_paths(source_sha256, selector, cache_dir=cache_dir)
    fd_pq, tmp_pq_str = tempfile.mkstemp(prefix=".eb_pq_", suffix=".parquet", dir=str(pq_path.parent))
    os.close(fd_pq)
    tmp_pq = Path(tmp_pq_str)
    try:
        df.to_parquet(tmp_pq, engine="pyarrow", compression="snappy", index=False)
        os.replace(tmp_pq, pq_path)
    except Exception:
        tmp_pq.unlink(missing_ok=True)
        raise

    pq_sha = sha256_file(pq_path)
    manifest: dict[str, Any] = {
        "format": "energybaseline.parquet_cache",
        "source_sha256": source_sha256,
        "selector": selector,
        "parquet_sha256": pq_sha,
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "metadata": dict(metadata or {}),
    }
    fd_mf, tmp_mf_str = tempfile.mkstemp(prefix=".eb_pq_mf_", suffix=".json", dir=str(mf_path.parent))
    tmp_mf = Path(tmp_mf_str)
    try:
        with os.fdopen(fd_mf, "wb") as fh:
            fh.write((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_mf, mf_path)
    except Exception:
        tmp_mf.unlink(missing_ok=True)
        raise
    return pq_path, manifest


def read_parquet_cache(
    source_sha256: str,
    *,
    selector: str = "",
    strict: bool = False,
    cache_dir: str | Path | None = None,
) -> pd.DataFrame | None:
    """Read a cached Parquet frame if present and SHA-256 verified; invalidate or raise on tamper."""
    pq_path, mf_path = _parquet_cache_paths(source_sha256, selector, cache_dir=cache_dir)
    if not pq_path.is_file() or not mf_path.is_file():
        return None
    try:
        manifest = json.loads(mf_path.read_text(encoding="utf-8"))
        expected_src = str(manifest.get("source_sha256", "")).lower()
        expected_pq = str(manifest.get("parquet_sha256", "")).lower()
        actual_pq = sha256_file(pq_path).lower()
        if expected_src != source_sha256.lower() or not expected_pq or actual_pq != expected_pq:
            raise IngestError(
                f"Parquet cache SHA-256 mismatch for {pq_path.name}: expected {expected_pq}, got {actual_pq}."
            )
        df = pd.read_parquet(pq_path, engine="pyarrow")
        if int(len(df)) != int(manifest.get("rows", -1)):
            raise IngestError("Parquet cache row-count mismatch against manifest.")
        return df
    except IngestError:
        pq_path.unlink(missing_ok=True)
        mf_path.unlink(missing_ok=True)
        if strict:
            raise
        return None
    except Exception as exc:
        pq_path.unlink(missing_ok=True)
        mf_path.unlink(missing_ok=True)
        if strict:
            raise IngestError(f"Corrupted Parquet cache entry: {exc}") from exc
        return None


# ---------------------------------------------------------------------------
# Streamed CSV / TSV / Excel / Parquet Ingestion (DAT-1, Gate G2)
# ---------------------------------------------------------------------------
def stream_csv_chunks(
    path: str | Path,
    *,
    chunk_rows: int = 50_000,
) -> tuple[Iterator[pd.DataFrame], dict[str, str]]:
    """Stream ``.csv`` / ``.tsv`` / ``.txt`` in bounded chunks with auto-detected encoding, delimiter, and decimal."""
    p = Path(path)
    with p.open("rb") as fh:
        head_bytes = fh.read(65536)
    if not head_bytes.strip():
        raise IngestError(f"File '{p.name}' is empty.")
    encoding = detect_text_encoding(head_bytes)
    sample_text = head_bytes.decode(encoding, errors="replace")
    sep, decimal = detect_csv_dialect(sample_text, p.suffix.lower())
    meta = {"encoding": encoding, "delimiter": sep, "decimal": decimal}

    def _iter() -> Iterator[pd.DataFrame]:
        reader = pd.read_csv(
            p,
            encoding=encoding,
            sep=sep,
            decimal=decimal,
            chunksize=max(1, int(chunk_rows)),
            low_memory=False,
        )
        for chunk in reader:
            chunk.columns = [str(c).strip() for c in chunk.columns]
            yield chunk

    return _iter(), meta


def load_file(
    path: str | Path,
    sheet: str | int | None = None,
    table: str | None = None,
    *,
    use_parquet_cache: bool = True,
    chunk_rows: int = 50_000,
    cache_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Ingest ``.csv``, ``.tsv``, ``.txt``, ``.xlsx``, ``.xlsm``, ``.xls``, ``.parquet``, ``.pq``, or Access DB."""
    p = Path(path)
    ext = p.suffix.lower()
    if not p.is_file():
        raise IngestError(f"File '{p}' does not exist.")

    source_sha = sha256_file(p)
    selector = f"sheet={sheet}|table={table}" if (sheet is not None or table is not None) else ""

    if use_parquet_cache and ext in (".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".xls") and os.environ.get("_EB_IN_WORKER") != "1":
        cached = read_parquet_cache(source_sha, selector=selector, strict=False, cache_dir=cache_dir)
        if cached is not None:
            cached.attrs["ingest_meta"] = {"cache_hit": True, "source_sha256": source_sha}
            return cached

    ingest_meta: dict[str, Any] = {"cache_hit": False, "source_sha256": source_sha, "format": ext.lstrip(".")}
    try:
        if ext in (".csv", ".txt", ".tsv"):
            chunks_iter, csv_meta = stream_csv_chunks(p, chunk_rows=chunk_rows)
            ingest_meta.update(csv_meta)
            chunks = list(chunks_iter)
            df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
        elif ext in (".xlsx", ".xlsm", ".xls"):
            chosen_sheet = select_best_sheet(p) if sheet is None else sheet
            ingest_meta["sheet"] = str(chosen_sheet)
            df = pd.read_excel(p, sheet_name=chosen_sheet)
        elif ext in (".parquet", ".pq"):
            df = pd.read_parquet(p, engine="pyarrow")
        elif ext in (".mdb", ".accdb"):
            tables = list_access_tables(str(p))
            table = table or (tables[0] if tables else None)
            if not table:
                raise IngestError("The Access database contains no tables.")
            ingest_meta["table"] = str(table)
            conn = _access_conn(str(p))
            try:
                df = pd.read_sql(f"SELECT * FROM [{table}]", conn)
            finally:
                conn.close()
        else:
            raise IngestError(
                f"Unsupported file type '{ext}'. Use .csv, .tsv, .txt, .xlsx, .xls, .parquet, .mdb or .accdb."
            )
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"Could not read '{p.name}': {exc}") from exc

    df.columns = [str(c).strip() for c in df.columns]
    df = coerce_numeric(df)

    if use_parquet_cache and ext in (".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".xls") and os.environ.get("_EB_IN_WORKER") != "1":
        try:
            pq_path, pq_manifest = write_parquet_cache(
                df,
                source_sha256=source_sha,
                selector=selector,
                metadata=ingest_meta,
                cache_dir=cache_dir,
            )
            ingest_meta["parquet_cache_path"] = str(pq_path)
            ingest_meta["parquet_cache_sha256"] = pq_manifest["parquet_sha256"]
        except Exception:
            pass

    df.attrs["ingest_meta"] = ingest_meta
    return df


def coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Convert text columns that are >=90 % numeric (including European decimal commas) to floats."""
    df = df.copy()
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_bool_dtype(s):
            if pd.api.types.is_bool_dtype(s):
                df[c] = s.astype(float)
            continue
        txt = s.astype("string").str.strip()
        # Handle European thousands+decimal format like '1.234,56' before simple comma replacement.
        euro_mask = txt.str.match(_EURO_THOUSANDS_RE, na=False)
        cleaned = txt.copy()
        if bool(euro_mask.any()):
            cleaned = cleaned.where(
                ~euro_mask,
                cleaned.str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
            )
        cleaned = cleaned.str.replace(",", ".", regex=False)
        num = pd.to_numeric(cleaned, errors="coerce")
        nn = int(s.notna().sum())
        if nn and int(num.notna().sum()) / nn >= 0.9:
            df[c] = num.astype(float)
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
        score = ok + (0.5 if name_hit else 0.0)
        if ok >= 0.9 and score > best_score:
            best, best_score = c, score
    return best


def data_dictionary(df: pd.DataFrame, time_col: str | None = None) -> pd.DataFrame:
    rows = []
    for c in df.columns:
        s = df[c]
        is_num = pd.api.types.is_numeric_dtype(s)
        role = "timestamp" if c == time_col else ("numeric" if is_num else "categorical/text")
        rows.append(
            {
                "column": c,
                "dtype": str(s.dtype),
                "role": role,
                "missing": int(s.isna().sum()),
                "missing_%": round(100 * float(s.isna().mean()), 2) if len(s) else 0.0,
                "unique": int(s.nunique(dropna=True)),
                "min": float(s.min()) if is_num and s.notna().any() else None,
                "max": float(s.max()) if is_num and s.notna().any() else None,
                "mean": float(s.mean()) if is_num and s.notna().any() else None,
                "std": float(s.std()) if is_num and s.notna().sum() > 1 else None,
            }
        )
    return pd.DataFrame(rows)


def import_report(df: pd.DataFrame, time_col: str | None) -> dict[str, Any]:
    num = df.select_dtypes("number")
    const = [c for c in num.columns if num[c].nunique(dropna=True) <= 1]
    rep: dict[str, Any] = {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "time_column": time_col,
        "duplicate_rows": int(df.duplicated().sum()),
        "columns_with_missing": {c: int(v) for c, v in df.isna().sum().items() if v},
        "constant_columns": const,
        "non_numeric_columns": [c for c in df.columns if c not in num.columns and c != time_col],
        "warnings": [],
    }
    if time_col and time_col in df.columns:
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


def build_ingestion_summary(
    df: pd.DataFrame,
    *,
    source_name: str,
    source_sha256: str,
    time_col: str | None = None,
) -> dict[str, Any]:
    """Build an immutable SHA-256 ingestion summary record for persistence (DAT-8)."""
    meta = dict(df.attrs.get("ingest_meta") or {})
    return {
        "source_name": source_name,
        "source_sha256": source_sha256,
        "dataframe_sha256": sha256_dataframe(df),
        "ingest_meta": meta,
        "report": import_report(df, time_col),
    }


def manual_frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns)
    return coerce_numeric(df.replace("", np.nan))
