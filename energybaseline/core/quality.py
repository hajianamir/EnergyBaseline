"""Data-quality profiling, Outlier Action Policy, Non-Routine Events, and leak-free preprocessing (DAT-5, Gate G2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .auto import AutoLog, is_auto
from .schema import (
    AUTO,
    ColumnRole,
    DatasetSchema,
    NonRoutineEventSpec,
    OutlierActionPolicy,
    coerce_dataset_schema,
)
from .temporal import Granularity

ENERGY_LIKE = re.compile(r"kwh|mwh|energy|consum|usage|meter|power|\bkw\b|therm|gas|steam|chilled|elec|water", re.I)


class FabricationError(ValueError):
    """Raised if any preprocessing step attempts to fabricate rows or impute missing target values (DAT-5)."""


@dataclass
class OutlierPolicyRecord:
    """Audit record for Outlier Action Policy execution (`flag_only | ask_user | auto_apply`, Gate G2)."""

    policy: str
    rule: str
    treatment: str
    flagged_rows: list[str]
    flagged_target_rows: list[str]
    flagged_count: int
    applied: bool
    pending_user_confirmation: bool
    n_train_before: int
    n_train_after: int
    training_set_delta: int
    target_mean_delta: float
    target_std_delta: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "rule": self.rule,
            "treatment": self.treatment,
            "flagged_rows": list(self.flagged_rows),
            "flagged_target_rows": list(self.flagged_target_rows),
            "flagged_count": self.flagged_count,
            "applied": self.applied,
            "pending_user_confirmation": self.pending_user_confirmation,
            "n_train_before": self.n_train_before,
            "n_train_after": self.n_train_after,
            "training_set_delta": self.training_set_delta,
            "target_mean_delta": self.target_mean_delta,
            "target_std_delta": self.target_std_delta,
        }


@dataclass
class Prep:
    df: pd.DataFrame
    predictors: list[str]
    cat_cols: list[str]
    exclusions: list[dict]
    quality: dict
    notes: list[str] = field(default_factory=list)
    outliers: dict = field(default_factory=dict)
    group_cols: list[str] = field(default_factory=list)
    weight_col: str | None = None
    nre_applied: list[dict] = field(default_factory=list)


def _excl(log: list, item: str, kind: str, reason: str, metric=None) -> None:
    log.append(
        {
            "item": item,
            "kind": kind,
            "reason": reason,
            "metric": None
            if metric is None
            else (round(float(metric), 6) if isinstance(metric, (int, float, np.floating)) else str(metric)),
        }
    )


def verify_no_target_fabrication(raw_target: pd.Series, processed_target: pd.Series) -> None:
    """Enforce DAT-5 invariant: missing target observations are NEVER imputed or fabricated."""
    if len(processed_target) > len(raw_target):
        raise FabricationError(
            f"Synthetic rows detected: processed frame has {len(processed_target)} rows vs {len(raw_target)} raw rows."
        )
    aligned_raw = raw_target.reindex(processed_target.index)
    fabricated_mask = aligned_raw.isna() & processed_target.notna()
    if bool(fabricated_mask.any()):
        n_fab = int(fabricated_mask.sum())
        raise FabricationError(
            f"DAT-5 violation: {n_fab} originally missing target observation(s) were imputed/fabricated."
        )


def _contiguous_missing_lengths(mask: np.ndarray) -> np.ndarray:
    """For each position in boolean ``mask`` (True = missing), return the total run length of its missing block."""
    n = len(mask)
    lengths = np.zeros(n, dtype=int)
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i + 1
        while j < n and mask[j]:
            j += 1
        lengths[i:j] = j - i
        i = j
    return lengths


def apply_leak_free_missing_policy(
    df: pd.DataFrame,
    target: str,
    predictors: Sequence[str],
    *,
    eval_mask: pd.Series | np.ndarray | None = None,
    short_gap_max_steps: int = 3,
    shutdown_min_steps: int = 24,
    strategy: str = "linear",
    seed: int = 42,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply DAT-5 leak-free missing-value policy without fabricating rows or imputing targets.

    - Never adds synthetic rows.
    - Never imputes ``target`` (neither in training nor in evaluation sets, nor across shutdowns).
    - Short driver gaps (`<= short_gap_max_steps`) use bounded interpolation inside training/eval segments
      separately so future evaluation observations never leak backward into training.
    - Extended shutdowns (`>= shutdown_min_steps` consecutive missing rows) are NEVER interpolated across.
    - Statistical imputers (`mice`, `knn`, `median`, `flag`) fit strictly on training rows (`~eval_mask`).
    """
    out = df.copy()
    raw_target = out[target].copy()
    preds = [c for c in predictors if c in out.columns and c != target]

    if eval_mask is None:
        eval_arr = np.zeros(len(out), dtype=bool)
    else:
        eval_arr = np.asarray(eval_mask, dtype=bool)
        if len(eval_arr) != len(out):
            raise ValueError("eval_mask length must match len(df).")
    train_arr = ~eval_arr

    # Identify plant/building extended shutdown spans (target missing for >= shutdown_min_steps OR all cols missing).
    target_miss_runs = _contiguous_missing_lengths(out[target].isna().to_numpy())
    shutdown_arr = target_miss_runs >= max(1, int(shutdown_min_steps))
    if preds:
        all_preds_miss_runs = _contiguous_missing_lengths(out[preds].isna().all(axis=1).to_numpy())
        shutdown_arr = shutdown_arr | (all_preds_miss_runs >= max(1, int(shutdown_min_steps)))

    added_flag_cols: list[str] = []
    short_interpolated_cells = 0
    model_imputed_cells = 0

    for c in preds:
        col_miss = out[c].isna().to_numpy()
        run_len = _contiguous_missing_lengths(col_miss)
        col_shutdown = shutdown_arr | (run_len >= max(1, int(shutdown_min_steps)))

        if strategy == "flag":
            flag_col = f"{c}__missing"
            out[flag_col] = col_miss.astype(float)
            added_flag_cols.append(flag_col)

        # Bounded short-gap interpolation separately within train and eval partitions (no train<->eval boundary leak).
        short_eligible = col_miss & (run_len <= max(1, int(short_gap_max_steps))) & (~col_shutdown)
        if np.any(short_eligible):
            for part_mask in (train_arr, eval_arr):
                if not np.any(part_mask):
                    continue
                sub = out.loc[part_mask, c].copy()
                interp = sub.interpolate(
                    method="linear",
                    limit=max(1, int(short_gap_max_steps)),
                    limit_area="inside",
                )
                use_here = short_eligible[part_mask] & interp.notna().to_numpy()
                if np.any(use_here):
                    short_interpolated_cells += int(np.sum(use_here))
                    idx_here = out.index[part_mask][use_here]
                    out.loc[idx_here, c] = interp.loc[idx_here]

    # Medium-gap driver handling (fitted strictly on non-shutdown training rows, never touching shutdown spans).
    remaining_preds = [c for c in preds if out.loc[~shutdown_arr, c].isna().any()]
    if remaining_preds and strategy in ("mice", "knn", "median", "flag", "linear", "ffill"):
        fit_rows = train_arr & (~shutdown_arr)
        apply_rows = ~shutdown_arr
        before_rem = int(out.loc[apply_rows, preds].isna().sum().sum())

        if strategy == "mice" and len(preds) >= 2 and int(np.sum(fit_rows)) >= 5:
            from sklearn.experimental import enable_iterative_imputer
            from sklearn.impute import IterativeImputer

            _ = enable_iterative_imputer

            imp = IterativeImputer(random_state=seed, max_iter=10)
            train_slice = out.loc[fit_rows, preds]
            imp.fit(train_slice)
            transformed = pd.DataFrame(
                imp.transform(out.loc[apply_rows, preds]),
                index=out.index[apply_rows],
                columns=preds,
            )
            for c in preds:
                col_run = _contiguous_missing_lengths(df[c].isna().to_numpy())
                keep_na = col_run[apply_rows] >= max(1, int(shutdown_min_steps))
                vals = transformed[c].copy()
                if np.any(keep_na):
                    vals.iloc[np.where(keep_na)[0]] = out.loc[apply_rows, c].iloc[np.where(keep_na)[0]]
                out.loc[apply_rows, c] = vals
        elif strategy == "knn" and int(np.sum(fit_rows)) >= 5:
            from sklearn.impute import KNNImputer

            train_slice = out.loc[fit_rows, preds]
            mu = train_slice.mean()
            sd = train_slice.std(ddof=0).replace(0, 1).fillna(1.0)
            knn = KNNImputer(n_neighbors=min(5, int(np.sum(fit_rows))))
            knn.fit((train_slice - mu) / sd)
            transformed = (
                pd.DataFrame(
                    knn.transform((out.loc[apply_rows, preds] - mu) / sd),
                    index=out.index[apply_rows],
                    columns=preds,
                )
                * sd
                + mu
            )
            for c in preds:
                col_run = _contiguous_missing_lengths(df[c].isna().to_numpy())
                keep_na = col_run[apply_rows] >= max(1, int(shutdown_min_steps))
                vals = transformed[c].copy()
                if np.any(keep_na):
                    vals.iloc[np.where(keep_na)[0]] = out.loc[apply_rows, c].iloc[np.where(keep_na)[0]]
                out.loc[apply_rows, c] = vals
        elif strategy == "ffill":
            for part_mask in (train_arr, eval_arr):
                if not np.any(part_mask):
                    continue
                filled = out.loc[part_mask, preds].ffill(limit=max(1, int(shutdown_min_steps) - 1))
                for c in preds:
                    col_run = _contiguous_missing_lengths(df[c].isna().to_numpy())
                    allow = part_mask & (~shutdown_arr) & (col_run < max(1, int(shutdown_min_steps)))
                    out.loc[allow, c] = filled.loc[out.index[allow], c]
        elif strategy in ("median", "flag", "linear"):
            train_medians = out.loc[fit_rows, preds].median()
            for c in preds:
                med_val = train_medians.get(c, np.nan)
                if pd.isna(med_val):
                    continue
                col_run = _contiguous_missing_lengths(df[c].isna().to_numpy())
                fill_mask = apply_rows & out[c].isna().to_numpy() & (col_run < max(1, int(shutdown_min_steps)))
                out.loc[fill_mask, c] = float(med_val)

        after_rem = int(out.loc[apply_rows, preds].isna().sum().sum())
        model_imputed_cells = max(0, before_rem - after_rem)

    verify_no_target_fabrication(raw_target, out[target])
    summary = {
        "strategy": strategy,
        "short_gap_max_steps": int(short_gap_max_steps),
        "shutdown_min_steps": int(shutdown_min_steps),
        "shutdown_rows": int(np.sum(shutdown_arr)),
        "short_interpolated_cells": int(short_interpolated_cells),
        "model_imputed_cells": int(model_imputed_cells),
        "added_flag_columns": added_flag_cols,
        "target_missing_before": int(raw_target.isna().sum()),
        "target_missing_after": int(out[target].isna().sum()),
    }
    return out, summary


