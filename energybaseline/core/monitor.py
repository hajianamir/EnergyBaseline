"""Prediction & Monitoring: schema validation, baseline prediction, deviation analytics, KPIs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .temporal import _resample, default_agg_for, detect_sampling, granularity, parse_timestamps
from .features import FeatureSpec


class MonitorError(ValueError):
    pass


def needs_time(spec: FeatureSpec) -> bool:
    return bool(spec.calendar or spec.fourier or spec.lags or spec.rolls)


def schema_report(df: pd.DataFrame, bundle: dict, time_col: str | None) -> dict:
    """Missing columns, dtype mismatches and training-range warnings BEFORE prediction."""
    spec: FeatureSpec = bundle["spec"]
    req = list(bundle["required_columns"])
    rep = {"ok": True, "missing_columns": [], "dtype_mismatches": [], "range_warnings": [], "time_issue": "", "extra_columns": [], "rows": len(df)}
    rep["missing_columns"] = [c for c in req if c not in df.columns]
    for c in req:
        if c in df.columns and c in spec.raw_cols:
            num = pd.to_numeric(df[c], errors="coerce")
            bad = int(num.isna().sum() - df[c].isna().sum())
            if bad > 0:
                rep["dtype_mismatches"].append(f"'{c}': {bad} non-numeric values (expected numeric)")
            st = bundle["train_stats"].get(c)
            if st and num.notna().any():
                span = max(st["max"] - st["min"], 1e-9)
                below = int((num < st["min"] - 0.1 * span).sum())
                above = int((num > st["max"] + 0.1 * span).sum())
                if below or above:
                    rep["range_warnings"].append(f"'{c}': {below + above} values outside the training range [{st['min']:.4g}, {st['max']:.4g}] (extrapolation risk)")
    if needs_time(spec):
        if not time_col or time_col not in df.columns:
            rep["time_issue"] = "A timestamp column is required by this baseline (calendar / lag features)."
        else:
            ts = parse_timestamps(df[time_col])
            if ts.isna().all():
                rep["time_issue"] = f"Column '{time_col}' could not be parsed as timestamps."
            elif ts.isna().any():
                rep["time_issue"] = f"{int(ts.isna().sum())} unparseable timestamps will be dropped."
    rep["extra_columns"] = [c for c in df.columns if c not in req and c != time_col and c != bundle["target"]]
    rep["has_actual"] = bundle["target"] in df.columns
    rep["ok"] = not rep["missing_columns"] and not (rep["time_issue"] and "required" in rep["time_issue"] or "could not" in rep["time_issue"])
    return rep


def prepare(df: pd.DataFrame, bundle: dict, time_col: str | None) -> tuple[pd.DataFrame, list[str]]:
    spec: FeatureSpec = bundle["spec"]
    notes: list[str] = []
    cols = [c for c in bundle["required_columns"] if c in df.columns]
    keep = cols + ([bundle["target"]] if bundle["target"] in df.columns else [])
    raw = df[keep + ([time_col] if time_col and time_col in df.columns else [])].copy()
    if time_col and time_col in raw.columns:
        ts = parse_timestamps(raw[time_col])
        raw = raw.loc[ts.notna()].copy()
        raw.index = pd.DatetimeIndex(ts[ts.notna()].values)
        raw = raw.drop(columns=[time_col]).sort_index()
        if raw.index.duplicated().any():
            raw = raw.groupby(level=0).mean(numeric_only=True)
            notes.append("duplicate timestamps averaged")
        det = detect_sampling(raw.index)
        base = granularity(bundle["granularity"])
        if det["median_seconds"] == det["median_seconds"] and det["median_seconds"] < 0.67 * base.seconds:
            aggs = {c: bundle.get("aggregation", {}).get(c) or default_agg_for(c, None) for c in raw.columns}
            raw = _resample(raw.apply(pd.to_numeric, errors="coerce"), base.freq, aggs)
            notes.append(f"input finer ({det['label']}) than the baseline granularity ({base.label}): aggregated")
        elif det["median_seconds"] == det["median_seconds"] and det["median_seconds"] > 1.5 * base.seconds:
            notes.append(f"WARNING: input is coarser ({det['label']}) than the baseline granularity ({base.label}); predictions are not reliable")
    else:
        if needs_time(spec):
            raise MonitorError("This baseline needs timestamps (calendar/lag features).")
        raw = raw.reset_index(drop=True)
        notes.append("no timestamp: time-based features are inactive")
    return raw, notes


def predict_deviation(df: pd.DataFrame, bundle: dict, time_col: str | None = None, alert: dict | None = None) -> pd.DataFrame:
    rep = schema_report(df, bundle, time_col)
    if rep["missing_columns"]:
        raise MonitorError("Missing required columns: " + ", ".join(rep["missing_columns"]))
    raw, notes = prepare(df, bundle, time_col)
    if raw.empty:
        raise MonitorError("No usable rows after preparation.")
    spec: FeatureSpec = bundle["spec"]
    X = spec.build(raw)
    n_imp = int(X.isna().any(axis=1).sum())
    X = X.fillna(pd.Series(spec.medians_)).fillna(0.0)
    pred = bundle["model"].predict(X)
    lo, hi = bundle["resid_q"]
    out = pd.DataFrame({"baseline": pred, "lower": pred + lo, "upper": pred + hi}, index=raw.index)
    tgt = bundle["target"]
    out["actual"] = pd.to_numeric(raw[tgt], errors="coerce") if tgt in raw.columns else np.nan
    out["deviation"] = out["actual"] - out["baseline"]
    out["deviation_pct"] = np.where(np.abs(out["baseline"]) > 1e-9, 100 * out["deviation"] / out["baseline"], np.nan)
    out["cum_deviation"] = out["deviation"].fillna(0).cumsum().where(out["actual"].notna().cummax())
    cumb = out["baseline"].where(out["actual"].notna()).fillna(0).cumsum()
    out["cum_deviation_pct"] = np.where(cumb.abs() > 1e-9, 100 * out["cum_deviation"] / cumb, np.nan)
    out["normalized_deviation"] = out["deviation"] / max(bundle["rmse"], 1e-12)
    a = alert or bundle["alert"]
    mode, val = a["mode"], float(a["value"])
    if mode == "percent":
        ex = out["deviation_pct"].abs() > val
    elif mode == "cvrmse_multiple":
        ex = out["deviation"].abs() > val * bundle["rmse"]
    else:
        ex = (out["actual"] < out["lower"]) | (out["actual"] > out["upper"])
    out["exceedance"] = ex.fillna(False) & out["actual"].notna()
    out["alert"] = np.where(out["exceedance"], np.where(out["deviation"] > 0, "OVER baseline", "UNDER baseline"), "")
    out.attrs.update({"notes": notes, "n_imputed": n_imp, "alert": a, "target": tgt, "schema": rep})
    return out


def kpis(res: pd.DataFrame) -> dict:
    d = res.dropna(subset=["actual"])
    if d.empty:
        return {"rows": len(res), "has_actual": False}
    i = d["deviation"].abs().idxmax()
    return {
        "rows": int(len(d)), "has_actual": True, "total_actual": float(d["actual"].sum()), "total_baseline": float(d["baseline"].sum()),
        "total_deviation": float(d["deviation"].sum()), "total_deviation_pct": float(100 * d["deviation"].sum() / d["baseline"].sum()) if abs(d["baseline"].sum()) > 1e-9 else None,
        "mean_deviation_pct": float(d["deviation_pct"].mean()), "exceedances": int(d["exceedance"].sum()), "exceedance_share_pct": float(100 * d["exceedance"].mean()),
        "max_deviation_at": str(i), "max_deviation_value": float(d.loc[i, "deviation"]), "period": [str(d.index.min()), str(d.index.max())],
    }


def top_deviations(res: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    d = res.dropna(subset=["actual"]).copy()
    d["abs_dev"] = d["deviation"].abs()
    return d.sort_values("abs_dev", ascending=False).head(n).drop(columns=["abs_dev"])


def manual_template(bundle: dict) -> list[dict]:
    """Form definition (auto-generated from the baseline's required predictors)."""
    spec: FeatureSpec = bundle["spec"]
    fields = []
    if needs_time(spec):
        fields.append({"name": "timestamp", "kind": "datetime", "hint": f"timestamp on the {bundle['granularity']} grid"})
    for c in spec.raw_cols:
        st = bundle["train_stats"].get(c, {})
        fields.append({"name": c, "kind": "number", "min": st.get("min"), "max": st.get("max"), "mean": st.get("mean"),
                       "hint": f"training range {st.get('min', float('nan')):.4g} … {st.get('max', float('nan')):.4g}"})
    for c, lv in spec.cat_levels.items():
        fields.append({"name": c, "kind": "choice", "choices": lv, "hint": "category"})
    fields.append({"name": bundle["target"], "kind": "number_optional", "hint": "actual measured value (optional, enables deviation)"})
    return fields
