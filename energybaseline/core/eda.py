"""Exploratory & engineering analysis."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .features import find_temperature_col, fit_balance_points
from .temporal import Granularity


def change_points(x: np.ndarray, max_cp: int = 4, min_size: int = 10) -> tuple[list[int], str]:
    """PELT via ``ruptures`` when installed, otherwise a CUSUM binary segmentation (own implementation)."""
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 4 * min_size:
        return [], "too short"
    try:
        import ruptures as rpt
        pen = 3 * np.log(len(x)) * np.var(x)
        cps = rpt.Pelt(model="l2", min_size=min_size).fit(x).predict(pen=pen)[:-1]
        return [int(c) for c in cps[:max_cp * 2]], "ruptures.Pelt(l2)"
    except Exception:
        pass
    segs = [(0, len(x))]
    cps: list[int] = []
    for _ in range(max_cp):
        best = (0.0, None, None)
        for s, e in segs:
            seg = x[s:e]
            if len(seg) < 2 * min_size:
                continue
            c = np.cumsum(seg - seg.mean())
            k = int(np.argmax(np.abs(c[min_size:-min_size]))) + min_size if len(seg) > 2 * min_size else None
            if k is None:
                continue
            a, b = seg[:k], seg[k:]
            t = abs(a.mean() - b.mean()) / (np.sqrt(a.var() / len(a) + b.var() / len(b)) + 1e-12)
            if t > best[0]:
                best = (t, (s, e), s + k)
        if best[1] is None or best[0] < 6:
            break
        segs.remove(best[1])
        segs += [(best[1][0], best[2]), (best[2], best[1][1])]
        cps.append(int(best[2]))
    return sorted(cps), "binary segmentation (CUSUM + Welch t > 6)"


def run_eda(df: pd.DataFrame, target: str, predictors: list[str], g: Granularity) -> dict:
    y = df[target]
    out: dict = {}
    num = df[[target] + predictors]
    desc = num.describe().T
    desc["cv_%"] = 100 * desc["std"] / desc["mean"].abs().replace(0, np.nan)
    desc["skew"] = [float(stats.skew(num[c].dropna())) if num[c].notna().sum() > 2 else np.nan for c in num]
    desc["kurtosis"] = [float(stats.kurtosis(num[c].dropna())) if num[c].notna().sum() > 3 else np.nan for c in num]
    out["descriptive"] = desc.round(4).reset_index().rename(columns={"index": "variable"})
    yv = y.dropna()
    sample = yv.sample(min(len(yv), 4000), random_state=0) if len(yv) > 4000 else yv
    sh = stats.shapiro(sample) if 3 <= len(sample) <= 5000 else None
    out["distribution"] = {"skew": float(stats.skew(yv)), "kurtosis": float(stats.kurtosis(yv)),
                           "shapiro_p": float(sh.pvalue) if sh else None, "normal_at_5pct": bool(sh and sh.pvalue > 0.05),
                           "load_factor": float(yv.mean() / yv.max()) if yv.max() > 0 else None,
                           "cv_percent": float(100 * yv.std() / abs(yv.mean())) if yv.mean() else None}
    rows = []
    for c in predictors:
        pair = df[[c, target]].dropna()
        if len(pair) < 5 or pair[c].nunique() < 2:
            continue
        pr = pair[c].corr(pair[target])
        sp = pair[c].corr(pair[target], method="spearman")
        rows.append({"variable": c, "pearson": pr, "spearman": sp, "abs_rank_score": max(abs(pr), abs(sp))})
    corr = pd.DataFrame(rows).sort_values("abs_rank_score", ascending=False) if rows else pd.DataFrame(columns=["variable", "pearson", "spearman", "abs_rank_score"])
    if len(corr) >= 1:
        try:
            from sklearn.feature_selection import mutual_info_regression
            sub = df[predictors + [target]].dropna()
            sub = sub.iloc[:: max(1, len(sub) // 3000)]
            mi = mutual_info_regression(sub[corr["variable"].tolist()].values, sub[target].values, random_state=0)
            corr["mutual_info"] = mi
        except Exception:
            pass
    out["relationships"] = corr.round(4)
    # trend
    t = np.arange(len(yv))
    if len(yv) > 10:
        sl = stats.linregress(t, yv.values)
        tau = stats.kendalltau(t, yv.values)
        per_year = sl.slope * (365.25 * g.steps_per_day)
        out["trend"] = {"slope_per_step": float(sl.slope), "slope_per_year": float(per_year), "slope_pct_of_mean_per_year": float(100 * per_year / abs(yv.mean())) if yv.mean() else None,
                        "kendall_tau": float(tau.statistic), "kendall_p": float(tau.pvalue), "significant_trend": bool(tau.pvalue < 0.05)}
    # profiles
    prof = {}
    if g.tier == "subdaily":
        d = pd.DataFrame({"y": y, "h": y.index.hour, "wk": np.where(y.index.day_of_week >= 5, "weekend", "weekday")})
        prof["hour_of_day"] = d.pivot_table(index="h", columns="wk", values="y", aggfunc="mean").round(4)
    if g.tier in ("subdaily", "daily"):
        prof["day_of_week"] = y.groupby(y.index.day_of_week).mean().round(4).to_frame("mean")
    prof["month"] = y.groupby(y.index.month).mean().round(4).to_frame("mean")
    out["profiles"] = prof
    if g.tier == "subdaily" and len(yv) > 48:
        out["schedule"] = {"peak_hour": int(y.groupby(y.index.hour).mean().idxmax()), "base_load": float(y.quantile(0.05)),
                           "weekend_to_weekday_ratio": float(y[y.index.day_of_week >= 5].mean() / y[y.index.day_of_week < 5].mean()) if (y.index.day_of_week >= 5).any() else None}
    # degree days
    tc = find_temperature_col(predictors)
    if tc:
        bh, bc, r2 = fit_balance_points(df[tc], y)
        out["degree_days"] = {"temperature_column": tc, "heating_balance": bh, "cooling_balance": bc, "r2": r2}
    # change points
    cps, how = change_points(y.values)
    out["change_points"] = {"method": how, "indices": cps, "timestamps": [str(y.index[min(i, len(y) - 1)]) for i in cps]}
    # engineering plausibility
    plaus = []
    if (yv < 0).any():
        plaus.append(f"{int((yv < 0).sum())} negative target values (generation/export or sensor error?)")
    zeros = float((yv == 0).mean())
    if zeros > 0.05:
        plaus.append(f"{zeros:.1%} zero readings - check for meter outages")
    if tc and tc in corr["variable"].values:
        r = float(corr.set_index("variable").loc[tc, "pearson"])
        plaus.append(f"Correlation of '{tc}' with the target is {r:+.2f}: " + ("positive -> cooling-dominated load." if r > 0.3 else "negative -> heating-dominated load." if r < -0.3 else "weak weather dependence."))
    out["plausibility"] = plaus or ["No engineering-plausibility issues detected."]
    out["variability"] = {"cv_percent": out["distribution"]["cv_percent"], "iqr": float(yv.quantile(.75) - yv.quantile(.25))}
    return out
