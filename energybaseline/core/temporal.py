"""Temporal configuration, quantity-type aggregation dispatch, DST normalisation, and gap classification."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import datetime as dt
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from .auto import AutoLog, is_auto
from .schema import (
    AUTO,
    ColumnRole,
    ColumnSpec,
    DatasetSchema,
    QuantityType,
    coerce_dataset_schema,
    suggest_column_spec,
    suggest_dataset_schema,
)

# label -> (pandas freq alias, nominal seconds, steps/day, seasonal period in steps, tier)
GRAN: dict[str, tuple[str, float, float, int, str]] = {
    "1min": ("1min", 60.0, 1440.0, 1440, "subdaily"),
    "5min": ("5min", 300.0, 288.0, 288, "subdaily"),
    "15min": ("15min", 900.0, 96.0, 96, "subdaily"),
    "30min": ("30min", 1800.0, 48.0, 48, "subdaily"),
    "hourly": ("h", 3600.0, 24.0, 24, "subdaily"),
    "daily": ("D", 86400.0, 1.0, 7, "daily"),
    "weekly": ("W", 7 * 86400.0, 1 / 7, 52, "weekly"),
    "monthly": ("MS", 30.4375 * 86400.0, 1 / 30.4375, 12, "monthly"),
    "quarterly": ("QS", 91.3125 * 86400.0, 1 / 91.3125, 4, "monthly"),
    "annual": ("YS", 365.25 * 86400.0, 1 / 365.25, 1, "monthly"),
}


class TemporalError(ValueError):
    """Base error for invalid temporal structure or unsupported granularity transitions."""


class DataRefusedError(TemporalError):
    """Raised with code ``DATA_REFUSED`` when data is coarser than declared granularity (never disaggregate)."""

    code: str = "DATA_REFUSED"

    def __init__(self, message: str) -> None:
        prefix = "" if message.startswith("DATA_REFUSED:") else "DATA_REFUSED: "
        super().__init__(f"{prefix}{message}")


@dataclass
class Granularity:
    label: str
    freq: str
    seconds: float
    steps_per_day: float
    season_steps: int
    tier: str


@dataclass(frozen=True)
class GapSegment:
    """Classified missing-interval segment on the temporal grid (DAT-3, DAT-5)."""

    start: str
    end: str
    duration_seconds: float
    missing_steps: int
    kind: str  # short_gap | medium_gap | extended_shutdown

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "duration_seconds": self.duration_seconds,
            "missing_steps": self.missing_steps,
            "kind": self.kind,
        }


@dataclass
class DstNormalizationResult:
    """Result of normalising timestamps in an explicit IANA timezone into UTC storage."""

    utc_timestamps: pd.Series
    timezone: str
    storage_timezone: str
    display_timezone: str
    n_ambiguous: int
    n_nonexistent: int
    local_day_hour_counts: dict[str, int] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)


def granularity(label: str) -> Granularity:
    if label.startswith("custom"):
        sec = float(label.split(":")[1]) if ":" in label else 3600.0
        return Granularity(
            label,
            f"{int(sec)}s",
            sec,
            86400 / sec,
            max(2, int(round(86400 / sec))),
            "subdaily" if sec < 86400 else "daily",
        )
    if label not in GRAN:
        raise TemporalError(f"Unknown sampling granularity '{label}'.")
    f, sec, spd, season, tier = GRAN[label]
    return Granularity(label, f, sec, spd, season, tier)


def label_from_seconds(sec: float) -> str:
    for lab, (_, s, *_rest) in GRAN.items():
        tol = 0.05 * s if s < 86400 * 7 else 0.12 * s
        if abs(sec - s) <= tol:
            return lab
    return f"custom:{int(round(sec))}"


def detect_sampling(idx: pd.DatetimeIndex) -> dict[str, Any]:
    idx = pd.DatetimeIndex(idx).sort_values()
    d = np.diff(idx.as_unit("s").asi8).astype(float)
    d = d[d > 0]
    if len(d) == 0:
        return {
            "label": "unknown",
            "median_seconds": float("nan"),
            "regularity": 0.0,
            "n_gaps": 0,
            "max_gap_seconds": 0.0,
            "n_short": 0,
        }
    med = float(np.median(d))
    label = label_from_seconds(med)
    tol = 0.05 * med if med < 86400 * 7 else 0.12 * med
    regular = float(np.mean(np.abs(d - med) <= tol))
    return {
        "label": label,
        "median_seconds": med,
        "regularity": regular,
        "n_gaps": int(np.sum(d > med * 1.5)),
        "max_gap_seconds": float(d.max()),
        "n_short": int(np.sum(d < med * 0.5)),
    }


def parse_timestamps(s: pd.Series, *, dayfirst: bool = False) -> pd.Series:
    """Parse ISO-8601 and locale timestamps cleanly without mutating the source Series."""
    if isinstance(s.dtype, pd.DatetimeTZDtype) or pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s, errors="coerce")
    if s.dtype == object or str(s.dtype).startswith("str"):
        try:
            return pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=dayfirst)
        except ValueError:
            return pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=dayfirst, utc=True)
    return pd.to_datetime(s, errors="coerce")


# ---------------------------------------------------------------------------
# Explicit Quantity-Type Aggregation Dispatch (DAT-3, DAT-4; zero getattr/eval/regex)
# ---------------------------------------------------------------------------
QUANTITY_TYPE_DEFAULT_AGG: Mapping[QuantityType, str] = MappingProxyType(
    {
        QuantityType.FLOW: "sum",
        QuantityType.STOCK: "last",
        QuantityType.RATE: "time_weighted",
        QuantityType.QUALITY: "mean",
        QuantityType.INDEX: "mean",
    }
)


def aggregation_for_quantity_type(
    quantity_type: QuantityType | str,
    *,
    quality_agg: str = "mean",
) -> str:
    """Map a ``QuantityType`` deterministically to its physical aggregation operator (DAT-3)."""
    qt = quantity_type if isinstance(quantity_type, QuantityType) else QuantityType(str(quantity_type).strip().lower())
    if qt == QuantityType.QUALITY:
        qa = str(quality_agg or "mean").strip().lower()
        if qa not in ("mean", "median"):
            raise TemporalError(f"Unsupported quality aggregation '{quality_agg}'; expected 'mean' or 'median'.")
        return qa
    return QUANTITY_TYPE_DEFAULT_AGG[qt]


def aggregation_for_spec(spec: ColumnSpec, *, default_quality_agg: str = "mean") -> str:
    """Return the aggregation operator dictated by ``spec.quantity_type`` (DAT-3, DAT-4)."""
    if spec.quantity_type is None:
        return "last"
    q_agg = spec.quality_agg if spec.quality_agg in ("mean", "median") else default_quality_agg
    return aggregation_for_quantity_type(spec.quantity_type, quality_agg=q_agg)


def default_agg_for(
    col: str,
    user_agg: str | None = None,
    spec: ColumnSpec | None = None,
    *,
    default_quality_agg: str = "mean",
) -> str:
    """Resolve aggregation for ``col``: explicit ``ColumnSpec`` overrides ``user_agg`` and suggestions (DAT-4)."""
    if spec is not None and not spec.suggested_by_heuristic and spec.quantity_type is not None:
        return aggregation_for_spec(spec, default_quality_agg=default_quality_agg)
    if user_agg:
        method = str(user_agg).strip().lower()
        if method not in _AGGREGATION_DISPATCH:
            raise TemporalError(f"Unsupported aggregation method '{user_agg}'.")
        return method
    if spec is not None and spec.quantity_type is not None:
        return aggregation_for_spec(spec, default_quality_agg=default_quality_agg)
    suggestion = suggest_column_spec(col)
    return aggregation_for_spec(suggestion, default_quality_agg=default_quality_agg)


def _agg_sum(df_cols: pd.DataFrame, freq: str) -> pd.DataFrame:
    return df_cols.resample(freq, closed="left", label="left").sum(min_count=1)


def _agg_mean(df_cols: pd.DataFrame, freq: str) -> pd.DataFrame:
    return df_cols.resample(freq, closed="left", label="left").mean()


def _agg_median(df_cols: pd.DataFrame, freq: str) -> pd.DataFrame:
    return df_cols.resample(freq, closed="left", label="left").median()


def _agg_last(df_cols: pd.DataFrame, freq: str) -> pd.DataFrame:
    return df_cols.resample(freq, closed="left", label="left").last()


def _agg_first(df_cols: pd.DataFrame, freq: str) -> pd.DataFrame:
    return df_cols.resample(freq, closed="left", label="left").first()


def _agg_max(df_cols: pd.DataFrame, freq: str) -> pd.DataFrame:
    return df_cols.resample(freq, closed="left", label="left").max()


def _agg_min(df_cols: pd.DataFrame, freq: str) -> pd.DataFrame:
    return df_cols.resample(freq, closed="left", label="left").min()


def _agg_time_weighted(df_cols: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Duration-weighted rate integration over each target grid bin `[B_j, B_{j+1})` (DAT-3)."""
    template = df_cols.resample(freq, closed="left", label="left").mean()
    grid = template.index
    if len(grid) == 0:
        return template

    offset = pd.tseries.frequencies.to_offset(freq)
    last_edge = (grid[-1:] + offset)[0]
    edges_idx = grid.append(pd.DatetimeIndex([last_edge]))
    edges_ns = edges_idx.as_unit("ns").asi8
    n_bins = len(grid)

    result_cols: dict[str, np.ndarray] = {}
    for col in df_cols.columns:
        s = df_cols[col].dropna()
        if s.empty:
            result_cols[col] = np.full(n_bins, np.nan, dtype=float)
            continue
        t_ns = s.index.as_unit("ns").asi8
        vals = s.to_numpy(dtype=float, copy=False)
        bins = np.searchsorted(edges_ns, t_ns, side="right") - 1
        bins = np.clip(bins, 0, n_bins - 1)

        next_ns = np.empty_like(t_ns)
        if len(t_ns) > 1:
            same_bin = bins[:-1] == bins[1:]
            next_ns[:-1] = np.where(same_bin, t_ns[1:], edges_ns[bins[:-1] + 1])
        next_ns[-1] = edges_ns[bins[-1] + 1]

        weights = np.maximum((next_ns - t_ns).astype(float), 0.0)
        # If an observation lands exactly on an upper clamp (zero width), fall back to unit weight inside bin.
        zero_mask = weights <= 0.0
        if np.any(zero_mask):
            weights = np.where(zero_mask, 1.0, weights)

        weighted_sum = np.bincount(bins, weights=vals * weights, minlength=n_bins)
        weight_tot = np.bincount(bins, weights=weights, minlength=n_bins)
        with np.errstate(invalid="ignore", divide="ignore"):
            col_out = np.where(weight_tot > 0.0, weighted_sum / weight_tot, np.nan)
        result_cols[col] = col_out

    return pd.DataFrame(result_cols, index=grid)[list(df_cols.columns)]


