"""Data-quality profiling and per-target preprocessing with a complete exclusion audit trail."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from .auto import AutoLog
from .temporal import ENERGY_NAME, Granularity

ENERGY_LIKE = re.compile(r"kwh|mwh|energy|consum|usage|meter|power|\bkw\b|therm|gas|steam|chilled|elec|water", re.I)


@dataclass
class Prep:
    df: pd.DataFrame
    predictors: list[str]
    cat_cols: list[str]
    exclusions: list[dict]
    quality: dict
    notes: list[str] = field(default_factory=list)
    outliers: dict = field(default_factory=dict)


def _excl(log: list, item: str, kind: str, reason: str, metric=None) -> None:
    log.append({"item": item, "kind": kind, "reason": reason, "metric": None if metric is None else (round(float(metric), 6) if isinstance(metric, (int, float, np.floating)) else str(metric))})


def robust_bounds(x: pd.Series, thr: float) -> tuple[float, float]:
    med = x.median()
    mad = (x - med).abs().median()
    s = 1.4826 * mad if mad > 0 else x.std(ddof=0) or 1.0
    return med - thr * s, med + thr * s


def detect_outliers(method: str, X: pd.DataFrame, thr: float, seed: int) -> pd.DataFrame:
    """Return boolean frame (same shape as X) for univariate methods; for multivariate methods
    the row flag is broadcast to all columns."""
    flags = pd.DataFrame(False, index=X.index, columns=X.columns)
    if method == "none" or X.empty:
        return flags
    if method in ("robust_z", "mad"):
        for c in X:
            lo, hi = robust_bounds(X[c].dropna(), thr)
            flags[c] = (X[c] < lo) | (X[c] > hi)
        return flags
    if method == "iqr":
        for c in X:
            q1, q3 = X[c].quantile([0.25, 0.75])
            i = q3 - q1
            flags[c] = (X[c] < q1 - thr * 0.4286 * i) | (X[c] > q3 + thr * 0.4286 * i)  # thr=3.5 -> 1.5*IQR
        return flags
    Z = X.fillna(X.median())
    Z = (Z - Z.mean()) / Z.std(ddof=0).replace(0, 1)
    A = Z.values
    row = np.zeros(len(Z), dtype=bool)
    if method == "isolation_forest":
        from sklearn.ensemble import IsolationForest
        row = IsolationForest(random_state=seed, contamination=0.01).fit_predict(A) == -1
    elif method == "lof":
        from sklearn.neighbors import LocalOutlierFactor
        row = LocalOutlierFactor(n_neighbors=min(20, max(2, len(A) - 1)), contamination=0.01).fit_predict(A) == -1
    elif method == "ocsvm":
        from sklearn.svm import OneClassSVM
        idx = np.random.default_rng(seed).choice(len(A), min(len(A), 3000), replace=False)
        m = OneClassSVM(nu=0.01, gamma="scale").fit(A[idx])
        row = m.predict(A) == -1
    elif method == "mahalanobis":
        cov = np.cov(A, rowvar=False)
        inv = np.linalg.pinv(np.atleast_2d(cov))
        d2 = np.einsum("ij,jk,ik->i", A - A.mean(0), inv, A - A.mean(0))
        row = d2 > stats.chi2.ppf(0.999, df=A.shape[1])
    elif method == "hbos":
        score = np.zeros(len(A))
        for j in range(A.shape[1]):
            h, edges = np.histogram(A[:, j], bins=max(5, int(np.sqrt(len(A)))), density=True)
            b = np.clip(np.digitize(A[:, j], edges[:-1]) - 1, 0, len(h) - 1)
            score += -np.log(h[b] + 1e-9)
        row = score > np.quantile(score, 0.99)
    elif method == "autoencoder":
        from sklearn.neural_network import MLPRegressor
        k = max(1, A.shape[1] // 2)
        ae = MLPRegressor(hidden_layer_sizes=(max(2, A.shape[1]), k, max(2, A.shape[1])), max_iter=300, random_state=seed).fit(A, A)
        err = ((ae.predict(A) - A) ** 2).mean(1)
        row = err > np.quantile(err, 0.99)
    else:
        raise ValueError(f"Unknown outlier method {method}")
    for c in flags:
        flags[c] = row
    return flags


def calendar_adjust(s: pd.Series, g: Granularity) -> pd.Series:
    """Remove the typical time-of-week profile (median) so legitimate schedule effects are not flagged."""
    idx = s.index
    if not isinstance(idx, pd.DatetimeIndex) or s.notna().sum() < 30:
        return s
    if g.tier == "subdaily":
        key = idx.day_of_week * 24 + idx.hour
    elif g.tier == "daily":
        key = idx.day_of_week
    else:
        return s
    med = s.groupby(key).transform("median")
    return s - med


def vif_table(X: pd.DataFrame) -> pd.Series:
    if X.shape[1] < 2:
        return pd.Series(1.0, index=X.columns)
    R = np.corrcoef(X.values, rowvar=False)
    R = np.nan_to_num(R) + np.eye(X.shape[1]) * 1e-9
    inv = np.linalg.pinv(R)
    return pd.Series(np.diag(inv), index=X.columns)


def quality_score(df: pd.DataFrame, n_dups: int, n_gap_rows: int, outlier_share: float) -> dict:
    num = df.select_dtypes("number")
    completeness = 1 - float(num.isna().mean().mean()) if num.shape[1] else 0.0
    uniqueness = 1 - n_dups / max(1, len(df) + n_dups)
    regularity = 1 - n_gap_rows / max(1, len(df))
    validity = 1 - outlier_share
    total = 100 * (0.4 * completeness + 0.2 * validity + 0.1 * uniqueness + 0.3 * regularity)
    return {"score": round(total, 1), "completeness": round(completeness, 4), "validity": round(validity, 4),
            "uniqueness": round(uniqueness, 4), "regularity": round(regularity, 4)}


def profile(df: pd.DataFrame) -> pd.DataFrame:
    return df.describe(include="all").T


def preprocess_target(df: pd.DataFrame, target: str, all_targets: list[str], g: Granularity, auto: AutoLog,
                      seed: int, n_dups: int = 0) -> Prep:
    log: list[dict] = []
    notes: list[str] = []
    if target not in df.columns:
        raise ValueError(f"Target '{target}' not found after temporal preparation.")
    work = df.copy()
    y_valid = work[target].notna().sum()
    if y_valid < 8:
        raise ValueError(f"Target '{target}' has only {int(y_valid)} valid observations - cannot build a baseline.")
    cand = [c for c in work.columns if c != target]
    keep: list[str] = []

    # ---- other targets (leakage rule per target)
    for c in cand:
        if c in all_targets:
            _excl(log, c, "leakage", f"Other TARGET column ('{c}') is never used as a predictor of '{target}' (simultaneous energy flow).")
        else:
            keep.append(c)

    # ---- categorical vs numeric
    cats = []
    for c in list(keep):
        if not pd.api.types.is_numeric_dtype(work[c]):
            nun = work[c].nunique(dropna=True)
            if 1 < nun <= 12:
                cats.append(c)
            else:
                keep.remove(c)
                _excl(log, c, "non-numeric", f"Text column with {nun} distinct values is not usable as predictor (encode numerically first).", nun)
    nums = [c for c in keep if c not in cats]

    # ---- missing rate
    max_missing = auto.decide("max_missing_rate", lambda: (0.40, "default engineering limit", "columns >40 % missing carry too little information"))
    for c in list(nums):
        r = work[c].isna().mean()
        if r > max_missing:
            nums.remove(c)
            _excl(log, c, "missing", f"Missing rate {r:.1%} exceeds the limit {max_missing:.0%}.", r)
    # ---- constant / near-constant
    min_var = auto.decide("min_variance", lambda: (1e-8, "default numerical tolerance", "relative variance below 1e-8 means constant"))
    for c in list(nums):
        s = work[c].dropna()
        if s.nunique() <= 1 or (s.std() ** 2) / (max(abs(s.mean()), 1e-12) ** 2 + 1e-12) < min_var:
            nums.remove(c)
            _excl(log, c, "constant", "Constant / zero-variance column carries no information.", s.nunique())
    for c in list(cats):
        if work[c].nunique(dropna=True) <= 1:
            cats.remove(c)
            _excl(log, c, "constant", "Constant categorical column.")
    # ---- duplicates columns
    seen: dict[bytes, str] = {}
    for c in list(nums):
        key = pd.util.hash_pandas_object(work[c].round(9), index=False).values.tobytes()
        if key in seen:
            nums.remove(c)
            _excl(log, c, "duplicate-column", f"Identical to column '{seen[key]}'.")
        else:
            seen[key] = c

    # ---- leakage (energy-like, almost perfect correlation with target)
    leak_thr = auto.decide("leakage_threshold", lambda: (0.99, "default", "|r|>=0.99 with target AND energy-like name => leakage"))
    for c in list(nums):
        pair = work[[c, target]].dropna()
        if len(pair) > 5:
            r = abs(pair[c].corr(pair[target]))
            if r >= leak_thr and (ENERGY_LIKE.search(c) or r >= 0.9995):
                nums.remove(c)
                _excl(log, c, "leakage", f"|r|={r:.4f} with target and energy-like/derived column: likely a component or copy of the target.", r)
            elif r >= leak_thr:
                notes.append(f"WARNING: '{c}' correlates |r|={r:.4f} with '{target}' (kept: not energy-like; verify it is a legitimate driver).")

    # ---- outliers
    sk = float(abs(stats.skew(work[target].dropna()))) if y_valid > 3 else 0.0
    n = len(work)

    def _om():
        if y_valid < 30:
            return "none", f"n={int(y_valid)} < 30", "too few observations for reliable outlier statistics"
        if sk > 2:
            return "mad", f"target |skew|={sk:.2f} > 2", "heavily skewed -> median/MAD estimator"
        return "robust_z", f"target |skew|={sk:.2f} <= 2, n={n}", "approximately symmetric -> robust z-score on calendar-adjusted values"

    method = auto.decide("outlier_method", _om)
    thr = auto.decide("outlier_threshold", lambda: (3.5 if sk <= 2 else 4.5, f"|skew|={sk:.2f}", "3.5 (Iglewicz-Hoaglin); 4.5 for skewed variables"))
    cols_o = [target] + nums
    univariate = method in ("robust_z", "mad", "iqr")
    adj = pd.DataFrame({c: calendar_adjust(work[c], g) for c in cols_o}) if univariate else work[cols_o]
    if univariate and g.tier in ("subdaily", "daily"):
        notes.append("Outlier statistics were computed on calendar-adjusted values (time-of-week median removed) so that legitimate schedule effects are not flagged.")
    flags = detect_outliers(method, adj, thr, seed)
    row_flag_target = flags[target]
    share = float(row_flag_target.mean())
    any_flag = float(flags.any(axis=1).mean())

    def _ot():
        if method == "none":
            return "flag", "no detection performed", "nothing to treat"
        if share <= 0.02:
            return "remove", f"target flagged share={share:.2%} <= 2 %", "few extreme points: remove from training (target never altered)"
        return "flag", f"target flagged share={share:.2%} > 2 %", "too many to be errors (likely genuine operating regimes): flag only, keep"

    treat = auto.decide("outlier_treatment", _ot)
    n_t = int(row_flag_target.sum())
    out_info = {"method": method, "threshold": thr, "treatment": treat, "target_flagged": n_t, "target_share": share,
                "any_flagged_share": any_flag, "per_column": {c: int(flags[c].sum()) for c in cols_o}}
    if treat in ("remove", "winsorize", "interpolate") and n_t:
        idxs = work.index[row_flag_target]
        work.loc[idxs, target] = np.nan
        _excl(log, f"{n_t} target rows", "outlier", f"Flagged by {method} (thr {thr}); excluded from training ('{treat}' - target values are never modified). First: {idxs[0]}", n_t)
    for c in nums:
        k = int(flags[c].sum())
        if not k or treat == "flag":
            continue
        if treat == "winsorize" or (treat == "remove" and method in ("robust_z", "mad", "iqr")):
            lo, hi = robust_bounds(adj[c].dropna(), thr)
            work[c] = (work[c] - adj[c]) + adj[c].clip(lo, hi)
            _excl(log, f"{k} values of {c}", "outlier", f"Winsorised to the profile band [{lo:.4g}, {hi:.4g}] around the typical value ({method}).", k)
        elif treat == "interpolate":
            work.loc[flags[c], c] = np.nan
            _excl(log, f"{k} values of {c}", "outlier", f"Replaced by time interpolation ({method}).", k)

    # ---- missing values in predictors
    miss_rate = float(work[nums].isna().mean().mean()) if nums else 0.0

    def _ms():
        if miss_rate == 0:
            return "linear", "no missing predictor values", "irrelevant (kept as default)"
        if miss_rate < 0.05:
            return "linear", f"mean missing rate={miss_rate:.2%} < 5 %", "time-linear interpolation (short gaps)"
        if miss_rate < 0.20 and n <= 20000:
            return "knn", f"mean missing rate={miss_rate:.2%} in 5-20 %", "kNN imputation on standardised predictors"
        return "median", f"mean missing rate={miss_rate:.2%} >= 20 % or n>20000", "median imputation (robust, cheap)"

    strat = auto.decide("missing_strategy", _ms)
    if nums and work[nums].isna().any().any():
        before = int(work[nums].isna().sum().sum())
        limit = max(3, int(round(g.steps_per_day))) if g.steps_per_day >= 1 else 3
        if strat == "linear":
            work[nums] = work[nums].interpolate(method="linear", limit=limit, limit_direction="both")
            work[nums] = work[nums].fillna(work[nums].median())
        elif strat == "ffill":
            work[nums] = work[nums].ffill().bfill()
        elif strat == "knn":
            from sklearn.impute import KNNImputer
            mu, sd = work[nums].mean(), work[nums].std(ddof=0).replace(0, 1)
            Z = (work[nums] - mu) / sd
            work[nums] = pd.DataFrame(KNNImputer(n_neighbors=5).fit_transform(Z), index=work.index, columns=nums) * sd + mu
        elif strat == "median":
            work[nums] = work[nums].fillna(work[nums].median())
        elif strat == "drop":
            work = work.dropna(subset=nums)
        _excl(log, f"{before} missing predictor cells", "imputation", f"Imputed with '{strat}'.", before)
    work[cats] = work[cats].ffill().bfill() if cats else work[cats]

    # ---- multicollinearity
    corr_thr = auto.decide("corr_threshold", lambda: (0.95, "default", "|r|>=0.95 => redundant pair"))
    vif_thr = auto.decide("vif_threshold", lambda: ((5.0, f"n={n} < 100", "stricter VIF for small samples") if n < 100 else (10.0, f"n={n}", "VIF>10 is the standard multicollinearity limit")))
    if len(nums) > 1:
        tcorr = work[nums + [target]].corr(method="spearman")[target].abs()
        P = work[nums].corr().abs()
        S = work[nums].corr(method="spearman").abs()
        M = np.maximum(P.values, S.values)
        pairs = [(M[i, j], nums[i], nums[j]) for i in range(len(nums)) for j in range(i + 1, len(nums)) if M[i, j] >= corr_thr]
        dropped: set[str] = set()
        for r, a, b in sorted(pairs, reverse=True):
            if a in dropped or b in dropped:
                continue
            worse, better = (a, b) if tcorr.get(a, 0) < tcorr.get(b, 0) else (b, a)
            dropped.add(worse)
            _excl(log, worse, "collinearity", f"|r|={r:.3f} with '{better}' (>= {corr_thr}); kept the one more correlated with the target.", r)
        nums = [c for c in nums if c not in dropped]
        while len(nums) > 2:
            v = vif_table(work[nums].fillna(work[nums].median()))
            if v.max() <= vif_thr:
                break
            worst = v.idxmax()
            nums.remove(worst)
            _excl(log, worst, "vif", f"VIF={v.max():.1f} > {vif_thr}; iterative pruning.", v.max())
        if len(nums) > 1:
            Zs = work[nums].fillna(work[nums].median())
            Zs = (Zs - Zs.mean()) / Zs.std(ddof=0).replace(0, 1)
            notes.append(f"Condition number of the retained standardised predictors: {np.linalg.cond(Zs.values):.1f}")

    out = work[[target] + nums + cats].copy()
    q = quality_score(df[[target] + [c for c in df.columns if c != target]], n_dups,
                      int(df.drop(columns=[]).isna().all(axis=1).sum()), any_flag)
    return Prep(out, nums, cats, log, q, notes, out_info)