def apply_non_routine_events(
    df: pd.DataFrame,
    events: Sequence[NonRoutineEventSpec | Mapping[str, Any]],
    *,
    target: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[str]]:
    """Apply declared Non-Routine Events (`exclude`, `flag`, or `adjust_indicator`) to ``df``."""
    work = df.copy()
    records: list[dict[str, Any]] = []
    indicator_cols: list[str] = []
    if not events or not isinstance(work.index, pd.DatetimeIndex):
        return work, records, indicator_cols

    for raw_ev in events:
        ev = raw_ev if isinstance(raw_ev, NonRoutineEventSpec) else NonRoutineEventSpec.from_dict(raw_ev)
        if ev.affected_targets and target not in ev.affected_targets:
            continue
        t0, t1 = pd.Timestamp(ev.start), pd.Timestamp(ev.end)
        if work.index.tz is not None:
            if t0.tzinfo is None:
                t0 = t0.tz_localize(work.index.tz)
            if t1.tzinfo is None:
                t1 = t1.tz_localize(work.index.tz)
        else:
            if t0.tzinfo is not None:
                t0 = t0.tz_convert("UTC").tz_localize(None)
            if t1.tzinfo is not None:
                t1 = t1.tz_convert("UTC").tz_localize(None)

        mask = (work.index >= t0) & (work.index <= t1)
        affected_n = int(np.sum(mask))
        if ev.action == "exclude" and affected_n > 0 and target in work.columns:
            work.loc[mask, target] = np.nan
        elif ev.action == "adjust_indicator":
            slug = re.sub(r"[^a-zA-Z0-9_]+", "_", ev.name).strip("_").lower() or "event"
            col_name = f"nre__{slug}"
            work[col_name] = mask.astype(float)
            indicator_cols.append(col_name)

        records.append(
            {
                "name": ev.name,
                "start": ev.start,
                "end": ev.end,
                "action": ev.action,
                "affected_rows": affected_n,
                "reason": ev.reason,
            }
        )
    return work, records, indicator_cols