_AGGREGATION_DISPATCH: Mapping[str, Callable[[pd.DataFrame, str], pd.DataFrame]] = MappingProxyType(
    {
        "sum": _agg_sum,
        "mean": _agg_mean,
        "median": _agg_median,
        "last": _agg_last,
        "first": _agg_first,
        "max": _agg_max,
        "min": _agg_min,
        "time_weighted": _agg_time_weighted,
    }
)


def _resample(df: pd.DataFrame, freq: str, aggs: Mapping[str, str]) -> pd.DataFrame:
    """Resample ``df`` onto ``freq`` using compile-time immutable ``_AGGREGATION_DISPATCH``."""
    if not aggs or df.empty:
        return df.iloc[:0].copy()
    parts: list[pd.DataFrame] = []
    for method in sorted(set(aggs.values())):
        handler = _AGGREGATION_DISPATCH.get(method)
        if handler is None:
            raise TemporalError(f"Unsupported aggregation method '{method}'.")
        cols = [c for c, m in aggs.items() if m == method]
        parts.append(handler(df[cols], freq))
    out = pd.concat(parts, axis=1)[list(aggs)]
    return out


# ---------------------------------------------------------------------------
# Explicit Multi-Zone DST Normalisation: UTC Stored / Local Displayed (DAT-2, Gate G2)
# ---------------------------------------------------------------------------
def _has_iso_tz_offset(text: str) -> bool:
    t = text.strip()
    if len(t) < 10:
        return False
    if t.endswith(("Z", "z")):
        return True
    for width, colon_pos in ((6, -3), (5, None)):
        if len(t) > width + 8 and t[-width] in ("+", "-"):
            tail = t[-width + 1 :]
            if colon_pos is not None:
                if len(tail) == 5 and tail[2] == ":" and tail[:2].isdigit() and tail[3:].isdigit():
                    return True
            elif len(tail) == 4 and tail.isdigit():
                return True
    return False


