"""Granularity-aware feature engineering. ``FeatureSpec`` is stored with the model so the
Prediction module can rebuild the exact same features from raw predictor columns."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .auto import AutoLog
from .temporal import Granularity

log = logging.getLogger("energybaseline.features")
TEMP_RE = re.compile(r"(?:^|[^a-z0-9])(?:temperature|temp|oat|tdb|drybulb|tamb|ambient|tout)(?:$|[^a-z0-9])", re.I)
TIME_INT_COLS = ("hour", "dow", "month")
_EXCLUDED_TEMPERATURE = {"setpoint", "setpt", "supply", "return", "chw", "hw"}


def find_temperature_col(
    cols: list[str], units: dict[str, str] | None = None, *, explicit: str | None = None
) -> str | None:
    """Resolve an unambiguous outdoor temperature among allowed predictors.

    Explicit schema selection wins. Units disambiguate eligible names, but cannot
    turn a supply/process/setpoint temperature into an outdoor driver.
    """
    if explicit is not None:
        if explicit not in cols:
            raise ValueError("The selected temperature must be an allowed predictor column.")
        return explicit
    candidates = []
    for column in cols:
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(column)).lower()
        tokens = re.findall(r"[a-z0-9]+", name)
        compact = "".join(tokens)
        if _EXCLUDED_TEMPERATURE.intersection(tokens) or any(x in compact for x in ("setpoint", "setpt")):
            continue
        normalized = " ".join(tokens).replace("dry bulb", "drybulb").replace("t out", "tout")
        if TEMP_RE.search(normalized) or ("outdoor" in tokens and "t" in tokens):
            candidates.append(column)
    if units and candidates:
        from .ingest import lookup_unit

        measured = [
            c for c in candidates if (u := lookup_unit(units.get(c, ""))) is not None and u.dimension == "temperature"
        ]
        if measured:
            candidates = measured
    return candidates[0] if len(candidates) == 1 else None


def validate_memory(values, *, setting: str) -> list[int]:
    """Reject future-looking, fractional or boolean lag/window specifications."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [value.strip() for value in values.split(",") if value.strip()]
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{setting} must be a sequence of positive integers.")
    out = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError(f"{setting} must contain positive integers, not booleans.")
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{setting} must contain positive integers.") from None
        if integer <= 0 or float(value) != integer:
            raise ValueError(f"{setting} must contain positive integers; future-looking features are forbidden.")
        out.append(integer)
    return sorted(set(out))