def robust_bounds(x: pd.Series, thr: float) -> tuple[float, float]:
    med = x.median()
    mad = (x - med).abs().median()
    s = 1.4826 * mad if mad > 0 else x.std(ddof=0) or 1.0
    return float(med - thr * s), float(med + thr * s)


def detect_outliers(method: str, X: pd.DataFrame, thr: float, seed: int) -> pd.DataFrame:
    """Return boolean frame (same shape as X) for univariate methods; broadcast row flag for multivariate."""
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
            flags[c] = (X[c] < q1 - thr * 0.4286 * i) | (X[c] > q3 + thr * 0.4286 * i)
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
        ae = MLPRegressor(
            hidden_layer_sizes=(max(2, A.shape[1]), k, max(2, A.shape[1])),
            max_iter=300,
            random_state=seed,
        ).fit(A, A)
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


def apply_outlier_action_policy(
    work: pd.DataFrame,
    *,
    target: str,
    predictors: Sequence[str],
    adj: pd.DataFrame,
    flags: pd.DataFrame,
    method: str,
    threshold: float,
    treatment: str,
    policy: OutlierActionPolicy | str,
    user_approved_rows: Sequence[Any] | bool | None = None,
    exclusion_log: list[dict] | None = None,
) -> tuple[pd.DataFrame, OutlierPolicyRecord]:
    """Execute Outlier Action Policy (`flag_only | ask_user | auto_apply`) and record training-set delta (Gate G2)."""
    pol = policy if isinstance(policy, OutlierActionPolicy) else OutlierActionPolicy(str(policy).strip().lower())
    out = work.copy()
    nums = [c for c in predictors if c in out.columns and c in flags.columns]

    any_row_mask = flags.any(axis=1)
    target_row_mask = flags[target] if target in flags.columns else pd.Series(False, index=out.index)
    flagged_rows = [str(ix) for ix in out.index[any_row_mask]]
    flagged_target_rows = [str(ix) for ix in out.index[target_row_mask]]
    rule_desc = f"{method}(threshold={threshold:g}, treatment={treatment})"

    y_before = out[target].dropna()
    n_before = int(len(y_before))
    mean_before = float(y_before.mean()) if n_before else 0.0
    std_before = float(y_before.std(ddof=0)) if n_before > 1 else 0.0

    effective_flags = pd.DataFrame(False, index=flags.index, columns=flags.columns)
    applied = False
    pending = False

    if pol == OutlierActionPolicy.FLAG_ONLY or method == "none" or treatment == "flag":
        applied = False
        pending = False
    elif pol == OutlierActionPolicy.ASK_USER:
        if user_approved_rows is None or user_approved_rows is False:
            applied = False
            pending = bool(len(flagged_rows) > 0)
        else:
            if user_approved_rows is True or user_approved_rows == "*":
                effective_flags = flags.copy()
            else:
                approved_set = {str(x) for x in user_approved_rows}
                keep_idx = pd.Index([str(ix) in approved_set for ix in flags.index])
                effective_flags.loc[keep_idx.to_numpy(dtype=bool), :] = flags.loc[keep_idx.to_numpy(dtype=bool), :]
            applied = bool(effective_flags.any().any())
            pending = False
    elif pol == OutlierActionPolicy.AUTO_APPLY:
        effective_flags = flags.copy()
        applied = bool(effective_flags.any().any())
        pending = False

    if applied:
        eff_target_mask = effective_flags[target] if target in effective_flags.columns else pd.Series(False, index=out.index)
        n_t = int(eff_target_mask.sum())
        if treatment in ("remove", "winsorize", "interpolate") and n_t:
            idxs = out.index[eff_target_mask]
            out.loc[idxs, target] = np.nan
            if exclusion_log is not None:
                _excl(
                    exclusion_log,
                    f"{n_t} target rows",
                    "outlier",
                    f"Flagged by {method} (thr {threshold}, policy={pol.value}); excluded from training ('{treatment}' - target values are never modified). First: {idxs[0]}",
                    n_t,
                )
        for c in nums:
            k = int(effective_flags[c].sum())
            if not k:
                continue
            if treatment == "winsorize" or (treatment == "remove" and method in ("robust_z", "mad", "iqr")):
                lo, hi = robust_bounds(adj[c].dropna(), threshold)
                out[c] = (out[c] - adj[c]) + adj[c].clip(lo, hi)
                if exclusion_log is not None:
                    _excl(
                        exclusion_log,
                        f"{k} values of {c}",
                        "outlier",
                        f"Winsorised to the profile band [{lo:.4g}, {hi:.4g}] around the typical value ({method}, policy={pol.value}).",
                        k,
                    )
            elif treatment == "interpolate":
                out.loc[effective_flags[c], c] = np.nan
                if exclusion_log is not None:
                    _excl(
                        exclusion_log,
                        f"{k} values of {c}",
                        "outlier",
                        f"Replaced by bounded time interpolation ({method}, policy={pol.value}).",
                        k,
                    )

    y_after = out[target].dropna()
    n_after = int(len(y_after))
    mean_after = float(y_after.mean()) if n_after else 0.0
    std_after = float(y_after.std(ddof=0)) if n_after > 1 else 0.0

    out.attrs["outlier_flags"] = flags.to_dict(orient="list")
    record = OutlierPolicyRecord(
        policy=pol.value,
        rule=rule_desc,
        treatment=treatment,
        flagged_rows=flagged_rows,
        flagged_target_rows=flagged_target_rows,
        flagged_count=len(flagged_rows),
        applied=applied,
        pending_user_confirmation=pending,
        n_train_before=n_before,
        n_train_after=n_after,
        training_set_delta=n_after - n_before,
        target_mean_delta=round(mean_after - mean_before, 6),
        target_std_delta=round(std_after - std_before, 6),
    )
    return out, record


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
    return {
        "score": round(total, 1),
        "completeness": round(completeness, 4),
        "validity": round(validity, 4),
        "uniqueness": round(uniqueness, 4),
        "regularity": round(regularity, 4),
    }