def _validate_iana_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise TemporalError(f"Invalid IANA timezone '{tz_name}': {exc}") from exc


def normalize_timestamps_utc(
    s: pd.Series | pd.DatetimeIndex,
    tz_name: str,
    *,
    dst_handling: str = "utc",
    nonexistent_policy: str = "drop",
) -> DstNormalizationResult:
    """Normalize local or offset-bearing timestamps into UTC storage while auditing DST transitions.

    - Stores UTC timestamps internally (naive UTC ``Series`` representing exact UTC instants).
    - Disambiguates repeated fall-back wall-clock hours (`fold=0` pre-transition, `fold=1` post-transition)
      or drops them when ``dst_handling == 'drop_ambiguous'``.
    - Detects nonexistent spring-forward wall-clock hours and drops (`NaT`) or shifts them forward.
    - Records per-local-day distinct UTC hour counts so 23-hour spring-forward days, 25-hour fall-back days,
      and 24-hour no-DST zones are explicitly auditable.
    """
    zi = _validate_iana_tz(tz_name)
    series_in = pd.Series(s) if not isinstance(s, pd.Series) else s.copy()
    msgs: list[str] = []

    # Check if raw inputs already carry explicit UTC offsets (e.g. ISO-8601 strings with +02:00 / -05:00 / Z).
    if isinstance(series_in.dtype, pd.DatetimeTZDtype):
        utc_aware = series_in.dt.tz_convert("UTC")
        local_aware = utc_aware.dt.tz_convert(zi)
        day_counts = _local_day_hour_counts(local_aware)
        return DstNormalizationResult(
            utc_timestamps=utc_aware.dt.tz_localize(None),
            timezone=tz_name,
            storage_timezone="UTC",
            display_timezone=tz_name,
            n_ambiguous=0,
            n_nonexistent=0,
            local_day_hour_counts=day_counts,
            messages=msgs,
        )

    # Probe string inputs for explicit timezone offsets so ISO-8601 offset strings preserve exact instants.
    if series_in.dtype == object or str(series_in.dtype).startswith("str"):
        non_null_str = series_in.dropna().astype(str).str.strip()
        if not non_null_str.empty and all(_has_iso_tz_offset(v) for v in non_null_str):
            utc_parsed = pd.to_datetime(series_in, errors="coerce", utc=True, format="mixed")
            local_aware = utc_parsed.dt.tz_convert(zi)
            day_counts = _local_day_hour_counts(local_aware)
            return DstNormalizationResult(
                utc_timestamps=utc_parsed.dt.tz_localize(None),
                timezone=tz_name,
                storage_timezone="UTC",
                display_timezone=tz_name,
                n_ambiguous=0,
                n_nonexistent=0,
                local_day_hour_counts=day_counts,
                messages=msgs,
            )

    parsed_naive = parse_timestamps(series_in)
    if isinstance(parsed_naive.dtype, pd.DatetimeTZDtype):
        utc_aware = parsed_naive.dt.tz_convert("UTC")
        local_aware = utc_aware.dt.tz_convert(zi)
        return DstNormalizationResult(
            utc_timestamps=utc_aware.dt.tz_localize(None),
            timezone=tz_name,
            storage_timezone="UTC",
            display_timezone=tz_name,
            n_ambiguous=0,
            n_nonexistent=0,
            local_day_hour_counts=_local_day_hour_counts(local_aware),
            messages=msgs,
        )

    # Fast vectorized probe via pandas tz_localize to check whether ANY row touches a DST transition.
    valid_mask = parsed_naive.notna()
    valid_idx = pd.DatetimeIndex(parsed_naive.loc[valid_mask])
    if len(valid_idx) == 0:
        return DstNormalizationResult(
            utc_timestamps=pd.Series(pd.NaT, index=series_in.index, dtype="datetime64[ns]"),
            timezone=tz_name,
            storage_timezone="UTC",
            display_timezone=tz_name,
            n_ambiguous=0,
            n_nonexistent=0,
            local_day_hour_counts={},
            messages=msgs,
        )

    probe_amb_nat = valid_idx.tz_localize(zi, ambiguous="NaT", nonexistent="NaT")
    has_transition_rows = bool(probe_amb_nat.isna().any())

    if not has_transition_rows:
        utc_idx = probe_amb_nat.tz_convert("UTC")
        local_idx = utc_idx.tz_convert(zi)
        out_s = pd.Series(pd.NaT, index=series_in.index, dtype="datetime64[ns]")
        out_s.loc[valid_mask] = utc_idx.tz_localize(None).as_unit("ns").values
        return DstNormalizationResult(
            utc_timestamps=out_s,
            timezone=tz_name,
            storage_timezone="UTC",
            display_timezone=tz_name,
            n_ambiguous=0,
            n_nonexistent=0,
            local_day_hour_counts=_local_day_hour_counts(pd.Series(local_idx)),
            messages=msgs,
        )

    # Exact PEP-495 per-row transition classification on rows touching spring-forward / fall-back windows.
    n_ambiguous = 0
    n_nonexistent = 0
    utc_values: list[pd.Timestamp] = []
    local_aware_list: list[dt.datetime] = []

    in_ambiguous_fold1 = False
    prev_amb_naive: dt.datetime | None = None
    seen_amb_fold0: set[dt.datetime] = set()

    py_datetimes = valid_idx.to_pydatetime()
    for naive_dt in py_datetimes:
        d0 = naive_dt.replace(tzinfo=zi, fold=0)
        d1 = naive_dt.replace(tzinfo=zi, fold=1)
        u0 = d0.astimezone(dt.timezone.utc)
        rt0 = u0.astimezone(zi).replace(tzinfo=None)

        if rt0 != naive_dt:
            # Spring-forward nonexistent wall-clock timestamp.
            n_nonexistent += 1
            if nonexistent_policy == "shift_forward":
                # Shift forward to the exact transition instant in UTC.
                u1 = d1.astimezone(dt.timezone.utc)
                chosen_utc = max(u0, u1)
                utc_values.append(pd.Timestamp(chosen_utc).tz_localize(None))
                local_aware_list.append(chosen_utc.astimezone(zi))
            else:
                utc_values.append(pd.NaT)
            continue

        if d0.utcoffset() != d1.utcoffset():
            # Fall-back ambiguous wall-clock timestamp.
            n_ambiguous += 1
            if dst_handling == "drop_ambiguous":
                utc_values.append(pd.NaT)
                continue
            if prev_amb_naive is not None and naive_dt < prev_amb_naive:
                in_ambiguous_fold1 = True
            elif naive_dt in seen_amb_fold0:
                in_ambiguous_fold1 = True

            fold = 1 if in_ambiguous_fold1 else 0
            if fold == 0:
                seen_amb_fold0.add(naive_dt)
            prev_amb_naive = naive_dt
            chosen = d1 if fold == 1 else d0
            chosen_utc = chosen.astimezone(dt.timezone.utc)
            utc_values.append(pd.Timestamp(chosen_utc).tz_localize(None))
            local_aware_list.append(chosen_utc.astimezone(zi))
        else:
            in_ambiguous_fold1 = False
            prev_amb_naive = None
            utc_values.append(pd.Timestamp(u0).tz_localize(None))
            local_aware_list.append(u0.astimezone(zi))

    if n_nonexistent:
        action_desc = (
            "shifted forward to transition boundary"
            if nonexistent_policy == "shift_forward"
            else "excluded as NaT (never fabricated)"
        )
        msgs.append(
            f"DST spring-forward ({tz_name}): {n_nonexistent} nonexistent local wall-clock timestamp(s) {action_desc}."
        )
    if n_ambiguous:
        amb_desc = (
            "dropped per dst_handling='drop_ambiguous'"
            if dst_handling == "drop_ambiguous"
            else "disambiguated chronologically into distinct UTC instants"
        )
        msgs.append(f"DST fall-back ({tz_name}): {n_ambiguous} ambiguous local timestamp(s) {amb_desc}.")

    out_s = pd.Series(pd.NaT, index=series_in.index, dtype="datetime64[ns]")
    out_s.loc[valid_mask] = pd.DatetimeIndex(utc_values).as_unit("ns").values
    day_counts = _local_day_hour_counts_from_py(local_aware_list)
    return DstNormalizationResult(
        utc_timestamps=out_s,
        timezone=tz_name,
        storage_timezone="UTC",
        display_timezone=tz_name,
        n_ambiguous=n_ambiguous,
        n_nonexistent=n_nonexistent,
        local_day_hour_counts=day_counts,
        messages=msgs,
    )


