"""Non-disableable O(n) compliance integrity checks and conflict display (VSG-5, AC-08, MT-2).

Security & compliance invariants:
* All four core rules (``TARGET_AS_PREDICTOR``, ``TARGET_LEAKAGE_MT2``,
  ``TIME_ORDER_VIOLATION``, ``DST_DUPLICATE_TIMESTAMPS``) are permanently enabled and
  dispatched from an immutable ``MappingProxyType``.
* Any programmatic or configuration attempt to disable, skip, or weaken an integrity rule
  raises :class:`IntegrityBypassError` (on direct API mutation) or records a blocking
  ``INTEGRITY_BYPASS_BLOCKED`` conflict while still executing every check unconditionally.
* Every check runs in ``O(n)`` per column (``O(n * p)`` total across ``p`` candidate columns)
  with vectorized NumPy/pandas primitives and zero ``O(n^2)`` loops.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Final
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .schema import Availability, ColumnRole, DatasetSchema

RULE_TARGET_AS_PREDICTOR: Final[str] = "TARGET_AS_PREDICTOR"
RULE_TARGET_LEAKAGE_MT2: Final[str] = "TARGET_LEAKAGE_MT2"
RULE_TIME_ORDER_VIOLATION: Final[str] = "TIME_ORDER_VIOLATION"
RULE_DST_DUPLICATE_TIMESTAMPS: Final[str] = "DST_DUPLICATE_TIMESTAMPS"
RULE_BYPASS_BLOCKED: Final[str] = "INTEGRITY_BYPASS_BLOCKED"

NON_DISABLEABLE_RULES: Final[frozenset[str]] = frozenset(
    {
        RULE_TARGET_AS_PREDICTOR,
        RULE_TARGET_LEAKAGE_MT2,
        RULE_TIME_ORDER_VIOLATION,
        RULE_DST_DUPLICATE_TIMESTAMPS,
    }
)

_BYPASS_CONFIG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "disable_integrity",
        "skip_integrity",
        "bypass_integrity",
        "override_integrity",
        "disabled_rules",
        "disabled_integrity_rules",
        "ignore_leakage",
        "allow_leakage",
        "allow_target_as_predictor",
        "allow_unsorted_time",
        "allow_duplicate_timestamps",
    }
)

_ENERGY_METER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(kwh|mwh|gwh|gj|mj|therm|btu|mmbtu|energy|elec|steam|gas_m3|fuel|meter|consumption|demand_kw|bill|cost|savings|enpi|target)",
    re.IGNORECASE,
)


class IntegrityError(RuntimeError):
    """Base exception for compliance integrity violations."""


class IntegrityBypassError(IntegrityError):
    """Raised when caller attempts to disable or mutate a non-disableable integrity rule."""


class IntegrityViolationError(IntegrityError):
    """Raised when blocking integrity conflicts prevent gate acceptance or execution."""


@dataclass(frozen=True)
class IntegrityConflict:
    """Single structured conflict item suitable for audit logs and GUI conflict tables."""

    rule_id: str
    target: str
    variable: str
    severity: str  # "blocking" | "warning"
    message: str
    evidence: dict[str, Any]
    remediation: str
    non_disableable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": dict(self.evidence),
            "message": self.message,
            "non_disableable": bool(self.non_disableable),
            "remediation": self.remediation,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "target": self.target,
            "variable": self.variable,
        }


@dataclass(frozen=True)
class IntegrityReport:
    """Immutable result of running all non-disableable integrity checks."""

    target: str
    n_rows: int
    checked_rules: tuple[str, ...]
    conflicts: tuple[IntegrityConflict, ...]
    bypass_attempts_blocked: int = 0

    @property
    def blocking_conflicts(self) -> tuple[IntegrityConflict, ...]:
        return tuple(c for c in self.conflicts if c.severity == "blocking")

    @property
    def warning_conflicts(self) -> tuple[IntegrityConflict, ...]:
        return tuple(c for c in self.conflicts if c.severity != "blocking")

    @property
    def passed(self) -> bool:
        return len(self.blocking_conflicts) == 0

    @property
    def report_sha256(self) -> str:
        payload = json.dumps(self.to_dict(include_hash=False), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def conflicts_for_variable(self, variable: str) -> list[IntegrityConflict]:
        return [c for c in self.conflicts if c.variable == variable]

    def to_conflict_rows(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.conflicts]

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "blocking_count": len(self.blocking_conflicts),
            "bypass_attempts_blocked": self.bypass_attempts_blocked,
            "checked_rules": list(self.checked_rules),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "n_rows": self.n_rows,
            "passed": self.passed,
            "target": self.target,
            "warning_count": len(self.warning_conflicts),
        }
        if include_hash:
            out["report_sha256"] = self.report_sha256
        return out


def _extract_timestamps_ns(
    df: pd.DataFrame,
    time_col: str | None,
) -> tuple[pd.Series | pd.Index | None, np.ndarray | None, list[IntegrityConflict], str]:
    """Extract raw timestamp sequence and int64 nanosecond array in O(n) without sorting."""
    conflicts: list[IntegrityConflict] = []
    label = time_col or "(index)"
    if time_col and time_col in df.columns:
        raw = df[time_col]
    elif isinstance(df.index, pd.DatetimeIndex):
        raw = df.index.to_series(index=range(len(df)))
        label = df.index.name or "(datetime_index)"
    else:
        return None, None, conflicts, label

    parsed = pd.to_datetime(raw, errors="coerce", utc=True)
    na_mask = parsed.isna()
    n_bad = int(na_mask.sum())
    if n_bad > 0 and len(df) > 0:
        first_bad = int(np.flatnonzero(na_mask.to_numpy())[0])
        conflicts.append(
            IntegrityConflict(
                rule_id=RULE_TIME_ORDER_VIOLATION,
                target="",
                variable=label,
                severity="blocking",
                message=f"Timestamp column '{label}' contains {n_bad} unparseable/missing timestamp(s).",
                evidence={"first_invalid_row": first_bad, "invalid_count": n_bad},
                remediation="Repair or remove rows with unparseable timestamps before gate acceptance.",
            )
        )
        valid_parsed = parsed.dropna()
        if len(valid_parsed) == 0:
            return raw, None, conflicts, label
        return raw, valid_parsed.astype("int64").to_numpy(), conflicts, label
    return raw, parsed.astype("int64").to_numpy(), conflicts, label


def _check_time_order(
    df: pd.DataFrame,
    target: str,
    time_col: str | None,
    all_targets: Sequence[str],
    candidate_predictors: Sequence[str],
    schema: DatasetSchema | None,
) -> list[IntegrityConflict]:
    """O(n) check verifying strictly non-decreasing timestamp order without sorting."""
    _ = (all_targets, candidate_predictors, schema)
    _, ts_ns, parse_conflicts, label = _extract_timestamps_ns(df, time_col)
    conflicts = [
        IntegrityConflict(
            rule_id=c.rule_id,
            target=target,
            variable=c.variable,
            severity=c.severity,
            message=c.message,
            evidence=c.evidence,
            remediation=c.remediation,
        )
        for c in parse_conflicts
    ]
    if ts_ns is None or len(ts_ns) <= 1:
        return conflicts

    diffs = np.diff(ts_ns)
    backward_idx = np.flatnonzero(diffs < 0)
    if backward_idx.size > 0:
        first_i = int(backward_idx[0])
        conflicts.append(
            IntegrityConflict(
                rule_id=RULE_TIME_ORDER_VIOLATION,
                target=target,
                variable=label,
                severity="blocking",
                message=(
                    f"Time series '{label}' is not monotonically ordered: "
                    f"{int(backward_idx.size)} backward timestamp transition(s) detected."
                ),
                evidence={
                    "backward_transitions": int(backward_idx.size),
                    "first_out_of_order_position": first_i + 1,
                },
                remediation="Normalize and sort timestamps monotonically via temporal setup before testing.",
            )
        )
    return conflicts


def _check_dst_and_duplicates(
    df: pd.DataFrame,
    target: str,
    time_col: str | None,
    all_targets: Sequence[str],
    candidate_predictors: Sequence[str],
    schema: DatasetSchema | None,
) -> list[IntegrityConflict]:
    """O(n) duplicate timestamp and unresolved DST ambiguity/nonexistent check."""
    _ = (all_targets, candidate_predictors)
    raw_ts, ts_ns, _, label = _extract_timestamps_ns(df, time_col)
    conflicts: list[IntegrityConflict] = []
    if raw_ts is None or len(df) == 0:
        return conflicts

    # 1. Exact duplicate instants in O(n) via hash table
    if ts_ns is not None and len(ts_ns) > 1:
        dup_mask = pd.Index(ts_ns).duplicated(keep="first")
        dup_count = int(dup_mask.sum())
        if dup_count > 0:
            first_dup = int(np.flatnonzero(dup_mask)[0])
            conflicts.append(
                IntegrityConflict(
                    rule_id=RULE_DST_DUPLICATE_TIMESTAMPS,
                    target=target,
                    variable=label,
                    severity="blocking",
                    message=(
                        f"Timestamp sequence '{label}' has {dup_count} unresolved duplicate timestamp(s) "
                        "(possible un-disambiguated DST fall-back or duplicate ingest rows)."
                    ),
                    evidence={"duplicate_count": dup_count, "first_duplicate_row": first_dup},
                    remediation="Apply explicit DST/duplicate aggregation in Temporal Setup before gate acceptance.",
                )
            )

    # 2. If DatasetSchema declares an IANA timezone and raw timestamps are tz-naive strings/datetimes,
    # check for nonexistent spring-forward wall times in O(n).
    tz_name = (schema.timezone if schema is not None else "").strip()
    if tz_name and tz_name.upper() != "UTC":
        try:
            naive_parsed = pd.to_datetime(raw_ts, errors="coerce")
            if getattr(naive_parsed.dt, "tz", None) is None:
                tz = ZoneInfo(tz_name)
                try:
                    naive_parsed.dt.tz_localize(tz, ambiguous="infer", nonexistent="raise")
                except Exception as exc:
                    conflicts.append(
                        IntegrityConflict(
                            rule_id=RULE_DST_DUPLICATE_TIMESTAMPS,
                            target=target,
                            variable=label,
                            severity="blocking",
                            message=(
                                f"Timezone '{tz_name}' DST validation detected ambiguous or nonexistent local "
                                f"wall-clock timestamps in '{label}': {exc}"
                            ),
                            evidence={"timezone": tz_name, "detail": str(exc)},
                            remediation="Normalize timestamps to UTC via explicit DST handling policy first.",
                        )
                    )
        except Exception:
            pass

    return conflicts


def _check_target_never_predictor(
    df: pd.DataFrame,
    target: str,
    time_col: str | None,
    all_targets: Sequence[str],
    candidate_predictors: Sequence[str],
    schema: DatasetSchema | None,
) -> list[IntegrityConflict]:
    """O(p) check verifying target (and sibling targets per MT-2) is never a predictor."""
    _ = (df, time_col)
    conflicts: list[IntegrityConflict] = []
    forbidden_targets: set[str] = {target}
    for t in all_targets:
        if t:
            forbidden_targets.add(str(t))
    if schema is not None:
        for spec in schema.columns:
            if spec.role == ColumnRole.TARGET:
                forbidden_targets.add(spec.name)

    for col in candidate_predictors:
        if col == target:
            conflicts.append(
                IntegrityConflict(
                    rule_id=RULE_TARGET_AS_PREDICTOR,
                    target=target,
                    variable=col,
                    severity="blocking",
                    message=f"Target '{target}' cannot be included as a predictor of itself.",
                    evidence={"target": target, "variable": col, "reason": "self_target"},
                    remediation=f"Exclude '{col}' from predictor candidates for target '{target}'.",
                )
            )
        elif col in forbidden_targets:
            conflicts.append(
                IntegrityConflict(
                    rule_id=RULE_TARGET_AS_PREDICTOR,
                    target=target,
                    variable=col,
                    severity="blocking",
                    message=(
                        f"Sibling target '{col}' cannot be included as a predictor for target '{target}' "
                        "(MT-2 cross-target independence)."
                    ),
                    evidence={"target": target, "variable": col, "reason": "sibling_target_mt2"},
                    remediation=f"Exclude sibling EnPI target '{col}' from '{target}' predictors.",
                )
            )
    return conflicts


def fast_linear_leakage_metrics(x: np.ndarray, y: np.ndarray) -> tuple[float, float, int]:
    """Compute (abs_pearson_r, rel_affine_max_err, n_valid) in O(n) single vectorized pass."""
    mask = np.isfinite(x) & np.isfinite(y)
    n_valid = int(mask.sum())
    if n_valid < 4:
        return 0.0, float("inf"), n_valid
    xv = x[mask]
    yv = y[mask]
    dx = xv - float(np.mean(xv))
    dy = yv - float(np.mean(yv))
    ssx = float(np.dot(dx, dx))
    ssy = float(np.dot(dy, dy))
    if ssx <= 1e-24 or ssy <= 1e-24:
        return 0.0, float("inf"), n_valid
    cov_xy = float(np.dot(dx, dy))
    abs_r = min(1.0, abs(cov_xy) / float(np.sqrt(ssx * ssy)))
    slope = cov_xy / ssx
    intercept = float(np.mean(yv)) - slope * float(np.mean(xv))
    resid = np.abs((slope * xv + intercept) - yv)
    y_scale = float(np.sqrt(ssy / n_valid)) + 1e-12
    rel_max_err = float(np.max(resid)) / y_scale
    return abs_r, rel_max_err, n_valid


def _check_leakage_mt2(
    df: pd.DataFrame,
    target: str,
    time_col: str | None,
    all_targets: Sequence[str],
    candidate_predictors: Sequence[str],
    schema: DatasetSchema | None,
) -> list[IntegrityConflict]:
    """O(n * p) vectorized check catching target copies, affine transforms, and post-period leakage."""
    _ = all_targets
    conflicts: list[IntegrityConflict] = []
    if target not in df.columns:
        return conflicts

    schema_by_name = {spec.name: spec for spec in schema.columns} if schema is not None else {}
    y_num = pd.to_numeric(df[target], errors="coerce").to_numpy(dtype=float)
    for col in candidate_predictors:
        if col == target or col == time_col or col not in df.columns:
            continue
        spec = schema_by_name.get(col)
        if spec is not None and str(spec.availability).strip().lower() in (
            Availability.REPORTING_ONLY.value,
            "post_period_only",
        ):
            conflicts.append(
                IntegrityConflict(
                    rule_id=RULE_TARGET_LEAKAGE_MT2,
                    target=target,
                    variable=col,
                    severity="blocking",
                    message=(
                        f"Variable '{col}' has availability={str(spec.availability)!r} and cannot be used "
                        "for baseline fitting without temporal leakage."
                    ),
                    evidence={"availability": str(spec.availability)},
                    remediation=f"Exclude '{col}' or supply baseline-period observations.",
                )
            )
            continue

        s_col = df[col]
        if not (pd.api.types.is_numeric_dtype(s_col) or pd.api.types.is_bool_dtype(s_col)):
            continue
        x_num = pd.to_numeric(s_col, errors="coerce").to_numpy(dtype=float)
        abs_r, rel_max_err, n_valid = fast_linear_leakage_metrics(x_num, y_num)
        if n_valid < 5:
            continue

        is_exact_affine = abs_r >= 0.999995 or rel_max_err <= 1e-6
        is_meter_proxy = abs_r >= 0.99 and bool(_ENERGY_METER_PATTERN.search(col))
        if is_exact_affine or is_meter_proxy:
            kind = "exact_or_scaled_target_identity" if is_exact_affine else "energy_submeter_leakage"
            conflicts.append(
                IntegrityConflict(
                    rule_id=RULE_TARGET_LEAKAGE_MT2,
                    target=target,
                    variable=col,
                    severity="blocking",
                    message=(
                        f"Variable '{col}' exhibits target leakage against '{target}' "
                        f"(|r|={abs_r:.6f}, max_rel_affine_err={rel_max_err:.3e}, kind={kind})."
                    ),
                    evidence={
                        "abs_pearson_r": round(abs_r, 8),
                        "kind": kind,
                        "n_valid": n_valid,
                        "rel_affine_max_err": round(rel_max_err, 8),
                    },
                    remediation=f"Exclude leaked variable '{col}' from target '{target}' predictors.",
                )
            )
    return conflicts


_RULE_DISPATCH: MappingProxyType[
    str,
    Callable[
        [pd.DataFrame, str, str | None, Sequence[str], Sequence[str], DatasetSchema | None],
        list[IntegrityConflict],
    ],
] = MappingProxyType(
    {
        RULE_TARGET_AS_PREDICTOR: _check_target_never_predictor,
        RULE_TARGET_LEAKAGE_MT2: _check_leakage_mt2,
        RULE_TIME_ORDER_VIOLATION: _check_time_order,
        RULE_DST_DUPLICATE_TIMESTAMPS: _check_dst_and_duplicates,
    }
)


def _detect_bypass_attempts(config: Mapping[str, Any] | None, target: str) -> list[IntegrityConflict]:
    if not config:
        return []
    conflicts: list[IntegrityConflict] = []
    for key in sorted(_BYPASS_CONFIG_KEYS):
        if key not in config:
            continue
        val = config[key]
        is_truthy_bypass = bool(val) if not isinstance(val, str) else val.strip().lower() not in ("", "false", "0", "no")
        if is_truthy_bypass:
            conflicts.append(
                IntegrityConflict(
                    rule_id=RULE_BYPASS_BLOCKED,
                    target=target,
                    variable="(integrity_engine)",
                    severity="blocking",
                    message=(
                        f"Attempted bypass of non-disableable compliance integrity checks via "
                        f"config[{key!r}]={val!r} was blocked."
                    ),
                    evidence={"config_key": key, "requested_value": str(val)},
                    remediation=f"Remove '{key}' from configuration; compliance integrity rules cannot be disabled.",
                )
            )
    return conflicts


class IntegrityEngine:
    """Immutable compliance integrity checker whose rules cannot be disabled or replaced."""

    __slots__ = ()

    @property
    def active_rules(self) -> tuple[str, ...]:
        return tuple(_RULE_DISPATCH.keys())

    @property
    def rule_registry(self) -> MappingProxyType[str, Callable[..., list[IntegrityConflict]]]:
        return _RULE_DISPATCH

    def disable_rule(self, rule_id: str) -> None:
        raise IntegrityBypassError(
            f"Rule {rule_id!r} is non-disableable (NON_DISABLEABLE_RULES={sorted(NON_DISABLEABLE_RULES)})."
        )

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        if not enabled or rule_id not in NON_DISABLEABLE_RULES:
            raise IntegrityBypassError(
                f"Cannot set enabled={enabled} on compliance integrity rule {rule_id!r}; "
                "all integrity rules are permanently mandatory."
            )

    def run(
        self,
        df: pd.DataFrame,
        *,
        target: str,
        time_col: str | None = None,
        all_targets: Sequence[str] = (),
        candidate_predictors: Sequence[str] | None = None,
        schema: DatasetSchema | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> IntegrityReport:
        if candidate_predictors is None:
            excluded_meta = {target}
            if time_col:
                excluded_meta.add(time_col)
            preds = [str(c) for c in df.columns if str(c) not in excluded_meta]
        else:
            preds = [str(c) for c in candidate_predictors]

        conflicts: list[IntegrityConflict] = []
        bypass_conflicts = _detect_bypass_attempts(config, target)
        conflicts.extend(bypass_conflicts)

        # Unconditionally execute every non-disableable rule in deterministic order.
        ordered_rules = (
            RULE_TARGET_AS_PREDICTOR,
            RULE_TARGET_LEAKAGE_MT2,
            RULE_TIME_ORDER_VIOLATION,
            RULE_DST_DUPLICATE_TIMESTAMPS,
        )
        for rule_id in ordered_rules:
            fn = _RULE_DISPATCH[rule_id]
            conflicts.extend(fn(df, target, time_col, all_targets, preds, schema))

        return IntegrityReport(
            target=target,
            n_rows=len(df),
            checked_rules=ordered_rules,
            conflicts=tuple(conflicts),
            bypass_attempts_blocked=len(bypass_conflicts),
        )


DEFAULT_INTEGRITY_ENGINE: Final[IntegrityEngine] = IntegrityEngine()


def run_integrity_checks(
    df: pd.DataFrame,
    *,
    target: str,
    time_col: str | None = None,
    all_targets: Sequence[str] = (),
    candidate_predictors: Sequence[str] | None = None,
    schema: DatasetSchema | None = None,
    config: Mapping[str, Any] | None = None,
) -> IntegrityReport:
    """Execute all non-disableable integrity checks for ``target`` in ``O(n * p)``."""
    return DEFAULT_INTEGRITY_ENGINE.run(
        df,
        target=target,
        time_col=time_col,
        all_targets=all_targets,
        candidate_predictors=candidate_predictors,
        schema=schema,
        config=config,
    )