def profile(df: pd.DataFrame) -> pd.DataFrame:
    return df.describe(include="all").T


def preprocess_target(
    df: pd.DataFrame,
    target: str,
    all_targets: list[str],
    g: Granularity,
    auto: AutoLog,
    seed: int,
    n_dups: int = 0,
    *,
    schema: DatasetSchema | Mapping[str, Any] | None = None,
) -> Prep:
    log: list[dict] = []
    notes: list[str] = []
    if target not in df.columns:
        raise ValueError(f"Target '{target}' not found after temporal preparation.")

    resolved_schema = coerce_dataset_schema(schema or auto.config.get("dataset_schema"))
    raw_target_snapshot = df[target].copy()
    work = df.copy()

    # Apply Non-Routine Events if declared in DatasetSchema or config.
    nre_list: list[Any] = []
    if resolved_schema is not None and resolved_schema.non_routine_events:
        nre_list.extend(resolved_schema.non_routine_events)
    elif auto.config.get("non_routine_events"):
        nre_list.extend(auto.config["non_routine_events"])

    nre_records: list[dict[str, Any]] = []
    nre_indicator_cols: list[str] = []
    if nre_list:
        work, nre_records, nre_indicator_cols = apply_non_routine_events(work, nre_list, target=target)
        for nr in nre_records:
            if nr["action"] == "exclude" and nr["affected_rows"] > 0:
                _excl(
                    log,
                    f"NRE '{nr['name']}' ({nr['affected_rows']} rows)",
                    "non-routine-event",
                    f"Excluded window {nr['start']}..{nr['end']}: {nr['reason'] or 'declared non-routine event'}",
                    nr["affected_rows"],
                )
            elif nr["action"] == "flag" and nr["affected_rows"] > 0:
                notes.append(f"Non-routine event '{nr['name']}' flagged ({nr['affected_rows']} rows: {nr['start']}..{nr['end']}).")

    y_valid = work[target].notna().sum()
    if y_valid < 8:
        raise ValueError(f"Target '{target}' has only {int(y_valid)} valid observations - cannot build a baseline.")

    schema_targets = set(all_targets)
    group_cols: list[str] = []
    weight_col: str | None = None
    ignored_by_role: set[str] = set()
    if resolved_schema is not None:
        schema_targets.update(resolved_schema.target_columns)
        group_cols = [c for c in resolved_schema.group_columns if c in work.columns and c != target]
        weight_col = resolved_schema.weight_column if resolved_schema.weight_column in work.columns else None
        ignored_by_role.update(resolved_schema.ignored_columns)
        for ic in resolved_schema.ignored_columns:
            if ic not in work.columns:
                _excl(log, ic, "column", "Excluded by DatasetSchema ColumnRole.IGNORE (role=ignore)")

    cand = [c for c in work.columns if c != target]
    keep: list[str] = []

    # ---- roles & other targets (leakage rule per target)
    for c in cand:
        if c in schema_targets:
            _excl(
                log,
                c,
                "leakage",
                f"Other TARGET column ('{c}') is never used as a predictor of '{target}' (simultaneous energy flow).",
            )
        elif c in ignored_by_role:
            _excl(log, c, "role-ignore", f"Column '{c}' has DatasetSchema role='ignore'.")
        elif c == weight_col:
            notes.append(f"Column '{c}' retained as observation weight (role='weight'), excluded from predictors.")
        elif c in group_cols:
            notes.append(f"Column '{c}' retained as grouping metadata (role='group'), excluded from predictors.")
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
                _excl(
                    log,
                    c,
                    "non-numeric",
                    f"Text column with {nun} distinct values is not usable as predictor (encode numerically first).",
                    nun,
                )
    nums = [c for c in keep if c not in cats]

    # ---- missing rate
    max_missing = auto.decide(
        "max_missing_rate",
        lambda: (0.40, "default engineering limit", "columns >40 % missing carry too little information"),
    )
    for c in list(nums):
        r = work[c].isna().mean()
        if r > max_missing:
            nums.remove(c)
            _excl(log, c, "missing", f"Missing rate {r:.1%} exceeds the limit {max_missing:.0%}.", r)

    # ---- constant / near-constant
    min_var = auto.decide(
        "min_variance",
        lambda: (1e-8, "default numerical tolerance", "relative variance below 1e-8 means constant"),
    )
    for c in list(nums):
        if c in nre_indicator_cols:
            if work[c].nunique(dropna=True) <= 1:
                nums.remove(c)
            continue
        s = work[c].dropna()
        if s.nunique() <= 1 or (s.std() ** 2) / (max(abs(s.mean()), 1e-12) ** 2 + 1e-12) < min_var:
            nums.remove(c)
            _excl(log, c, "constant", "Constant / zero-variance column carries no information.", s.nunique())
    for c in list(cats):
        if work[c].nunique(dropna=True) <= 1:
            cats.remove(c)
            _excl(log, c, "constant", "Constant categorical column.")

    # ---- duplicate columns
    seen: dict[bytes, str] = {}
    for c in list(nums):
        key = pd.util.hash_pandas_object(work[c].round(9), index=False).values.tobytes()
        if key in seen:
            nums.remove(c)
            _excl(log, c, "duplicate-column", f"Identical to column '{seen[key]}'.")
        else:
            seen[key] = c

    # ---- leakage (energy-like, almost perfect correlation with target)
    leak_thr = auto.decide(
        "leakage_threshold",
        lambda: (0.99, "default", "|r|>=0.99 with target AND energy-like name => leakage"),
    )
    for c in list(nums):
        pair = work[[c, target]].dropna()
        if len(pair) > 5:
            r = abs(pair[c].corr(pair[target]))
            spec_c = resolved_schema.get(c) if resolved_schema is not None else None
            is_energy_flow = bool(
                (spec_c is not None and not spec_c.suggested_by_heuristic and spec_c.quantity_type is not None and spec_c.quantity_type.value == "flow")
                or ( (spec_c is None or spec_c.suggested_by_heuristic) and ENERGY_LIKE.search(c) )
            )
            if r >= leak_thr and (is_energy_flow or r >= 0.9995):
                nums.remove(c)
                _excl(
                    log,
                    c,
                    "leakage",
                    f"|r|={r:.4f} with target and energy-like/derived column: likely a component or copy of the target.",
                    r,
                )
            elif r >= leak_thr:
                notes.append(
                    f"WARNING: '{c}' correlates |r|={r:.4f} with '{target}' (kept: not energy-like; verify it is a legitimate driver)."
                )

    # ---- outliers & Outlier Action Policy (flag_only | ask_user | auto_apply)
    sk = float(abs(stats.skew(work[target].dropna()))) if y_valid > 3 else 0.0
    n = len(work)

    def _om():
        if y_valid < 30:
            return "none", f"n={int(y_valid)} < 30", "too few observations for reliable outlier statistics"
        if sk > 2:
            return "mad", f"target |skew|={sk:.2f} > 2", "heavily skewed -> median/MAD estimator"
        return (
            "robust_z",
            f"target |skew|={sk:.2f} <= 2, n={n}",
            "approximately symmetric -> robust z-score on calendar-adjusted values",
        )

    method = auto.decide("outlier_method", _om)
    thr = auto.decide(
        "outlier_threshold",
        lambda: (3.5 if sk <= 2 else 4.5, f"|skew|={sk:.2f}", "3.5 (Iglewicz-Hoaglin); 4.5 for skewed variables"),
    )
    cols_o = [target] + nums
    univariate = method in ("robust_z", "mad", "iqr")
    adj = pd.DataFrame({c: calendar_adjust(work[c], g) for c in cols_o}) if univariate else work[cols_o]
    if univariate and g.tier in ("subdaily", "daily"):
        notes.append(
            "Outlier statistics were computed on calendar-adjusted values (time-of-week median removed) so that legitimate schedule effects are not flagged."
        )
    flags = detect_outliers(method, adj, thr, seed)
    row_flag_target = flags[target]
    share = float(row_flag_target.mean())
    any_flag = float(flags.any(axis=1).mean())

    def _ot():
        if method == "none":
            return "flag", "no detection performed", "nothing to treat"
        if share <= 0.02:
            return (
                "remove",
                f"target flagged share={share:.2%} <= 2 %",
                "few extreme points: remove from training (target never altered)",
            )
        return (
            "flag",
            f"target flagged share={share:.2%} > 2 %",
            "too many to be errors (likely genuine operating regimes): flag only, keep",
        )

    treat = auto.decide("outlier_treatment", _ot)

    cfg_policy = auto.config.get("outlier_action_policy", AUTO)
    if not is_auto(cfg_policy):
        policy_val = str(
            auto.decide(
                "outlier_action_policy",
                lambda: ("flag_only", "explicit", "user-configured outlier action policy"),
            )
        )
    elif resolved_schema is not None and not all(cs.suggested_by_heuristic for cs in resolved_schema.columns):
        # Explicit DatasetSchema carries its modelled outlier_policy.
        policy_val = str(
            auto.decide(
                "outlier_action_policy",
                lambda: (
                    resolved_schema.outlier_policy.value,
                    f"DatasetSchema.outlier_policy='{resolved_schema.outlier_policy.value}'",
                    "apply modelled DatasetSchema outlier action policy",
                ),
            )
        )
    else:
        default_pol = "flag_only" if treat == "flag" else "auto_apply"
        policy_val = str(
            auto.decide(
                "outlier_action_policy",
                lambda: (
                    default_pol,
                    f"treatment='{treat}', flagged_share={share:.2%}",
                    "AUTO applies small-share outlier exclusion automatically and flags larger regime shares",
                ),
            )
        )

    user_approved = auto.config.get("approved_outlier_rows")
    work, policy_record = apply_outlier_action_policy(
        work,
        target=target,
        predictors=nums,
        adj=adj,
        flags=flags,
        method=method,
        threshold=float(thr),
        treatment=str(treat),
        policy=policy_val,
        user_approved_rows=user_approved,
        exclusion_log=log,
    )

    n_t = int(row_flag_target.sum())
    out_info = {
        "method": method,
        "threshold": thr,
        "treatment": treat,
        "policy": policy_record.policy,
        "target_flagged": n_t,
        "target_share": share,
        "any_flagged_share": any_flag,
        "per_column": {c: int(flags[c].sum()) for c in cols_o},
        "action_policy_record": policy_record.as_dict(),
    }
    if policy_record.pending_user_confirmation:
        notes.append(
            f"Outlier Action Policy 'ask_user': {policy_record.flagged_count} flagged row(s) await user confirmation (0 rows mutated)."
        )

    # ---- missing values in predictors (DAT-5 leak-free bounded policy; target NEVER imputed)
    miss_rate = float(work[nums].isna().mean().mean()) if nums else 0.0

    def _ms():
        if miss_rate == 0:
            return "linear", "no missing predictor values", "irrelevant (kept as default)"
        if miss_rate < 0.05:
            return "linear", f"mean missing rate={miss_rate:.2%} < 5 %", "bounded time-linear interpolation (short gaps)"
        if miss_rate < 0.20 and n <= 20000:
            return "knn", f"mean missing rate={miss_rate:.2%} in 5-20 %", "kNN imputation on standardised predictors"
        return "median", f"mean missing rate={miss_rate:.2%} >= 20 % or n>20000", "median imputation (robust, cheap)"

    strat = str(auto.decide("missing_strategy", _ms))
    if nums and work[nums].isna().any().any():
        before = int(work[nums].isna().sum().sum())
        short_limit = max(3, int(round(min(g.steps_per_day, 6)))) if g.steps_per_day >= 1 else 3
        shutdown_steps = max(24, int(round(g.steps_per_day))) if g.steps_per_day >= 1 else 12
        if strat == "drop":
            work = work.dropna(subset=nums)
            _excl(log, f"{before} missing predictor cells", "imputation", "Dropped rows with missing predictors.", before)
        else:
            work, miss_summary = apply_leak_free_missing_policy(
                work,
                target,
                nums,
                short_gap_max_steps=short_limit,
                shutdown_min_steps=shutdown_steps,
                strategy=strat,
                seed=seed,
            )
            for fc in miss_summary["added_flag_columns"]:
                if fc not in nums:
                    nums.append(fc)
            imputed_total = miss_summary["short_interpolated_cells"] + miss_summary["model_imputed_cells"]
            _excl(
                log,
                f"{imputed_total} of {before} missing predictor cells",
                "imputation",
                f"Handled via leak-free '{strat}' policy (shutdown rows preserved: {miss_summary['shutdown_rows']}).",
                imputed_total,
            )
    work[cats] = work[cats].ffill().bfill() if cats else work[cats]

    # Enforce DAT-5 "no fabrication" invariant on target!
    verify_no_target_fabrication(raw_target_snapshot, work[target])

    # ---- multicollinearity
    corr_thr = auto.decide("corr_threshold", lambda: (0.95, "default", "|r|>=0.95 => redundant pair"))
    vif_thr = auto.decide(
        "vif_threshold",
        lambda: (
            (5.0, f"n={n} < 100", "stricter VIF for small samples")
            if n < 100
            else (10.0, f"n={n}", "VIF>10 is the standard multicollinearity limit")
        ),
    )
    if len(nums) > 1:
        tcorr = work[nums + [target]].corr(method="spearman")[target].abs()
        P = work[nums].corr().abs()
        S = work[nums].corr(method="spearman").abs()
        M = np.maximum(P.values, S.values)
        pairs = [
            (M[i, j], nums[i], nums[j])
            for i in range(len(nums))
            for j in range(i + 1, len(nums))
            if M[i, j] >= corr_thr
        ]
        dropped: set[str] = set()
        for r, a, b in sorted(pairs, reverse=True):
            if a in dropped or b in dropped:
                continue
            worse, better = (a, b) if tcorr.get(a, 0) < tcorr.get(b, 0) else (b, a)
            dropped.add(worse)
            _excl(
                log,
                worse,
                "collinearity",
                f"|r|={r:.3f} with '{better}' (>= {corr_thr}); kept the one more correlated with the target.",
                r,
            )
        nums = [c for c in nums if c not in dropped]
        while len(nums) > 2:
            filled = work[nums].fillna(work[nums].median()).fillna(0.0)
            v = vif_table(filled)
            if v.max() <= vif_thr:
                break
            worst = v.idxmax()
            nums.remove(worst)
            _excl(log, worst, "vif", f"VIF={v.max():.1f} > {vif_thr}; iterative pruning.", v.max())
        if len(nums) > 1:
            Zs = work[nums].fillna(work[nums].median()).fillna(0.0)
            Zs = (Zs - Zs.mean()) / Zs.std(ddof=0).replace(0, 1)
            notes.append(f"Condition number of the retained standardised predictors: {np.linalg.cond(Zs.values):.1f}")

    extra_meta_cols = [c for c in ([weight_col] if weight_col else []) + group_cols if c and c in work.columns]
    ordered_cols = list(dict.fromkeys([target] + nums + cats + extra_meta_cols))
    out = work[ordered_cols].copy()
    q = quality_score(
        df[[target] + [c for c in df.columns if c != target]],
        n_dups,
        int(df.isna().all(axis=1).sum()),
        any_flag,
    )
    return Prep(
        out,
        nums,
        cats,
        log,
        q,
        notes,
        out_info,
        group_cols=group_cols,
        weight_col=weight_col,
        nre_applied=nre_records,
    )


__all__ = [
    "ColumnRole",
    "FabricationError",
    "OutlierPolicyRecord",
    "Prep",
    "apply_leak_free_missing_policy",
    "apply_non_routine_events",
    "apply_outlier_action_policy",
    "calendar_adjust",
    "detect_outliers",
    "preprocess_target",
    "profile",
    "quality_score",
    "robust_bounds",
    "verify_no_target_fabrication",
    "vif_table",
]