def _local_day_hour_counts(local_series: pd.Series) -> dict[str, int]:
    valid = local_series.dropna()
    if valid.empty:
        return {}
    utc_hours = valid.dt.tz_convert("UTC").dt.floor("h")
    local_dates = valid.dt.strftime("%Y-%m-%d")
    grouped = pd.DataFrame({"d": local_dates, "uh": utc_hours}).groupby("d")["uh"].nunique()
    return {str(k): int(v) for k, v in grouped.items()}


def _local_day_hour_counts_from_py(local_dts: list[dt.datetime]) -> dict[str, int]:
    if not local_dts:
        return {}
    buckets: dict[str, set[tuple[int, int, int, int]]] = {}
    for d in local_dts:
        u = d.astimezone(dt.timezone.utc)
        day_key = d.strftime("%Y-%m-%d")
        buckets.setdefault(day_key, set()).add((u.year, u.month, u.day, u.hour))
    return {k: len(v) for k, v in sorted(buckets.items())}


def to_local_index(utc_index: pd.DatetimeIndex, tz_name: str) -> pd.DatetimeIndex:
    """Convert stored naive-UTC ``DatetimeIndex`` to timezone-aware local ``DatetimeIndex`` for display."""
    if not tz_name or tz_name.lower() == "naive":
        return pd.DatetimeIndex(utc_index)
    zi = _validate_iana_tz(tz_name)
    idx = pd.DatetimeIndex(utc_index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(zi)


def format_local_timestamps(utc_index: pd.DatetimeIndex, tz_name: str) -> list[str]:
    """Render stored UTC timestamps as ISO-8601 strings in ``tz_name`` with local UTC offset."""
    loc = to_local_index(utc_index, tz_name)
    return [ts.isoformat() for ts in loc]


# ---------------------------------------------------------------------------
# Gap Classification: short_gap vs medium_gap vs extended_shutdown (DAT-3, DAT-5)
# ---------------------------------------------------------------------------
def classify_gaps(
    index: pd.DatetimeIndex,
    nominal_seconds: float,
    *,
    short_gap_max_steps: int = 3,
    shutdown_min_steps: int = 24,
) -> list[GapSegment]:
    """Classify missing timestamp intervals into short gaps vs extended shutdowns (DAT-3, DAT-5)."""
    if len(index) < 2 or not np.isfinite(nominal_seconds) or nominal_seconds <= 0:
        return []
    sorted_idx = pd.DatetimeIndex(index).sort_values().unique()
    if len(sorted_idx) < 2:
        return []
    sec = sorted_idx.as_unit("s").asi8
    diffs = np.diff(sec).astype(float)
    segments: list[GapSegment] = []
    for i, delta in enumerate(diffs):
        if delta <= nominal_seconds * 1.5:
            continue
        missing_steps = max(1, int(round(delta / nominal_seconds)) - 1)
        if missing_steps <= short_gap_max_steps:
            kind = "short_gap"
        elif missing_steps >= shutdown_min_steps:
            kind = "extended_shutdown"
        else:
            kind = "medium_gap"
        segments.append(
            GapSegment(
                start=str(sorted_idx[i]),
                end=str(sorted_idx[i + 1]),
                duration_seconds=float(delta),
                missing_steps=missing_steps,
                kind=kind,
            )
        )
    return segments


@dataclass
class TemporalReport:
    declared: str
    detected: str
    applied: str
    needs_confirmation: bool
    aggregation: dict[str, str]
    n_input_rows: int
    n_output_rows: int
    n_duplicates: int
    n_gaps_filled_as_missing: int
    regularity: float
    timezone: str
    dst: str
    baseline_window: tuple[str, str]
    reporting_window: tuple[str, str] | None
    messages: list[str] = field(default_factory=list)
    storage_timezone: str = "naive"
    display_timezone: str = "naive"
    n_dst_ambiguous: int = 0
    n_dst_nonexistent: int = 0
    local_day_hour_counts: dict[str, int] = field(default_factory=dict)
    gap_segments: list[dict[str, Any]] = field(default_factory=list)
    n_short_gaps: int = 0
    n_extended_shutdowns: int = 0
    quantity_types: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _resample_in_local_calendar(
    raw_num: pd.DataFrame,
    freq: str,
    aggs: Mapping[str, str],
    tz_name: str,
) -> pd.DataFrame:
    """Resample UTC-indexed sub-daily frame on local calendar boundaries in ``tz_name``, returning UTC index."""
    if not tz_name or raw_num.empty:
        return _resample(raw_num, freq, aggs)
    zi = _validate_iana_tz(tz_name)
    local_df = raw_num.copy()
    local_df.index = pd.DatetimeIndex(local_df.index).tz_localize("UTC").tz_convert(zi)
    resampled_local = _resample(local_df, freq, aggs)
    resampled_local.index = pd.DatetimeIndex(resampled_local.index).tz_convert("UTC").tz_localize(None)
    return resampled_local


def prepare_temporal(
    df: pd.DataFrame,
    time_col: str | None,
    auto: AutoLog,
    *,
    schema: DatasetSchema | Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, TemporalReport, Granularity]:
    """Return a regular timestamp-indexed frame on the declared (or detected) grid.

    - Uses ``DatasetSchema`` column ``QuantityType`` to dispatch aggregation (`flow`->sum, `stock`->last,
      `rate`->time_weighted, `quality`->mean/median, `index`->mean).
    - Demotes column-name heuristics to a logged suggestion only when no explicit ``ColumnSpec`` is supplied.
    - Raises ``DataRefusedError`` (`DATA_REFUSED`) if input data is coarser than declared granularity.
    - When an IANA timezone is declared, normalizes into UTC storage (`storage_timezone='UTC'`) while
      preserving local calendar day aggregation across 23-hour / 25-hour DST transitions.
    """
    msgs: list[str] = []
    raw = df.copy()

    resolved_schema = coerce_dataset_schema(schema or auto.config.get("dataset_schema"))
    effective_time_col = time_col or (resolved_schema.time_column if resolved_schema is not None else None)
    if not effective_time_col or effective_time_col not in raw.columns:
        raise TemporalError(f"Timestamp column {effective_time_col!r} not found in DataFrame.")

    if resolved_schema is None:
        resolved_schema = suggest_dataset_schema(raw, time_col=effective_time_col, auto=auto)

    # Drop explicitly ignored columns if not needed downstream, while keeping targets/predictors/etc.
    ignored_cols = [c for c in resolved_schema.ignored_columns if c in raw.columns and c != effective_time_col]
    if ignored_cols:
        raw = raw.drop(columns=ignored_cols)
        msgs.append(f"Ignored {len(ignored_cols)} column(s) per DatasetSchema role='ignore': {ignored_cols}.")

    raw_time_series = raw[effective_time_col]
    ts_probe = parse_timestamps(raw_time_series)
    bad = int(ts_probe.isna().sum())
    if bad:
        msgs.append(f"{bad} rows with unparseable timestamps were excluded (kept in raw data).")
    valid_rows = ts_probe.notna()
    raw = raw.loc[valid_rows].copy()
    raw_time_series = raw_time_series.loc[valid_rows]
    if len(raw) < 3:
        raise TemporalError("Fewer than 3 valid timestamps - cannot establish a temporal structure.")

    # --- timezone / DST
    schema_tz = resolved_schema.timezone if resolved_schema is not None else ""
    tz_cfg = auto.config.get("timezone", AUTO)
    if is_auto(tz_cfg) and schema_tz:
        tz = auto.decide(
            "timezone",
            lambda: (
                schema_tz,
                f"DatasetSchema.timezone='{schema_tz}'",
                "explicit DatasetSchema timezone normalises timestamps to UTC storage with local display",
            ),
        )
    else:
        tz = auto.decide(
            "timezone",
            lambda: ("", "timestamps carry no declared timezone", "AUTO keeps naive wall-clock time (no conversion)"),
        )
    tz = str(tz or "").strip()

    dst = "naive"
    storage_tz = "naive"
    display_tz = "naive"
    n_dst_amb = 0
    n_dst_nonex = 0
    day_hour_counts: dict[str, int] = {}

    if tz:
        dst = str(
            auto.decide(
                "dst_handling",
                lambda: (
                    "utc",
                    f"timezone={tz} declared",
                    "store UTC internally and display local wall-clock time across 23h/25h DST transitions",
                ),
            )
        )
        norm = normalize_timestamps_utc(
            raw_time_series,
            tz,
            dst_handling=dst,
            nonexistent_policy="drop",
        )
        msgs.extend(norm.messages)
        ts_clean = norm.utc_timestamps
        okm = ts_clean.notna()
        raw = raw.loc[okm].copy()
        ts_clean = ts_clean.loc[okm]
        storage_tz = norm.storage_timezone
        display_tz = norm.display_timezone
        n_dst_amb = norm.n_ambiguous
        n_dst_nonex = norm.n_nonexistent
        day_hour_counts = norm.local_day_hour_counts
    else:
        ts_parsed = parse_timestamps(raw_time_series)
        if isinstance(ts_parsed.dtype, pd.DatetimeTZDtype):
            ts_clean = ts_parsed.dt.tz_convert("UTC").dt.tz_localize(None)
            storage_tz = "UTC"
            display_tz = "UTC"
        else:
            ts_clean = ts_parsed

    if len(raw) < 3:
        raise TemporalError("Fewer than 3 valid timestamps remain after DST / timestamp normalisation.")

    raw.index = pd.DatetimeIndex(ts_clean.values)
    raw = raw.drop(columns=[effective_time_col]).sort_index()

    # --- true duplicates (after DST fall-back disambiguation)
    dup = int(raw.index.duplicated().sum())
    if dup:
        num = set(raw.select_dtypes("number").columns)
        dedup_agg: dict[str, str] = {}
        for c in raw.columns:
            if c not in num:
                dedup_agg[c] = "first"
            else:
                spec_c = resolved_schema.get(c)
                if (
                    spec_c is not None
                    and not spec_c.suggested_by_heuristic
                    and spec_c.quantity_type == QuantityType.STOCK
                ):
                    dedup_agg[c] = "last"
                else:
                    dedup_agg[c] = "mean"
        msgs.append(
            f"{dup} duplicate timestamps detected; duplicates resolved deterministically without silent row loss."
        )
        raw = raw.groupby(level=0).agg(dedup_agg)

    det = detect_sampling(raw.index)
    gap_objs = classify_gaps(raw.index, det["median_seconds"])
    n_short_gaps = sum(1 for gp in gap_objs if gp.kind == "short_gap")
    n_shutdowns = sum(1 for gp in gap_objs if gp.kind == "extended_shutdown")
    if n_shutdowns:
        msgs.append(
            f"{n_shutdowns} extended shutdown gap(s) classified (>=24 steps); targets and cross-shutdown values are never fabricated."
        )

    needs_confirm = is_auto(auto.config.get("sampling_period", AUTO))
    decl = auto.decide(
        "sampling_period",
        lambda: (
            det["label"],
            f"median step={det['median_seconds']:.0f}s, regularity={det['regularity']:.1%}",
            "nearest standard granularity to the median timestamp step",
        ),
    )
    decl = "mixed" if str(decl).lower().startswith(("mixed", "irregular")) else str(decl)
    if decl in ("mixed", "mixed/irregular", "irregular"):
        applied = det["label"]
        msgs.append(f"Mixed/irregular sampling declared; regularised to the median step ({applied}).")
    else:
        applied = decl
    g = granularity(applied)

    # --- validate declared vs detected (DATA_REFUSED on coarser data)
    ratio = g.seconds / det["median_seconds"] if det["median_seconds"] == det["median_seconds"] else 1.0
    num_cols = [c for c in raw.columns if pd.api.types.is_numeric_dtype(raw[c])]
    other_cols = [c for c in raw.columns if c not in num_cols]
    if ratio < 0.67 and decl not in ("mixed", "mixed/irregular", "irregular"):
        raise DataRefusedError(
            f"Data is sampled every ~{det['median_seconds']:.0f}s ({det['label']}) which is COARSER than the declared "
            f"'{decl}' ({g.seconds:.0f}s). The application refuses to disaggregate data (it would fabricate information). "
            f"Declare '{det['label']}' or supply finer data."
        )

    user_agg_cfg = auto.config.get("aggregation")
    user_agg = None if is_auto(user_agg_cfg) else str(user_agg_cfg).strip().lower()
    qual_agg_cfg = auto.config.get("quality_aggregation")
    qual_agg = "mean" if is_auto(qual_agg_cfg) else str(qual_agg_cfg).strip().lower()

    aggs: dict[str, str] = {}
    qtypes_used: dict[str, str] = {}
    for c in num_cols:
        spec_c = resolved_schema.get(c)
        aggs[c] = default_agg_for(c, user_agg, spec=spec_c, default_quality_agg=qual_agg)
        if spec_c is not None and spec_c.quantity_type is not None:
            qtypes_used[c] = spec_c.quantity_type.value
        else:
            qtypes_used[c] = suggest_column_spec(c).quantity_type.value  # type: ignore[union-attr]

    if ratio > 1.5:
        msgs.append(f"Data is finer than declared ('{det['label']}' -> '{applied}'): aggregation required and applied.")
        sample = ", ".join(f"{c}({qtypes_used[c]})->{m}" for c, m in list(aggs.items())[:6])
        auto.note(
            "aggregation",
            sample + (" ..." if len(aggs) > 6 else ""),
            f"finer-than-declared ratio={ratio:.1f}",
            "dispatched strictly by ColumnSpec.quantity_type (flow->sum, stock->last, rate->time_weighted, quality->mean/median, index->mean)",
        )
    if det.get("n_short", 0) > 0 and ratio <= 1.5:
        msgs.append(
            f"{det['n_short']} intervals shorter than half the declared granularity were folded into the declared grid by aggregation."
        )

    use_local_calendar = bool(tz and g.seconds >= 86400.0 and det["median_seconds"] < 86400.0)
    if num_cols:
        if use_local_calendar:
            out = _resample_in_local_calendar(raw[num_cols], g.freq, aggs, tz)
        else:
            out = _resample(raw[num_cols], g.freq, aggs)
    else:
        out = raw.iloc[:0].copy()

    for c in other_cols:
        if use_local_calendar and tz:
            zi = _validate_iana_tz(tz)
            s_loc = raw[c].copy()
            s_loc.index = pd.DatetimeIndex(s_loc.index).tz_localize("UTC").tz_convert(zi)
            rs_cat = s_loc.resample(g.freq, closed="left", label="left").last()
            rs_cat.index = pd.DatetimeIndex(rs_cat.index).tz_convert("UTC").tz_localize(None)
            out[c] = rs_cat
        else:
            out[c] = raw[c].resample(g.freq, closed="left", label="left").last()

    n_missing_rows = int(out[num_cols].isna().all(axis=1).sum()) if num_cols else 0
    if n_missing_rows:
        msgs.append(f"{n_missing_rows} grid intervals contain no data (gaps); they stay missing - never fabricated.")
    if det["regularity"] < 0.9:
        msgs.append(f"Irregular sampling: only {det['regularity']:.0%} of steps equal the median step.")

    # --- windows
    bw = auto.decide(
        "baseline_window",
        lambda: ("", f"data span {out.index.min()} .. {out.index.max()}", "AUTO uses the entire available range"),
    )
    start, end = out.index.min(), out.index.max()
    if bw:
        try:
            a, b = [x.strip() for x in str(bw).split(",")]
            if tz:
                norm_bounds = normalize_timestamps_utc(pd.Series([a, b]), tz, dst_handling="utc")
                start, end = (
                    pd.Timestamp(norm_bounds.utc_timestamps.iloc[0]),
                    pd.Timestamp(norm_bounds.utc_timestamps.iloc[1]),
                )
            else:
                start, end = pd.Timestamp(a), pd.Timestamp(b)
        except Exception:
            msgs.append(f"Baseline window '{bw}' could not be parsed; entire range used.")

    rw = auto.decide(
        "reporting_window",
        lambda: ("", "no reporting period declared", "none - whole baseline range used for fitting"),
    )
    rep: tuple[pd.Timestamp, pd.Timestamp] | None = None
    if rw:
        try:
            a, b = [x.strip() for x in str(rw).split(",")]
            if tz:
                norm_rep = normalize_timestamps_utc(pd.Series([a, b]), tz, dst_handling="utc")
                rep = (pd.Timestamp(norm_rep.utc_timestamps.iloc[0]), pd.Timestamp(norm_rep.utc_timestamps.iloc[1]))
            else:
                rep = (pd.Timestamp(a), pd.Timestamp(b))
        except Exception:
            msgs.append(f"Reporting window '{rw}' could not be parsed; ignored.")

    out = out.loc[(out.index >= start) & (out.index <= end)].copy()
    if rep is not None:
        out = out.loc[~((out.index >= rep[0]) & (out.index <= rep[1]))].copy()

    out.attrs["storage_timezone"] = storage_tz
    out.attrs["display_timezone"] = display_tz
    if resolved_schema is not None:
        out.attrs["schema_sha256"] = resolved_schema.compute_sha256()

    rpt = TemporalReport(
        declared=str(decl),
        detected=det["label"],
        applied=applied,
        needs_confirmation=needs_confirm,
        aggregation=aggs if ratio > 1.5 else {},
        n_input_rows=len(df),
        n_output_rows=len(out),
        n_duplicates=dup,
        n_gaps_filled_as_missing=n_missing_rows,
        regularity=det["regularity"],
        timezone=tz or "naive",
        dst=dst,
        baseline_window=(str(start), str(end)),
        reporting_window=((str(rep[0]), str(rep[1])) if rep else None),
        messages=msgs,
        storage_timezone=storage_tz,
        display_timezone=display_tz,
        n_dst_ambiguous=n_dst_amb,
        n_dst_nonexistent=n_dst_nonex,
        local_day_hour_counts=day_hour_counts,
        gap_segments=[gp.as_dict() for gp in gap_objs],
        n_short_gaps=n_short_gaps,
        n_extended_shutdowns=n_shutdowns,
        quantity_types=qtypes_used,
    )
    return out, rpt, g


__all__ = [
    "ColumnRole",
    "DataRefusedError",
    "DstNormalizationResult",
    "GRAN",
    "GapSegment",
    "Granularity",
    "QUANTITY_TYPE_DEFAULT_AGG",
    "TemporalError",
    "TemporalReport",
    "_resample",
    "aggregation_for_quantity_type",
    "aggregation_for_spec",
    "classify_gaps",
    "default_agg_for",
    "detect_sampling",
    "format_local_timestamps",
    "granularity",
    "label_from_seconds",
    "normalize_timestamps_utc",
    "parse_timestamps",
    "prepare_temporal",
    "to_local_index",
]
