"""Temporal configuration, validation and granularity-aware regularisation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .auto import AutoLog

# label -> (pandas freq alias, nominal seconds, steps/day, seasonal period in steps, tier)
GRAN = {
    "1min": ("1min", 60, 1440, 1440, "subdaily"),
    "5min": ("5min", 300, 288, 288, "subdaily"),
    "15min": ("15min", 900, 96, 96, "subdaily"),
    "30min": ("30min", 1800, 48, 48, "subdaily"),
    "hourly": ("h", 3600, 24, 24, "subdaily"),
    "daily": ("D", 86400, 1, 7, "daily"),
    "weekly": ("W", 7 * 86400, 1 / 7, 52, "weekly"),
    "monthly": ("MS", 30.4375 * 86400, 1 / 30.4375, 12, "monthly"),
    "quarterly": ("QS", 91.3125 * 86400, 1 / 91.3125, 4, "monthly"),
    "annual": ("YS", 365.25 * 86400, 1 / 365.25, 1, "monthly"),
}
ENERGY_NAME = re.compile(r"kwh|mwh|energy|consum|usage|volume|m3|therm|gj|btu|steam|water|gas|elec|chilled", re.I)


class TemporalError(ValueError):
    pass


@dataclass
class Granularity:
    label: str
    freq: str
    seconds: float
    steps_per_day: float
    season_steps: int
    tier: str


def granularity(label: str) -> Granularity:
    if label.startswith("custom"):
        sec = float(label.split(":")[1]) if ":" in label else 3600.0
        return Granularity(label, f"{int(sec)}s", sec, 86400 / sec, max(2, int(round(86400 / sec))), "subdaily" if sec < 86400 else "daily")
    f, sec, spd, season, tier = GRAN[label]
    return Granularity(label, f, sec, spd, season, tier)


def label_from_seconds(sec: float) -> str:
    for lab, (_, s, *_rest) in GRAN.items():
        tol = 0.05 * s if s < 86400 * 7 else 0.12 * s
        if abs(sec - s) <= tol:
            return lab
    return f"custom:{int(round(sec))}"


def detect_sampling(idx: pd.DatetimeIndex) -> dict:
    idx = pd.DatetimeIndex(idx).sort_values()
    d = np.diff(idx.values.astype("datetime64[s]").astype("int64")).astype(float)
    d = d[d > 0]
    if len(d) == 0:
        return {"label": "unknown", "median_seconds": float("nan"), "regularity": 0.0, "n_gaps": 0, "max_gap_seconds": 0.0}
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


def parse_timestamps(s: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s)
    out = pd.to_datetime(s, errors="coerce", format="mixed") if s.dtype == object or str(s.dtype).startswith("str") else pd.to_datetime(s, errors="coerce")
    return out


def default_agg_for(col: str, user_agg: str | None) -> str:
    if user_agg:
        return user_agg
    return "sum" if ENERGY_NAME.search(col) and not re.search(r"temp|rate|power|kw\b|humid", col, re.I) else "mean"


def _resample(df: pd.DataFrame, freq: str, aggs: dict[str, str]) -> pd.DataFrame:
    parts = []
    for method in sorted(set(aggs.values())):
        cols = [c for c, m in aggs.items() if m == method]
        rs = df[cols].resample(freq, closed="left", label="left")
        parts.append(rs.sum(min_count=1) if method == "sum" else getattr(rs, method)())
    out = pd.concat(parts, axis=1)[list(aggs)]
    return out


@dataclass
class TemporalReport:
    declared: str
    detected: str
    applied: str
    needs_confirmation: bool
    aggregation: dict
    n_input_rows: int
    n_output_rows: int
    n_duplicates: int
    n_gaps_filled_as_missing: int
    regularity: float
    timezone: str
    dst: str
    baseline_window: tuple
    reporting_window: tuple | None
    messages: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def prepare_temporal(df: pd.DataFrame, time_col: str, auto: AutoLog) -> tuple[pd.DataFrame, TemporalReport, Granularity]:
    """Return a regular, naive-timestamp-indexed frame on the declared (or detected) grid."""
    msgs: list[str] = []
    raw = df.copy()
    ts = parse_timestamps(raw[time_col])
    bad = int(ts.isna().sum())
    if bad:
        msgs.append(f"{bad} rows with unparseable timestamps were excluded (kept in raw data).")
    raw = raw.loc[ts.notna()].copy()
    ts = ts.loc[ts.notna()]
    if len(raw) < 3:
        raise TemporalError("Fewer than 3 valid timestamps - cannot establish a temporal structure.")

    # --- timezone / DST
    tz = auto.decide("timezone", lambda: ("", "timestamps carry no declared timezone", "AUTO keeps naive wall-clock time (no conversion)"))
    dst = "wall_clock"
    if tz:
        dst = auto.decide("dst_handling", lambda: ("wall_clock", f"timezone={tz} declared", "preserve wall-clock time; ambiguous hour handled by inference"))
        tzs = pd.DatetimeIndex(ts)
        if tzs.tz is None:
            amb = "NaT" if dst == "drop_ambiguous" else "infer"
            try:
                tzs = tzs.tz_localize(tz, ambiguous=amb, nonexistent="shift_forward")
            except Exception:
                tzs = tzs.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
                msgs.append("DST ambiguity could not be inferred; ambiguous timestamps dropped.")
        if dst == "utc":
            tzs = tzs.tz_convert("UTC")
        ts = pd.Series(tzs.tz_localize(None), index=ts.index)
        okm = ts.notna()
        raw, ts = raw.loc[okm], ts.loc[okm]
    raw.index = pd.DatetimeIndex(ts.values)
    raw = raw.drop(columns=[time_col]).sort_index()

    # --- duplicates
    dup = int(raw.index.duplicated().sum())
    if dup:
        msgs.append(f"{dup} duplicate timestamps detected; duplicates were averaged (numeric) / first value kept (other).")
        num = raw.select_dtypes("number").columns
        agg = {c: ("mean" if c in num else "first") for c in raw.columns}
        raw = raw.groupby(level=0).agg(agg)

    det = detect_sampling(raw.index)
    from .auto import is_auto
    needs_confirm = is_auto(auto.config.get("sampling_period", "AUTO"))
    decl = auto.decide("sampling_period", lambda: (det["label"], f"median step={det['median_seconds']:.0f}s, regularity={det['regularity']:.1%}", "nearest standard granularity to the median timestamp step"))
    decl = "mixed" if str(decl).lower().startswith(("mixed", "irregular")) else str(decl)
    if decl in ("mixed", "mixed/irregular", "irregular"):
        applied = det["label"]
        msgs.append(f"Mixed/irregular sampling declared; regularised to the median step ({applied}).")
    else:
        applied = decl
    g = granularity(applied)

    # --- validate declared vs detected
    ratio = g.seconds / det["median_seconds"] if det["median_seconds"] == det["median_seconds"] else 1.0
    num_cols = [c for c in raw.columns if pd.api.types.is_numeric_dtype(raw[c])]
    other_cols = [c for c in raw.columns if c not in num_cols]
    if ratio < 0.67 and decl not in ("mixed", "mixed/irregular", "irregular"):
        raise TemporalError(
            f"Data is sampled every ~{det['median_seconds']:.0f}s ({det['label']}) which is COARSER than the declared "
            f"'{decl}'. The application refuses to disaggregate data (it would fabricate information). "
            f"Declare '{det['label']}' or supply finer data.")
    user_agg = auto.config.get("aggregation")
    user_agg = None if user_agg in (None, "", "AUTO", "auto") else str(user_agg)
    aggs = {c: default_agg_for(c, user_agg) for c in num_cols}
    if ratio > 1.5:
        msgs.append(f"Data is finer than declared ('{det['label']}' -> '{applied}'): aggregation required and applied.")
        sample = ", ".join(f"{c}:{m}" for c, m in list(aggs.items())[:6])
        auto.note("aggregation", sample + (" ..." if len(aggs) > 6 else ""),
                  f"finer-than-declared ratio={ratio:.1f}", "sum for energy/volume-like column names, mean for other variables (or user choice)")
    if det.get("n_short", 0) > 0 and ratio <= 1.5:
        msgs.append(f"{det['n_short']} intervals shorter than half the declared granularity were folded into the declared grid by aggregation.")
    out = _resample(raw[num_cols], g.freq, aggs) if num_cols else raw.iloc[:0]
    for c in other_cols:  # categorical/text columns: last value per bin
        out[c] = raw[c].resample(g.freq).last()
    n_missing_rows = int(out[num_cols].isna().all(axis=1).sum()) if num_cols else 0
    if n_missing_rows:
        msgs.append(f"{n_missing_rows} grid intervals contain no data (gaps); they stay missing - never fabricated.")
    if det["regularity"] < 0.9:
        msgs.append(f"Irregular sampling: only {det['regularity']:.0%} of steps equal the median step.")

    # --- windows
    bw = auto.decide("baseline_window", lambda: ("", f"data span {out.index.min()} .. {out.index.max()}", "AUTO uses the entire available range"))
    start, end = out.index.min(), out.index.max()
    if bw:
        try:
            a, b = [x.strip() for x in str(bw).split(",")]
            start, end = pd.Timestamp(a), pd.Timestamp(b)
        except Exception:
            msgs.append(f"Baseline window '{bw}' could not be parsed; entire range used.")
    rw = auto.decide("reporting_window", lambda: ("", "no reporting period declared", "none - whole baseline range used for fitting"))
    rep = None
    if rw:
        try:
            a, b = [x.strip() for x in str(rw).split(",")]
            rep = (pd.Timestamp(a), pd.Timestamp(b))
        except Exception:
            msgs.append(f"Reporting window '{rw}' could not be parsed; ignored.")
    out = out.loc[(out.index >= start) & (out.index <= end)]
    if rep is not None:
        out = out.loc[~((out.index >= rep[0]) & (out.index <= rep[1]))]
    rpt = TemporalReport(
        declared=str(decl), detected=det["label"], applied=applied, needs_confirmation=needs_confirm,
        aggregation=aggs if ratio > 1.5 else {}, n_input_rows=len(df), n_output_rows=len(out), n_duplicates=dup,
        n_gaps_filled_as_missing=n_missing_rows, regularity=det["regularity"], timezone=tz or "naive", dst=dst,
        baseline_window=(str(start), str(end)), reporting_window=(tuple(map(str, rep)) if rep else None), messages=msgs)
    return out, rpt, g
