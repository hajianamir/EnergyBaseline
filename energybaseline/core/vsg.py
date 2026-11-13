"""Variable Selection Gate (VSG): pre-test profiling, AUTO screening, overrides, and gate lock (VSG-1..9, PIPE-1, AC-07).

Key compliance guarantees:
* Every ``(target, variable)`` pair defaults to ``AUTO`` and resolves from empirical data
  signals (missingness, relative variance, Pearson ``r``, Spearman ``rho``, quantile-binned
  mutual information ``I(X; Y)`` preserving nonlinear degree-day drivers, iterative VIF
  multicollinearity grouping, and non-disableable integrity rules).
* Non-disableable integrity rules (:mod:`energybaseline.core.integrity`) block self-target,
  sibling-target (MT-2), affine/sub-meter leakage, time-order, and duplicate/DST violations;
  user overrides and cross-target copies cannot bypass them.
* ``ComplianceGateError`` blocks any inferential test execution until VSG (and TCP) are
  explicitly accepted (PIPE-1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from .auto import AUTO, AutoLog, is_auto
from .integrity import (
    IntegrityReport,
    IntegrityViolationError,
    fast_linear_leakage_metrics,
    run_integrity_checks,
)
from .provenance import ProvenanceLedger, ProvenancePhase, ProvenanceSource, normalize_phase
from .schema import Availability, ColumnRole, DatasetSchema, Inclusion, coerce_dataset_schema

_SPARK_BARS = "▁▂▃▄▅▆▇█"
_WEATHER_PAT = re.compile(r"(temp|oat|hdd|cdd|degree|humid|rh|wind|solar|irrad|cloud|enthalpy|wetbulb|dew)", re.I)
_PROD_PAT = re.compile(r"(prod|output|ton|unit|batch|occup|headcount|hour|shift|flow|throughput|load|schedule)", re.I)
_DOMAIN_PRIORITY_PAT = re.compile(r"(temp|oat|hdd|cdd|prod|occup|flow|throughput)", re.I)


class VSGValidationError(ValueError):
    """Raised when VSG inputs, presets, or override arguments are invalid."""


class ComplianceGateError(RuntimeError):
    """Raised when an inferential stage is invoked before VSG and TCP gates are accepted (PIPE-1)."""


class VariableDisposition(str, Enum):
    """Resolved disposition of a candidate variable for a specific target."""

    INCLUDE = "include"
    EXCLUDE = "exclude"
    COLLINEAR_GROUPED = "collinear_grouped"


VALID_PRESETS: frozenset[str] = frozenset(
    {
        "auto_recommended",
        "weather_only",
        "production_and_weather",
        "strict_known_in_advance",
        "include_all_valid",
    }
)


def _normalize_requested_disposition(val: str) -> str:
    if is_auto(val):
        return AUTO
    norm = str(val).strip().lower()
    aliases = {
        "include": VariableDisposition.INCLUDE.value,
        "keep": VariableDisposition.INCLUDE.value,
        "exclude": VariableDisposition.EXCLUDE.value,
        "drop": VariableDisposition.EXCLUDE.value,
        "collinear_grouped": VariableDisposition.COLLINEAR_GROUPED.value,
        "grouped": VariableDisposition.COLLINEAR_GROUPED.value,
    }
    if norm not in aliases:
        raise VSGValidationError(
            f"Unsupported variable disposition {val!r}; expected AUTO, include, exclude, or collinear_grouped."
        )
    return aliases[norm]


def _sparkline_from_counts(counts: Sequence[int]) -> str:
    if not counts or max(counts) <= 0:
        return ""
    hi = max(counts)
    chars: list[str] = []
    for c in counts:
        idx = min(len(_SPARK_BARS) - 1, int(round((c / hi) * (len(_SPARK_BARS) - 1))))
        chars.append(_SPARK_BARS[idx])
    return "".join(chars)


def _mini_histogram(series: pd.Series, bins: int = 8) -> dict[str, Any]:
    s_clean = series.dropna()
    if len(s_clean) == 0:
        return {"bin_edges": [], "counts": [], "kind": "empty", "sparkline": ""}
    if pd.api.types.is_numeric_dtype(s_clean) and not pd.api.types.is_bool_dtype(s_clean):
        vals = pd.to_numeric(s_clean, errors="coerce").dropna().to_numpy(dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            return {"bin_edges": [], "counts": [], "kind": "empty", "sparkline": ""}
        if float(np.min(vals)) == float(np.max(vals)):
            v0 = float(vals[0])
            counts = [int(vals.size)]
            return {
                "bin_edges": [round(v0 - 0.5, 6), round(v0 + 0.5, 6)],
                "counts": counts,
                "kind": "numeric",
                "sparkline": _SPARK_BARS[-1],
            }
        eff_bins = max(2, min(int(bins), int(vals.size)))
        cnt_arr, edges_arr = np.histogram(vals, bins=eff_bins)
        counts = [int(x) for x in cnt_arr.tolist()]
        edges = [round(float(e), 6) for e in edges_arr.tolist()]
        return {
            "bin_edges": edges,
            "counts": counts,
            "kind": "numeric",
            "sparkline": _sparkline_from_counts(counts),
        }
    vc = s_clean.astype(str).value_counts().head(max(2, int(bins)))
    counts = [int(x) for x in vc.to_list()]
    return {
        "categories": [str(k) for k in vc.index.to_list()],
        "counts": counts,
        "kind": "categorical",
        "sparkline": _sparkline_from_counts(counts),
    }


def _binned_mutual_information(x: np.ndarray, y: np.ndarray, max_bins: int = 8) -> float:
    """Deterministic O(n) empirical mutual information I(X; Y) in nats over quantile bins."""
    mask = np.isfinite(x) & np.isfinite(y)
    n = int(mask.sum())
    if n < 12:
        return 0.0
    xv = x[mask]
    yv = y[mask]
    k = max(2, min(max_bins, int(math.sqrt(n / 2.0))))
    qx = np.unique(np.quantile(xv, np.linspace(0.0, 1.0, k + 1)))
    qy = np.unique(np.quantile(yv, np.linspace(0.0, 1.0, k + 1)))
    if qx.size < 3 or qy.size < 3:
        return 0.0
    bx = np.clip(np.digitize(xv, qx[1:-1], right=False), 0, qx.size - 2)
    by = np.clip(np.digitize(yv, qy[1:-1], right=False), 0, qy.size - 2)
    joint = np.zeros((qx.size - 1, qy.size - 1), dtype=float)
    np.add.at(joint, (bx, by), 1.0)
    joint /= float(n)
    px = joint.sum(axis=1, keepdims=True)
    py = joint.sum(axis=0, keepdims=True)
    denom = px @ py
    nz = (joint > 0.0) & (denom > 0.0)
    mi = float(np.sum(joint[nz] * np.log(joint[nz] / denom[nz])))
    return max(0.0, mi)


def _rank_spearman(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 4:
        return 0.0
    rx = pd.Series(x[mask]).rank(method="average").to_numpy(dtype=float)
    ry = pd.Series(y[mask]).rank(method="average").to_numpy(dtype=float)
    dx = rx - float(np.mean(rx))
    dy = ry - float(np.mean(ry))
    denom = float(np.sqrt(np.dot(dx, dx) * np.dot(dy, dy)))
    if denom <= 1e-18:
        return 0.0
    return float(np.clip(np.dot(dx, dy) / denom, -1.0, 1.0))


def _signed_pearson(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 4:
        return 0.0
    xv = x[mask]
    yv = y[mask]
    dx = xv - float(np.mean(xv))
    dy = yv - float(np.mean(yv))
    denom = float(np.sqrt(np.dot(dx, dx) * np.dot(dy, dy)))
    if denom <= 1e-18:
        return 0.0
    return float(np.clip(np.dot(dx, dy) / denom, -1.0, 1.0))


def _compute_vif_map(df: pd.DataFrame, numeric_cols: Sequence[str]) -> dict[str, float]:
    """Compute Variance Inflation Factor per numeric column via standardized correlation inverse."""
    cols = [c for c in numeric_cols if c in df.columns]
    if len(cols) <= 1:
        return {c: 1.0 for c in cols}
    sub = df[cols].apply(pd.to_numeric, errors="coerce")
    med = sub.median(numeric_only=True)
    filled = sub.fillna(med).fillna(0.0)
    stds = filled.std(ddof=0)
    varying = [c for c in cols if float(stds.get(c, 0.0)) > 1e-12]
    out: dict[str, float] = {c: 1.0 for c in cols}
    if len(varying) <= 1:
        return out
    corr = filled[varying].corr().to_numpy(dtype=float)
    corr = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, 1.0)
    try:
        inv_corr = np.linalg.pinv(corr + np.eye(len(varying)) * 1e-9)
        diag = np.diag(inv_corr)
        for c, dval in zip(varying, diag):
            out[c] = round(float(max(1.0, min(1e6, dval))), 4)
    except np.linalg.LinAlgError:
        pass
    return out


@dataclass(frozen=True)
class VariableProfile:
    """Pre-test descriptive & screening profile for a single variable (VSG-2, VSG-4)."""

    variable: str
    target: str
    dtype: str
    role: str
    availability: str
    inclusion: str
    unit: str
    n_total: int
    n_valid: int
    missing_count: int
    missing_pct: float
    unique_count: int
    mean: float | None
    std: float | None
    variance: float | None
    relative_variance: float | None
    min_val: float | None
    q25: float | None
    median: float | None
    q75: float | None
    max_val: float | None
    is_constant: bool
    is_near_constant: bool
    pearson_r: float
    spearman_rho: float
    mutual_info: float
    info_correlation: float
    vif: float
    leakage_suspicion: bool
    leakage_reason: str
    integrity_blocking: bool
    mini_histogram: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability,
            "dtype": self.dtype,
            "inclusion": self.inclusion,
            "info_correlation": self.info_correlation,
            "integrity_blocking": self.integrity_blocking,
            "is_constant": self.is_constant,
            "is_near_constant": self.is_near_constant,
            "leakage_reason": self.leakage_reason,
            "leakage_suspicion": self.leakage_suspicion,
            "max": self.max_val,
            "mean": self.mean,
            "median": self.median,
            "min": self.min_val,
            "mini_histogram": dict(self.mini_histogram),
            "missing_count": self.missing_count,
            "missing_pct": self.missing_pct,
            "mutual_info": self.mutual_info,
            "n_total": self.n_total,
            "n_valid": self.n_valid,
            "pearson_r": self.pearson_r,
            "q25": self.q25,
            "q75": self.q75,
            "relative_variance": self.relative_variance,
            "role": self.role,
            "spearman_rho": self.spearman_rho,
            "std": self.std,
            "target": self.target,
            "unique_count": self.unique_count,
            "unit": self.unit,
            "variable": self.variable,
            "variance": self.variance,
            "vif": self.vif,
        }


def pre_test_profile(
    df: pd.DataFrame,
    *,
    target: str,
    time_col: str | None = None,
    all_targets: Sequence[str] = (),
    schema: DatasetSchema | Mapping[str, Any] | None = None,
    bins: int = 8,
    near_constant_rel_var: float = 1e-8,
) -> dict[str, VariableProfile]:
    """Compute pre-test screening profiles for all candidate columns against ``target`` (VSG-2)."""
    if target not in df.columns:
        raise VSGValidationError(f"Target column {target!r} not found in DataFrame.")
    resolved_schema = coerce_dataset_schema(schema)
    eff_time = time_col or (resolved_schema.time_column if resolved_schema is not None else None)
    candidate_cols = [str(c) for c in df.columns if str(c) != eff_time and str(c) != target]

    integrity_rep = run_integrity_checks(
        df,
        target=target,
        time_col=eff_time,
        all_targets=all_targets,
        candidate_predictors=candidate_cols,
        schema=resolved_schema,
    )
    conflict_by_var: dict[str, list[str]] = {}
    for c in integrity_rep.conflicts:
        conflict_by_var.setdefault(c.variable, []).append(c.message)

    y_arr = pd.to_numeric(df[target], errors="coerce").to_numpy(dtype=float)
    num_candidates = [
        c
        for c in candidate_cols
        if c not in set(all_targets)
        and (pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_bool_dtype(df[c]))
    ]
    vif_map = _compute_vif_map(df, num_candidates)

    schema_by_name = {sp.name: sp for sp in resolved_schema.columns} if resolved_schema is not None else {}
    n_total = len(df)
    profiles: dict[str, VariableProfile] = {}
    for col in candidate_cols:
        s = df[col]
        spec = schema_by_name.get(col)
        role = spec.role.value if spec is not None else (ColumnRole.TARGET.value if col in set(all_targets) else ColumnRole.PREDICTOR.value)
        availability = str(spec.availability) if spec is not None else Availability.ALWAYS.value
        inclusion = spec.inclusion.value if spec is not None else Inclusion.KNOWN_IN_ADVANCE.value
        unit = spec.unit if spec is not None else ""

        miss_cnt = int(s.isna().sum())
        n_valid = n_total - miss_cnt
        miss_pct = round((100.0 * miss_cnt / n_total) if n_total > 0 else 100.0, 4)
        uniq = int(s.nunique(dropna=True))
        is_num = pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s)

        mean_v = std_v = var_v = rel_var = min_v = q25_v = med_v = q75_v = max_v = None
        pearson_r = spearman_rho = mi_val = info_corr = 0.0
        is_const = uniq <= 1
        is_near_const = is_const
        leak_susp = col in conflict_by_var
        leak_reason = "; ".join(conflict_by_var.get(col, []))

        if is_num and n_valid > 0:
            x_num = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
            finite_x = x_num[np.isfinite(x_num)]
            if finite_x.size > 0:
                mean_v = round(float(np.mean(finite_x)), 6)
                std_f = float(np.std(finite_x, ddof=1)) if finite_x.size > 1 else 0.0
                var_f = std_f * std_f
                std_v = round(std_f, 6)
                var_v = round(var_f, 8)
                rv = var_f / (max(abs(float(np.mean(finite_x))), 1e-12) ** 2 + 1e-12)
                rel_var = round(float(rv), 12)
                qs = np.quantile(finite_x, [0.0, 0.25, 0.5, 0.75, 1.0])
                min_v, q25_v, med_v, q75_v, max_v = (round(float(q), 6) for q in qs)
                is_near_const = bool(is_const or rv < near_constant_rel_var)

                pearson_r = round(_signed_pearson(x_num, y_arr), 6)
                spearman_rho = round(_rank_spearman(x_num, y_arr), 6)
                mi_val = round(_binned_mutual_information(x_num, y_arr), 6)
                info_corr = round(float(math.sqrt(max(0.0, 1.0 - math.exp(-2.0 * mi_val)))), 6)

                abs_r, rel_aff, n_pair = fast_linear_leakage_metrics(x_num, y_arr)
                if not leak_susp and n_pair >= 5 and abs_r >= 0.975:
                    leak_susp = True
                    leak_reason = f"Elevated linear coupling with target (|r|={abs_r:.4f}, rel_err={rel_aff:.3e})"

        profiles[col] = VariableProfile(
            variable=col,
            target=target,
            dtype=str(s.dtype),
            role=role,
            availability=availability,
            inclusion=inclusion,
            unit=unit,
            n_total=n_total,
            n_valid=n_valid,
            missing_count=miss_cnt,
            missing_pct=miss_pct,
            unique_count=uniq,
            mean=mean_v,
            std=std_v,
            variance=var_v,
            relative_variance=rel_var,
            min_val=min_v,
            q25=q25_v,
            median=med_v,
            q75=q75_v,
            max_val=max_v,
            is_constant=is_const,
            is_near_constant=is_near_const,
            pearson_r=pearson_r,
            spearman_rho=spearman_rho,
            mutual_info=mi_val,
            info_correlation=info_corr,
            vif=vif_map.get(col, 1.0),
            leakage_suspicion=leak_susp,
            leakage_reason=leak_reason,
            integrity_blocking=col in conflict_by_var,
            mini_histogram=_mini_histogram(s, bins=bins),
        )
    return profiles


@dataclass
class VariableDecision:
    """Resolved per-(target, variable) disposition with audit reason and provenance metadata."""

    target: str
    variable: str
    mode: str  # AUTO | USER_OVERRIDE | PRESET | CROSS_TARGET_COPY
    requested_disposition: str  # AUTO | include | exclude | collinear_grouped
    resolved_disposition: str  # include | exclude | collinear_grouped
    reason: str
    signals: str
    collinear_leader: str = ""
    integrity_blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "collinear_leader": self.collinear_leader,
            "integrity_blocked": self.integrity_blocked,
            "mode": self.mode,
            "reason": self.reason,
            "requested_disposition": self.requested_disposition,
            "resolved_disposition": self.resolved_disposition,
            "signals": self.signals,
            "target": self.target,
            "variable": self.variable,
        }


class VariableSelectionGate:
    """Compliance core Variable Selection Gate across one or more EnPI targets (VSG-1..9, PIPE-1)."""

    def __init__(
        self,
        df: pd.DataFrame,
        *,
        targets: Sequence[str],
        time_col: str | None = None,
        schema: DatasetSchema | Mapping[str, Any] | None = None,
        dataset_sha256: str = "",
        config: Mapping[str, Any] | None = None,
        auto_log: AutoLog | None = None,
        provenance: ProvenanceLedger | None = None,
    ) -> None:
        if not targets:
            raise VSGValidationError("VariableSelectionGate requires at least one target column.")
        for t in targets:
            if t not in df.columns:
                raise VSGValidationError(f"Target column {t!r} does not exist in DataFrame.")
        self._df = df
        self.targets: tuple[str, ...] = tuple(str(t) for t in targets)
        self.schema: DatasetSchema | None = coerce_dataset_schema(schema)
        self.time_col: str | None = time_col or (self.schema.time_column if self.schema is not None else None)
        self.dataset_sha256: str = str(
            dataset_sha256
            or (self.schema.dataset_sha256 if self.schema is not None else "")
            or hashlib.sha256(
                f"{list(df.columns)}|{len(df)}".encode("utf-8")
            ).hexdigest()
        )
        self.config: dict[str, Any] = dict(config or {})
        self.auto_log: AutoLog = auto_log if auto_log is not None else AutoLog(self.config, stage="vsg")
        self.provenance: ProvenanceLedger = (
            provenance if provenance is not None else ProvenanceLedger(dataset_sha256=self.dataset_sha256)
        )

        self.profiles_by_target: dict[str, dict[str, VariableProfile]] = {}
        self.decisions_by_target: dict[str, dict[str, VariableDecision]] = {}
        self.integrity_by_target: dict[str, IntegrityReport] = {}
        self._accepted_by_target: dict[str, bool] = {t: False for t in self.targets}

        for t in self.targets:
            self._resolve_target_auto(t, phase=ProvenancePhase.PRE_TEST.value, record_provenance=True)

    def _domain_relevance_score(self, prof: VariableProfile) -> float:
        rel = max(abs(prof.pearson_r), abs(prof.spearman_rho), prof.info_correlation)
        bonus = 0.05 if _DOMAIN_PRIORITY_PAT.search(prof.variable) else 0.0
        return rel + bonus

    def _resolve_target_auto(
        self,
        target: str,
        *,
        phase: str = ProvenancePhase.PRE_TEST.value,
        record_provenance: bool = True,
    ) -> None:
        t_auto = self.auto_log.for_target(target)
        n = len(self._df)
        profiles = pre_test_profile(
            self._df,
            target=target,
            time_col=self.time_col,
            all_targets=self.targets,
            schema=self.schema,
        )
        self.profiles_by_target[target] = profiles

        miss_vals = [p.missing_pct for p in profiles.values()] or [0.0]
        med_miss = float(np.median(miss_vals))
        max_miss_pct = float(
            t_auto.decide(
                "vsg_max_missing_pct",
                lambda: (
                    (25.0 if n < 60 else 40.0 if n < 1000 else 50.0),
                    f"n={n}, p={len(profiles)}, median_missing_pct={med_miss:.2f}%",
                    "maximum predictor missingness adapts to sample length (25% n<60, 40% n<1000, 50% n>=1000)",
                ),
                candidate_space="[10.0, 60.0]",
                stage="vsg",
            )
        )
        min_rel_var = float(
            t_auto.decide(
                "vsg_min_rel_variance",
                lambda: (
                    (1e-6 if n < 50 else 1e-8),
                    f"n={n}, candidate_count={len(profiles)}",
                    "near-constant relative-variance floor scaled by sample size",
                ),
                candidate_space="[1e-10, 1e-4]",
                stage="vsg",
            )
        )
        vif_limit = float(
            t_auto.decide(
                "vsg_vif_limit",
                lambda: (
                    (5.0 if n < 100 else 10.0),
                    f"n={n}, candidate_count={len(profiles)}",
                    "iterative VIF multicollinearity ceiling (5.0 for n<100, 10.0 for n>=100)",
                ),
                candidate_space="[2.5, 20.0]",
                stage="vsg",
            )
        )

        # Initial integrity pass on all candidate columns to catch self/sibling/leakage/time conflicts
        initial_integrity = run_integrity_checks(
            self._df,
            target=target,
            time_col=self.time_col,
            all_targets=self.targets,
            candidate_predictors=list(profiles.keys()),
            schema=self.schema,
            config=self.config,
        )
        blocking_vars = {c.variable for c in initial_integrity.blocking_conflicts}

        decisions: dict[str, VariableDecision] = {}
        surviving_numeric: list[str] = []

        for col, prof in profiles.items():
            sig_str = (
                f"miss={prof.missing_pct:.2f}%, uniq={prof.unique_count}, "
                f"r={prof.pearson_r:.3f}, rho={prof.spearman_rho:.3f}, "
                f"mi={prof.mutual_info:.3f}, vif={prof.vif:.2f}"
            )
            if col in blocking_vars or prof.integrity_blocking:
                reason = prof.leakage_reason or f"Blocked by non-disableable integrity rule for target '{target}'."
                decisions[col] = VariableDecision(
                    target=target,
                    variable=col,
                    mode="AUTO",
                    requested_disposition=AUTO,
                    resolved_disposition=VariableDisposition.EXCLUDE.value,
                    reason=reason,
                    signals=sig_str,
                    integrity_blocked=True,
                )
            elif prof.role == ColumnRole.IGNORE.value:
                decisions[col] = VariableDecision(
                    target=target,
                    variable=col,
                    mode="AUTO",
                    requested_disposition=AUTO,
                    resolved_disposition=VariableDisposition.EXCLUDE.value,
                    reason="Excluded because DatasetSchema role is 'ignore'.",
                    signals=sig_str,
                )
            elif prof.role in (ColumnRole.GROUP.value, ColumnRole.WEIGHT.value):
                decisions[col] = VariableDecision(
                    target=target,
                    variable=col,
                    mode="AUTO",
                    requested_disposition=AUTO,
                    resolved_disposition=VariableDisposition.EXCLUDE.value,
                    reason=f"Auxiliary '{prof.role}' column reserved for weighting/causal stratification, not direct regression.",
                    signals=sig_str,
                )
            elif prof.missing_pct > max_miss_pct:
                decisions[col] = VariableDecision(
                    target=target,
                    variable=col,
                    mode="AUTO",
                    requested_disposition=AUTO,
                    resolved_disposition=VariableDisposition.EXCLUDE.value,
                    reason=f"Missing rate {prof.missing_pct:.2f}% exceeds AUTO limit {max_miss_pct:.1f}%.",
                    signals=sig_str,
                )
            elif prof.is_constant or (
                prof.relative_variance is not None and prof.relative_variance < min_rel_var
            ):
                decisions[col] = VariableDecision(
                    target=target,
                    variable=col,
                    mode="AUTO",
                    requested_disposition=AUTO,
                    resolved_disposition=VariableDisposition.EXCLUDE.value,
                    reason="Constant or near-zero variance predictor carries no explanatory signal.",
                    signals=sig_str,
                )
            else:
                rel_linear = max(abs(prof.pearson_r), abs(prof.spearman_rho))
                if prof.info_correlation > rel_linear + 0.12 and prof.mutual_info > 0.05:
                    reason = (
                        f"Included via nonlinear mutual-information relevance (MI={prof.mutual_info:.3f}, "
                        f"info_corr={prof.info_correlation:.3f} > linear |r|={ abs(prof.pearson_r):.3f}; "
                        "preserves piecewise/degree-day response)."
                    )
                else:
                    reason = (
                        f"Included after passing missingness ({prof.missing_pct:.1f}% <= {max_miss_pct:.1f}%), "
                        f"variance, and leakage screening (|r|={abs(prof.pearson_r):.3f}, |rho|={abs(prof.spearman_rho):.3f})."
                    )
                decisions[col] = VariableDecision(
                    target=target,
                    variable=col,
                    mode="AUTO",
                    requested_disposition=AUTO,
                    resolved_disposition=VariableDisposition.INCLUDE.value,
                    reason=reason,
                    signals=sig_str,
                )
                if prof.mean is not None:
                    surviving_numeric.append(col)

        # Iterative VIF / pairwise multicollinearity grouping (VSG-5, VSG-8)
        active_num = list(surviving_numeric)
        while len(active_num) > 1:
            vifs = _compute_vif_map(self._df, active_num)
            worst_col = max(active_num, key=lambda c: (vifs.get(c, 1.0), -self._domain_relevance_score(profiles[c]), c))
            worst_vif = vifs.get(worst_col, 1.0)
            if worst_vif <= vif_limit:
                break
            # Identify strongest correlated surviving partner as collinear group leader
            others = [c for c in active_num if c != worst_col]
            x_w = pd.to_numeric(self._df[worst_col], errors="coerce").to_numpy(dtype=float)
            best_leader = max(
                others,
                key=lambda c: (
                    abs(_signed_pearson(x_w, pd.to_numeric(self._df[c], errors="coerce").to_numpy(dtype=float))),
                    self._domain_relevance_score(profiles[c]),
                ),
            )
            leader_r = abs(
                _signed_pearson(
                    x_w, pd.to_numeric(self._df[best_leader], errors="coerce").to_numpy(dtype=float)
                )
            )
            # Between worst_col and best_leader, keep the higher domain/relevance priority predictor
            drop_col, keep_col = (
                (worst_col, best_leader)
                if self._domain_relevance_score(profiles[best_leader]) >= self._domain_relevance_score(profiles[worst_col])
                else (best_leader, worst_col)
            )
            drop_vif = vifs.get(drop_col, worst_vif)
            prev_dec = decisions[drop_col]
            decisions[drop_col] = VariableDecision(
                target=target,
                variable=drop_col,
                mode="AUTO",
                requested_disposition=AUTO,
                resolved_disposition=VariableDisposition.COLLINEAR_GROUPED.value,
                collinear_leader=keep_col,
                reason=(
                    f"Grouped as collinear with '{keep_col}' (VIF={drop_vif:.2f} > {vif_limit:.1f}, "
                    f"|r|={leader_r:.3f}); '{keep_col}' retained by domain/relevance priority."
                ),
                signals=prev_dec.signals,
            )
            active_num.remove(drop_col)

        self.decisions_by_target[target] = decisions
        self._refresh_integrity(target)

        for col, dec in decisions.items():
            t_auto.note(
                f"vsg.{col}",
                dec.resolved_disposition,
                dec.signals,
                dec.reason,
                stage="vsg",
            )
            if record_provenance:
                self.provenance.append(
                    entity_type="vsg_variable",
                    entity_id=col,
                    target=target,
                    source=ProvenanceSource.AUTO_RULE,
                    phase=phase,
                    previous_value=None,
                    new_value=dec.resolved_disposition,
                    reason=dec.reason,
                )

    def _refresh_integrity(self, target: str) -> IntegrityReport:
        included = [
            col
            for col, dec in self.decisions_by_target[target].items()
            if dec.resolved_disposition == VariableDisposition.INCLUDE.value
        ]
        rep = run_integrity_checks(
            self._df,
            target=target,
            time_col=self.time_col,
            all_targets=self.targets,
            candidate_predictors=included,
            schema=self.schema,
            config=self.config,
        )
        self.integrity_by_target[target] = rep
        return rep

    def included_variables(self, target: str) -> list[str]:
        if target not in self.decisions_by_target:
            raise VSGValidationError(f"Unknown target {target!r}.")
        return [
            col
            for col, dec in self.decisions_by_target[target].items()
            if dec.resolved_disposition == VariableDisposition.INCLUDE.value
        ]

    def excluded_variables(self, target: str) -> list[str]:
        if target not in self.decisions_by_target:
            raise VSGValidationError(f"Unknown target {target!r}.")
        return [
            col
            for col, dec in self.decisions_by_target[target].items()
            if dec.resolved_disposition == VariableDisposition.EXCLUDE.value
        ]

    def excluded_descriptive_profiles(self, target: str) -> list[dict[str, Any]]:
        """Return descriptive profiles + exclusion rationale for every excluded variable (VSG-4)."""
        if target not in self.decisions_by_target:
            raise VSGValidationError(f"Unknown target {target!r}.")
        out: list[dict[str, Any]] = []
        for col, dec in self.decisions_by_target[target].items():
            if dec.resolved_disposition == VariableDisposition.EXCLUDE.value:
                prof_dict = self.profiles_by_target[target][col].to_dict()
                prof_dict["exclusion_reason"] = dec.reason
                prof_dict["mode"] = dec.mode
                out.append(prof_dict)
        return out

    def override_variable(
        self,
        target: str,
        variable: str,
        disposition: str,
        *,
        reason: str,
        phase: str | ProvenancePhase = ProvenancePhase.PRE_TEST,
        source: ProvenanceSource = ProvenanceSource.USER_MODIFICATION,
    ) -> VariableDecision:
        """Apply an audited engineer override, enforcing non-disableable integrity rules (VSG-5, VSG-9)."""
        if target not in self.decisions_by_target:
            raise VSGValidationError(f"Unknown target {target!r}.")
        if variable == target:
            raise IntegrityViolationError(f"Target '{target}' can never be included as a predictor of itself.")
        if variable not in self.decisions_by_target[target]:
            raise VSGValidationError(f"Unknown candidate variable {variable!r} for target {target!r}.")
        if not (reason or "").strip():
            raise VSGValidationError("Interactive VSG override requires a non-empty audit reason (VSG-9).")

        phs = normalize_phase(phase)
        norm_disp = _normalize_requested_disposition(disposition)
        if norm_disp == AUTO:
            # Re-evaluate single target AUTO state and keep non-overridden variables
            prev_val = self.decisions_by_target[target][variable].resolved_disposition
            saved_overrides = {
                c: d
                for c, d in self.decisions_by_target[target].items()
                if c != variable and d.mode != "AUTO"
            }
            self._resolve_target_auto(target, phase=phs, record_provenance=False)
            for c, d in saved_overrides.items():
                self.decisions_by_target[target][c] = d
            self._refresh_integrity(target)
            new_dec = self.decisions_by_target[target][variable]
            self.provenance.append(
                entity_type="vsg_variable",
                entity_id=variable,
                target=target,
                source=source,
                phase=phs,
                previous_value=prev_val,
                new_value=new_dec.resolved_disposition,
                reason=reason.strip(),
            )
            self._accepted_by_target[target] = False
            return new_dec

        if norm_disp == VariableDisposition.INCLUDE.value:
            # Verify candidate against non-disableable integrity rules before permitting inclusion
            probe = run_integrity_checks(
                self._df,
                target=target,
                time_col=self.time_col,
                all_targets=self.targets,
                candidate_predictors=[variable],
                schema=self.schema,
                config=self.config,
            )
            var_conflicts = [c for c in probe.blocking_conflicts if c.variable == variable]
            if var_conflicts:
                raise IntegrityViolationError(
                    f"Cannot override '{variable}' to 'include' for target '{target}': "
                    + "; ".join(c.message for c in var_conflicts)
                )

        prev = self.decisions_by_target[target][variable]
        mode_label = (
            "PRESET"
            if source == ProvenanceSource.PRESET
            else "CROSS_TARGET_COPY"
            if source == ProvenanceSource.CROSS_TARGET_COPY
            else "USER_OVERRIDE"
        )
        updated = VariableDecision(
            target=target,
            variable=variable,
            mode=mode_label,
            requested_disposition=norm_disp,
            resolved_disposition=norm_disp,
            reason=reason.strip(),
            signals=prev.signals,
            collinear_leader="" if norm_disp != VariableDisposition.COLLINEAR_GROUPED.value else prev.collinear_leader,
            integrity_blocked=prev.integrity_blocked and norm_disp == VariableDisposition.EXCLUDE.value,
        )
        self.decisions_by_target[target][variable] = updated
        self._refresh_integrity(target)
        self._accepted_by_target[target] = False

        self.auto_log.for_target(target).record_override(
            f"vsg.{variable}",
            norm_disp,
            reason=reason.strip(),
            signals=f"engineer_override({source.value})",
            stage="vsg",
        )
        self.provenance.append(
            entity_type="vsg_variable",
            entity_id=variable,
            target=target,
            source=source,
            phase=phs,
            previous_value=prev.resolved_disposition,
            new_value=norm_disp,
            reason=reason.strip(),
        )
        return updated

    def bulk_set_disposition(
        self,
        target: str,
        variables: Sequence[str],
        disposition: str,
        *,
        reason: str = "bulk VSG action",
        phase: str | ProvenancePhase = ProvenancePhase.PRE_TEST,
        skip_integrity_blocked: bool = True,
    ) -> dict[str, str]:
        """Apply a bulk disposition across ``variables`` for ``target`` (VSG-4)."""
        if target not in self.decisions_by_target:
            raise VSGValidationError(f"Unknown target {target!r}.")
        outcomes: dict[str, str] = {}
        for var in variables:
            if var not in self.decisions_by_target[target]:
                raise VSGValidationError(f"Unknown variable {var!r} for target {target!r}.")
            cur = self.decisions_by_target[target][var]
            if skip_integrity_blocked and cur.integrity_blocked and _normalize_requested_disposition(disposition) == VariableDisposition.INCLUDE.value:
                outcomes[var] = "skipped_integrity_blocked"
                continue
            dec = self.override_variable(target, var, disposition, reason=reason, phase=phase)
            outcomes[var] = dec.resolved_disposition
        return outcomes

    def apply_preset(
        self,
        target: str,
        preset_name: str,
        *,
        phase: str | ProvenancePhase = ProvenancePhase.PRE_TEST,
    ) -> dict[str, str]:
        """Apply a named domain screening preset to ``target`` (VSG-4)."""
        if target not in self.decisions_by_target:
            raise VSGValidationError(f"Unknown target {target!r}.")
        preset = preset_name.strip().lower()
        if preset not in VALID_PRESETS:
            raise VSGValidationError(
                f"Unknown VSG preset {preset_name!r}; expected one of {sorted(VALID_PRESETS)}."
            )
        phs = normalize_phase(phase)
        self._resolve_target_auto(target, phase=phs, record_provenance=False)
        if preset == "auto_recommended":
            self.provenance.append(
                entity_type="vsg_preset",
                entity_id=preset,
                target=target,
                source=ProvenanceSource.PRESET,
                phase=phs,
                previous_value="custom",
                new_value="auto_recommended",
                reason="Reset target VSG dispositions to AUTO data-driven defaults.",
            )
            self._accepted_by_target[target] = False
            return {c: d.resolved_disposition for c, d in self.decisions_by_target[target].items()}

        profs = self.profiles_by_target[target]
        for col, prof in profs.items():
            dec = self.decisions_by_target[target][col]
            if dec.integrity_blocked or prof.is_constant or prof.missing_pct > 50.0:
                continue
            want_inc = False
            if preset == "weather_only":
                want_inc = bool(_WEATHER_PAT.search(col))
            elif preset == "production_and_weather":
                want_inc = bool(_WEATHER_PAT.search(col) or _PROD_PAT.search(col))
            elif preset == "strict_known_in_advance":
                want_inc = (
                    prof.inclusion == Inclusion.KNOWN_IN_ADVANCE.value
                    and dec.resolved_disposition == VariableDisposition.INCLUDE.value
                )
            elif preset == "include_all_valid":
                want_inc = prof.role not in (ColumnRole.IGNORE.value, ColumnRole.GROUP.value, ColumnRole.WEIGHT.value)

            desired = VariableDisposition.INCLUDE.value if want_inc else VariableDisposition.EXCLUDE.value
            if desired != dec.resolved_disposition:
                self.override_variable(
                    target,
                    col,
                    desired,
                    reason=f"Preset '{preset}' applied",
                    phase=phs,
                    source=ProvenanceSource.PRESET,
                )
        self._accepted_by_target[target] = False
        return {c: d.resolved_disposition for c, d in self.decisions_by_target[target].items()}

    def copy_target_decisions(
        self,
        source_target: str,
        dest_target: str,
        *,
        reason: str = "Copied dispositions from sibling target",
        phase: str | ProvenancePhase = ProvenancePhase.PRE_TEST,
    ) -> dict[str, str]:
        """Copy variable dispositions from ``source_target`` to ``dest_target`` while re-enforcing integrity (VSG-4)."""
        if source_target not in self.decisions_by_target:
            raise VSGValidationError(f"Unknown source_target {source_target!r}.")
        if dest_target not in self.decisions_by_target:
            raise VSGValidationError(f"Unknown dest_target {dest_target!r}.")
        if source_target == dest_target:
            return {c: d.resolved_disposition for c, d in self.decisions_by_target[dest_target].items()}

        phs = normalize_phase(phase)
        copied: dict[str, str] = {}
        src_map = self.decisions_by_target[source_target]
        dst_map = self.decisions_by_target[dest_target]
        for col in dst_map:
            if col == source_target:
                copied[col] = "blocked_by_integrity"
                continue
            if col not in src_map:
                continue
            src_dec = src_map[col]
            desired = src_dec.resolved_disposition
            if desired == VariableDisposition.INCLUDE.value:
                probe = run_integrity_checks(
                    self._df,
                    target=dest_target,
                    time_col=self.time_col,
                    all_targets=self.targets,
                    candidate_predictors=[col],
                    schema=self.schema,
                    config=self.config,
                )
                if any(c.variable == col for c in probe.blocking_conflicts):
                    copied[col] = "blocked_by_integrity"
                    continue
            self.override_variable(
                dest_target,
                col,
                desired,
                reason=f"{reason} ({source_target} -> {dest_target})",
                phase=phs,
                source=ProvenanceSource.CROSS_TARGET_COPY,
            )
            copied[col] = desired
        self._accepted_by_target[dest_target] = False
        return copied

    def accept(
        self,
        target: str | None = None,
        *,
        phase: str | ProvenancePhase = ProvenancePhase.PRE_TEST,
    ) -> str:
        """Validate non-disableable integrity checks and mark VSG gate accepted (PIPE-1)."""
        phs = normalize_phase(phase)
        check_targets = (target,) if target is not None else self.targets
        for t in check_targets:
            if t not in self.decisions_by_target:
                raise VSGValidationError(f"Unknown target {t!r}.")
            rep = self._refresh_integrity(t)
            if not rep.passed:
                msgs = "; ".join(c.message for c in rep.blocking_conflicts)
                raise IntegrityViolationError(
                    f"Cannot accept VSG gate for target '{t}' while blocking integrity conflicts exist: {msgs}"
                )
            self._accepted_by_target[t] = True
            self.provenance.record_stage(
                stage_name="vsg_accepted",
                target=t,
                payload={
                    "decisions": {c: d.to_dict() for c, d in sorted(self.decisions_by_target[t].items())},
                    "included": self.included_variables(t),
                    "integrity_sha256": rep.report_sha256,
                },
                upstream_stage_sha256=self.dataset_sha256,
                phase=phs,
            )
        return self.vsg_sha256

    def is_accepted(self, target: str | None = None) -> bool:
        if target is not None:
            if target not in self._accepted_by_target:
                return False
            return bool(self._accepted_by_target[target]) and self.integrity_by_target[target].passed
        return all(self._accepted_by_target[t] and self.integrity_by_target[t].passed for t in self.targets)

    def assert_accepted(self, target: str | None = None) -> None:
        if not self.is_accepted(target):
            scope = f"target '{target}'" if target else f"targets {list(self.targets)}"
            raise ComplianceGateError(
                f"PIPE-1 violation: Variable Selection Gate (VSG) is not accepted for {scope}. "
                "Inferential tests cannot execute before VSG acceptance."
            )

    @property
    def vsg_sha256(self) -> str:
        canonical = self._canonical_state_dict()
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _canonical_state_dict(self) -> dict[str, Any]:
        return {
            "accepted_by_target": {t: bool(self._accepted_by_target[t]) for t in sorted(self.targets)},
            "dataset_sha256": self.dataset_sha256,
            "decisions_by_target": {
                t: {c: self.decisions_by_target[t][c].to_dict() for c in sorted(self.decisions_by_target[t])}
                for t in sorted(self.targets)
            },
            "targets": list(self.targets),
            "time_col": self.time_col or "",
        }

    def to_dict(self) -> dict[str, Any]:
        out = self._canonical_state_dict()
        out["auto_log_hash"] = self.auto_log.compute_hash()
        out["excluded_profiles_by_target"] = {
            t: self.excluded_descriptive_profiles(t) for t in self.targets
        }
        out["integrity_by_target"] = {t: self.integrity_by_target[t].to_dict() for t in self.targets}
        out["profiles_by_target"] = {
            t: {c: p.to_dict() for c, p in self.profiles_by_target[t].items()} for t in self.targets
        }
        out["provenance"] = self.provenance.to_dict()
        out["vsg_sha256"] = self.vsg_sha256
        return out