@dataclass
class FeatureSpec:
    time_aware: bool
    raw_cols: list[str]
    cat_levels: dict[str, list] = field(default_factory=dict)
    calendar: bool = False
    fourier: dict[str, int] = field(default_factory=dict)  # period name -> K harmonics (day/week/year)
    temp_col: str | None = None
    balance_heat: float | None = None
    balance_cool: float | None = None
    lag_cols: list[str] = field(default_factory=list)
    lags: list[int] = field(default_factory=list)
    roll_cols: list[str] = field(default_factory=list)
    rolls: list[int] = field(default_factory=list)
    interactions: list[tuple[str, str]] = field(default_factory=list)
    steps_per_day: float = 1.0
    holiday_country: str = ""
    columns_: list[str] = field(default_factory=list)  # all candidate columns produced
    medians_: dict[str, float] = field(default_factory=dict)
    selected: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def required_columns(self) -> list[str]:
        return list(self.raw_cols) + list(self.cat_levels)

    def build(self, raw: pd.DataFrame) -> pd.DataFrame:
        validate_memory(self.lags, setting="lags")
        validate_memory(self.rolls, setting="rolling_windows")
        X = pd.DataFrame(index=raw.index)
        for c in self.raw_cols:
            X[c] = pd.to_numeric(raw[c], errors="coerce")
        for c, levels in self.cat_levels.items():
            for lv in levels[1:]:
                X[f"{c}={lv}"] = (raw[c].astype(str) == str(lv)).astype(float)
        idx = raw.index
        if self.time_aware and isinstance(idx, pd.DatetimeIndex):
            hour = idx.hour + idx.minute / 60.0
            dow = idx.day_of_week
            if self.calendar:
                X["is_weekend"] = (dow >= 5).astype(float)
                if self.steps_per_day > 1:
                    X["hour"] = hour.astype(float)
                if self.steps_per_day >= 1 / 7:
                    X["dow"] = dow.astype(float)
                X["month"] = idx.month.astype(float)
                if self.holiday_country:
                    try:
                        import holidays

                        hol = holidays.country_holidays(self.holiday_country)
                        X["is_holiday"] = np.array([d in hol for d in idx.date], dtype=float)
                    except (ImportError, KeyError, ValueError, NotImplementedError) as exc:
                        log.warning("features.holiday_unavailable error_type=%s", type(exc).__name__)
                        raise ValueError(
                            "The configured holiday calendar is unavailable. Correct the country code or disable holiday features explicitly."
                        ) from exc
            doy = idx.day_of_year.values + hour.values / 24.0
            for name, k in self.fourier.items():
                for h in range(1, k + 1):
                    if name == "day":
                        ph = 2 * np.pi * h * hour.values / 24.0
                    elif name == "week":
                        ph = 2 * np.pi * h * (dow.values + hour.values / 24.0) / 7.0
                    else:
                        ph = 2 * np.pi * h * doy / 365.25
                    X[f"sin_{name}{h}"] = np.sin(ph)
                    X[f"cos_{name}{h}"] = np.cos(ph)
        if self.temp_col and self.temp_col in raw.columns:
            T = pd.to_numeric(raw[self.temp_col], errors="coerce")
            if self.balance_heat is not None:
                X[f"HDD(Tb={self.balance_heat:.1f})"] = np.maximum(0.0, self.balance_heat - T)
            if self.balance_cool is not None:
                X[f"CDD(Tb={self.balance_cool:.1f})"] = np.maximum(0.0, T - self.balance_cool)
        for c in self.lag_cols:
            for L in self.lags:
                X[f"{c}_lag{L}"] = pd.to_numeric(raw[c], errors="coerce").shift(L)
        for c in self.roll_cols:
            for w in self.rolls:
                X[f"{c}_roll{w}"] = pd.to_numeric(raw[c], errors="coerce").rolling(w, min_periods=1).mean()
        for a, b in self.interactions:
            if a in X and b in X:
                X[f"ix:{a}*{b}"] = X[a] * X[b]
        if self.medians_:
            for c in X.columns:
                if X[c].isna().any() and c in self.medians_:
                    X[c] = X[c].fillna(self.medians_[c])
        if self.columns_:
            for c in self.columns_:
                if c not in X:
                    X[c] = 0.0
            X = X[self.columns_]
        return X.replace([np.inf, -np.inf], np.nan)


def _acf(x: np.ndarray, lag: int) -> float:
    x = x[~np.isnan(x)]
    if lag <= 0 or len(x) <= lag + 5:
        return 0.0
    x = x - x.mean()
    d = (x * x).sum()
    return float((x[:-lag] * x[lag:]).sum() / d) if d > 0 else 0.0


def fit_balance_points(T: pd.Series, y: pd.Series) -> tuple[float | None, float | None, float]:
    """Grid-search heating/cooling balance points maximising R2 of y ~ HDD + CDD."""
    d = pd.concat([T, y], axis=1).dropna()
    if len(d) < 20 or d.iloc[:, 0].nunique() < 5:
        return None, None, 0.0
    t, yy = d.iloc[:, 0].values, d.iloc[:, 1].values
    qs = np.unique(np.quantile(t, np.linspace(0.05, 0.95, 19)))
    best = (None, None, -1.0)
    sst = ((yy - yy.mean()) ** 2).sum()
    for bh in qs:
        for bc in qs:
            if bc < bh:
                continue
            A = np.column_stack([np.ones_like(t), np.maximum(0, bh - t), np.maximum(0, t - bc)])
            beta, *_ = np.linalg.lstsq(A, yy, rcond=None)
            r2 = 1 - ((yy - A @ beta) ** 2).sum() / sst
            if r2 > best[2]:
                best = (float(bh), float(bc), float(r2))
    return best


def build_spec(
    prep_df: pd.DataFrame, target: str, predictors: list[str], cat_cols: list[str], g: Granularity, auto: AutoLog
) -> tuple[FeatureSpec, pd.DataFrame, dict]:
    y = prep_df[target]
    n = int(y.notna().sum())
    span_days = (prep_df.index.max() - prep_df.index.min()).total_seconds() / 86400 if len(prep_df) > 1 else 0
    spd = g.steps_per_day
    info: dict = {}
    spec = FeatureSpec(
        time_aware=True,
        raw_cols=list(predictors),
        steps_per_day=spd,
        cat_levels={c: sorted(prep_df[c].dropna().astype(str).unique().tolist()) for c in cat_cols},
    )

    # calendar
    cal = auto.decide(
        "calendar_features",
        lambda: (
            g.tier in ("subdaily", "daily") and n >= 48,
            f"granularity={g.label}, n={n}",
            "calendar effects matter for sub-daily/daily data with >=48 points",
        ),
    )
    spec.calendar = bool(cal) and g.tier in ("subdaily", "daily", "weekly")
    if spec.calendar and span_days < 200:
        pass

    # fourier
    def _four():
        ks: dict[str, int] = {}
        ev = []
        arr = y.values.astype(float)
        if g.tier == "subdaily":
            a = _acf(arr, int(round(spd)))
            ev.append(f"ACF@1day={a:.2f}")
            if a > 0.2:
                ks["day"] = 2 if a > 0.5 else 1
            aw = _acf(arr, int(round(spd * 7)))
            ev.append(f"ACF@1week={aw:.2f}")
            if aw > 0.2:
                ks["week"] = 1
        elif g.tier == "daily":
            aw = _acf(arr, 7)
            ev.append(f"ACF@7d={aw:.2f}")
            if aw > 0.2:
                ks["week"] = 2 if aw > 0.5 else 1
        if span_days >= 365 and g.tier != "monthly":
            ks["year"] = 1
            ev.append(f"span={span_days:.0f}d >= 1y")
        if g.tier == "monthly" and n >= 24:
            ks["year"] = 1
            ev.append("monthly data, >=24 points")
        return (
            ks,
            ", ".join(ev) or "no periodicity evidence",
            "include harmonics where autocorrelation at the period exceeds 0.2",
        )

    fk = auto.decide("fourier_terms", _four)
    spec.fourier = fk if isinstance(fk, dict) else ({"day": int(fk)} if fk else {})

    # degree days
    from .domain import resolve_domain

    policy = resolve_domain(auto.config)
    tcol = find_temperature_col(predictors) if policy.energy else None
    dd = auto.decide(
        "degree_days",
        lambda: (
            tcol is not None,
            f"temperature-like column: {tcol}",
            "HDD/CDD only when an outdoor-temperature column exists",
        ),
    )
    if dd and not policy.energy:
        raise ValueError("Degree-day features require the explicitly selected energy domain pack.")
    if dd and tcol:
        bh, bc, r2 = fit_balance_points(prep_df[tcol], y)
        if bh is not None:
            spec.temp_col, spec.balance_heat, spec.balance_cool = tcol, bh, bc
            auto.note(
                "balance_points",
                f"heating={bh:.1f}, cooling={bc:.1f}",
                f"grid search maximising R2 of y~HDD+CDD (R2={r2:.3f})",
                "fitted from data (percentile grid)",
            )
            info["balance_r2"] = r2

    # lags / rolling for temperature-like column
    lag_col = tcol or (
        max(predictors, key=lambda c: abs(prep_df[c].corr(y)) if prep_df[c].nunique() > 2 else 0)
        if predictors
        else None
    )

    def _lags():
        if not lag_col or n < 60:
            return [], "insufficient data or no driver", "no lags"
        cands = sorted({1, 2, 3, max(1, int(spd // 4)), max(1, int(spd))}) if spd >= 1 else [1]
        base = abs(prep_df[lag_col].corr(y))
        keep, ev = [], [f"base |r|={base:.3f}"]
        for L in cands:
            r = abs(prep_df[lag_col].shift(L).corr(y))
            ev.append(f"lag{L}={r:.3f}")
            if r - base > 0.02:
                keep.append(L)
        return keep[:2], "; ".join(ev), f"keep lags whose |r| beats the unlagged driver '{lag_col}' by > 0.02"

    lags = validate_memory(auto.decide("lags", _lags), setting="lags")
    if lags and lag_col:
        spec.lag_cols, spec.lags = [lag_col], [int(x) for x in lags]

    def _roll():
        if not lag_col or n < 60:
            return [], "insufficient data or no driver", "no rolling means"
        base = abs(prep_df[lag_col].corr(y))
        keep, ev = [], [f"base |r|={base:.3f}"]
        for w in sorted({3, max(2, int(spd)), max(2, int(spd * 3))}) if spd >= 1 else []:
            r = abs(prep_df[lag_col].rolling(w, min_periods=1).mean().corr(y))
            ev.append(f"roll{w}={r:.3f}")
            if r - base > 0.02:
                keep.append(w)
        return keep[:1], "; ".join(ev), "keep rolling means that beat the unlagged driver by > 0.02"

    rolls = validate_memory(auto.decide("rolling_windows", _roll), setting="rolling_windows")
    if rolls and lag_col:
        spec.roll_cols, spec.rolls = [lag_col], [int(x) for x in rolls]

    # interactions
    p = len(predictors)
    ia = auto.decide(
        "interactions",
        lambda: (
            p >= 2 and p <= 6 and n > 20 * p,
            f"p={p}, n={n}",
            "interactions only when n > 20p and p<=6 (over-fit protection)",
        ),
    )
    if ia and p >= 2:
        top = sorted(predictors, key=lambda c: -abs(prep_df[c].corr(y)))[:3]
        spec.interactions = [(top[i], top[j]) for i in range(len(top)) for j in range(i + 1, len(top))]

    spec.holiday_country = str(
        auto.decide("holiday_country", lambda: ("", "no country declared", "no holiday flags (country unknown)")) or ""
    )
    spec.columns_ = []
    X = spec.build(prep_df)
    spec.columns_ = list(X.columns)
    spec.medians_ = {c: float(X[c].median()) if X[c].notna().any() else 0.0 for c in X.columns}
    # The unfilled matrix is used for validation; evaluate_candidate fits each
    # fold's medians on training rows. Persisted spec medians are for deployment.
    info["n_candidate_features"] = X.shape[1]
    return spec, X, info


# ---------------------------------------------------------------- selection
def select_features(X: pd.DataFrame, y: pd.Series, auto: AutoLog, seed: int, exclusions: list[dict]) -> list[str]:
    from sklearn.feature_selection import mutual_info_regression
    from sklearn.linear_model import Lasso

    p, n = X.shape[1], len(y)
    protected = [c for c in X.columns if c.startswith(("HDD", "CDD")) or c in ("hour", "dow")]

    def _m():
        if p <= 8:
            return "none", f"p={p} <= 8", "few candidates: keep all"
        if n > 10 * p:
            return (
                "lasso",
                f"p={p}, n={n} > 10p",
                "L1-penalised regression with a fixed scale-relative training-fold penalty",
            )
        return (
            "mutual_info",
            f"p={p}, n={n} <= 10p",
            "L1 unstable with small n/p: use non-parametric mutual information",
        )

    method = auto.decide("selection_method", _m)
    kmax = auto.decide(
        "max_features",
        lambda: (
            (min(p, n // 20) if n >= 20 else min(p, 1)),
            f"p={p}, n={n}",
            "exact k_max = min(p, n // 20) observations-per-feature rule",
        ),
    )
    kmax = int(kmax)
    keep = list(X.columns)
    if method != "none" and p > 1:
        Z = (X - X.mean()) / X.std(ddof=0).replace(0, 1)
        Z = Z.fillna(0)
        if method == "correlation":
            score = Z.apply(lambda c: abs(c.corr(y))).fillna(0)
        elif method == "mutual_info":
            score = pd.Series(mutual_info_regression(Z.values, y.values, random_state=seed), index=X.columns)
        else:
            # Fixed scale-relative penalty, fitted only on the enclosing train
            # fold. Hyperparameter CV belongs to RawEvaluationPlan.
            las = Lasso(alpha=0.01 * max(float(y.std(ddof=0)), 1e-12), random_state=seed, max_iter=20000).fit(
                Z.values, y.values
            )
            score = pd.Series(np.abs(las.coef_), index=X.columns)
            nz = int((score > 1e-12).sum())
            if nz == 0:
                score = pd.Series(mutual_info_regression(Z.values, y.values, random_state=seed), index=X.columns)
        ranked = score.sort_values(ascending=False)
        if method == "lasso":
            ranked_keep = [c for c in ranked.index if ranked[c] > 1e-12]
        else:
            ranked_keep = list(ranked.index)
        sel = ranked_keep[:kmax]
        for c in protected:
            if c not in sel:
                sel.append(c)
        for c in X.columns:
            if c not in sel:
                exclusions.append(
                    {
                        "item": c,
                        "kind": "feature-selection",
                        "reason": f"Dropped by {method} (score={ranked[c]:.4g}; kept {len(sel)} of {p}).",
                        "metric": round(float(ranked[c]), 6),
                    }
                )
        keep = [c for c in X.columns if c in sel]
    return keep
