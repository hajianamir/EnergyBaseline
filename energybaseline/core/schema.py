"""Declarative settings catalogue and Phase 2 dataset column metadata schema (DAT-1..8)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import pandas as pd

AUTO = "AUTO"


# ---------------------------------------------------------------------------
# User-selectable settings catalogue (preserved + Phase 2 policy entries)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SettingDef:
    key: str
    category: str
    label: str
    kind: str  # choice | int | float | bool | text | list
    recommended: str
    explain: str
    choices: tuple[str, ...] = field(default_factory=tuple)


S = SettingDef
SETTINGS: list[SettingDef] = [
    # ---- Data & Temporal
    S(
        "sampling_period",
        "Data & Temporal",
        "Sampling period",
        "choice",
        "AUTO (detected)",
        "Declared temporal granularity. AUTO detects it from the timestamps (you confirm in the Setup tab).",
        ("15min", "30min", "hourly", "daily", "weekly", "monthly", "quarterly", "annual", "custom:<seconds>", "mixed"),
    ),
    S(
        "aggregation",
        "Data & Temporal",
        "Aggregation override (finer data -> declared period)",
        "choice",
        "driven by ColumnSpec.quantity_type (flow->sum, stock->last, rate->time_weighted, quality->mean/median, index->mean)",
        "Optional global override; AUTO dispatches strictly by modelled column quantity_type.",
        ("mean", "median", "sum", "max", "min", "first", "last", "time_weighted"),
    ),
    S(
        "quality_aggregation",
        "Data & Temporal",
        "Default aggregation for 'quality' columns",
        "choice",
        "mean",
        "Used for QuantityType.QUALITY columns unless overridden per column.",
        ("mean", "median"),
    ),
    S(
        "timezone",
        "Data & Temporal",
        "Timezone",
        "text",
        "naive wall-clock (no conversion)",
        "IANA timezone of the timestamps, e.g. Europe/Berlin. Declared timezones store UTC internally and display local time.",
    ),
    S(
        "dst_handling",
        "Data & Temporal",
        "DST handling",
        "choice",
        "utc",
        "Only used when a timezone is declared (utc stores UTC cleanly across 23h/25h DST days).",
        ("wall_clock", "utc", "drop_ambiguous"),
    ),
    S(
        "baseline_window",
        "Data & Temporal",
        "Baseline window (start,end)",
        "text",
        "entire available range",
        "ISO dates 'YYYY-MM-DD,YYYY-MM-DD'. AUTO uses the full span after cleaning.",
    ),
    S(
        "reporting_window",
        "Data & Temporal",
        "Reporting window (start,end)",
        "text",
        "none",
        "Optional. Rows in this window are excluded from model fitting and reserved for monitoring.",
    ),
    # ---- Preprocessing
    S(
        "missing_strategy",
        "Preprocessing",
        "Missing-value strategy (predictors)",
        "choice",
        "linear (<5 %), knn (5-20 %), median (>20 %)",
        "Targets are never imputed; short predictor gaps use bounded interpolation and long shutdowns are never fabricated.",
        ("linear", "ffill", "median", "knn", "mice", "flag", "drop"),
    ),
    S(
        "max_missing_rate",
        "Preprocessing",
        "Max column missing rate",
        "float",
        "0.40",
        "Columns above this rate are excluded.",
    ),
    S(
        "outlier_method",
        "Preprocessing",
        "Outlier detection",
        "choice",
        "depends on skew, n, dimension",
        "Method used to flag outliers.",
        ("none", "robust_z", "iqr", "mad", "isolation_forest", "lof", "ocsvm", "mahalanobis", "hbos", "autoencoder"),
    ),
    S(
        "outlier_treatment",
        "Preprocessing",
        "Outlier treatment",
        "choice",
        "depends on flagged share",
        "flag = keep; winsorize; interpolate (predictors); remove (rows leave training).",
        ("flag", "winsorize", "interpolate", "remove"),
    ),
    S(
        "outlier_action_policy",
        "Preprocessing",
        "Outlier action policy",
        "choice",
        "flag_only | ask_user | auto_apply",
        "Controls whether outlier treatments are only flagged, require user approval, or apply automatically with logged training-set delta.",
        ("flag_only", "ask_user", "auto_apply"),
    ),
    S(
        "outlier_threshold",
        "Preprocessing",
        "Outlier threshold (robust z / IQR multiple)",
        "float",
        "3.5",
        "Higher = fewer flags.",
    ),
    S(
        "corr_threshold",
        "Preprocessing",
        "Collinearity |r| threshold",
        "float",
        "0.95",
        "One of each pair above this is excluded.",
    ),
    S(
        "vif_threshold",
        "Preprocessing",
        "VIF threshold",
        "float",
        "10 (5 for small n)",
        "Iterative VIF pruning.",
    ),
    S(
        "min_variance",
        "Preprocessing",
        "Min relative variance",
        "float",
        "1e-8",
        "Near-constant columns are excluded.",
    ),
    S(
        "leakage_threshold",
        "Preprocessing",
        "Leakage |r| threshold",
        "float",
        "0.99",
        "Energy-like columns with |r| above this with the target are excluded as leakage.",
    ),
    # ---- Feature engineering
    S("calendar_features", "Feature Engineering", "Calendar features", "bool", "on for sub-daily/daily", "Weekend flag, hour/day harmonics."),
    S("fourier_terms", "Feature Engineering", "Fourier harmonics", "int", "from autocorrelation", "Harmonics per detected period."),
    S("degree_days", "Feature Engineering", "Degree-day features (HDD/CDD)", "bool", "on if temperature column found", "Balance points fitted from data."),
    S("lags", "Feature Engineering", "Predictor lags (steps)", "list", "from lag-correlation gain", "Comma-separated, e.g. 1,24."),
    S("rolling_windows", "Feature Engineering", "Rolling-mean windows (steps)", "list", "from correlation gain", "Comma-separated."),
    S("interactions", "Feature Engineering", "Pairwise interactions", "bool", "on only if n > 20*p and p <= 6", "Only regularised/tree models see them."),
    S("holiday_country", "Feature Engineering", "Holiday calendar country code", "text", "none", "ISO code such as DE, US (needs 'holidays' package)."),
    # ---- Feature selection
    S(
        "selection_method",
        "Feature Selection",
        "Selection method",
        "choice",
        "none (p<=8) / lasso / mutual_info",
        "How candidate features are pruned.",
        ("none", "correlation", "mutual_info", "lasso"),
    ),
    S("max_features", "Feature Selection", "Max features", "int", "min(p, n/20)", "Upper bound on retained features."),
    # ---- Modeling
    S("enable_nn", "Modeling", "Neural networks", "bool", "on if n >= 300 and torch present", "Deep models need enough data."),
    S("model_families", "Modeling", "Model families", "list", "all applicable", "linear,tree,kernel,nn (comma-separated)."),
    S(
        "cv_scheme",
        "Modeling",
        "Validation scheme",
        "choice",
        "blocked_kfold (<2 y) / forward_chaining",
        "Time-series-aware splits with purge gap.",
        ("forward_chaining", "blocked_kfold"),
    ),
    S("cv_folds", "Modeling", "Validation folds", "int", "3-6 by n", "Number of outer folds."),
    S("hpo_trials", "Modeling", "HPO trials per tuned model", "int", "3-8 by n", "Optuna (TPE) trials; nested inside each fold."),
    S("nn_max_epochs", "Modeling", "NN max epochs", "int", "40-150 by n", "Early stopping is always on."),
    S("nn_seq_len", "Modeling", "NN sequence length (steps)", "int", "from granularity", "Window for LSTM/GRU/TCN/CNN."),
    S("parallel_targets", "Modeling", "Parallel targets", "int", "1 (sequential) or min(2, cores/2)", "Targets processed at once."),
    S("enable_mining", "Modeling", "Data-mining layer", "bool", "on", "Clustering, anomaly, association, TS mining, regimes."),
    # ---- Selection scoring
    S(
        "weight_profile",
        "Selection Scoring",
        "Scoring weight profile",
        "choice",
        "balanced (interpretable if n<200)",
        "Multi-criteria weights.",
        ("balanced", "accuracy_first", "interpretable"),
    ),
    S("min_r2", "Selection Scoring", "Minimum R2", "float", "0.75 monthly+, 0.5 otherwise", "Acceptance threshold."),
    S("max_cvrmse", "Selection Scoring", "Max CV(RMSE) %", "float", "ASHRAE 14 by granularity", "Acceptance threshold."),
    S("max_nmbe", "Selection Scoring", "Max |NMBE| %", "float", "ASHRAE 14 by granularity", "Acceptance threshold."),
    S("nn_min_gain", "Selection Scoring", "NN minimum relative gain %", "float", "5", "NN rejected if CV(RMSE) gain over best classical model is below this."),
    # ---- Reporting
    S("report_formats", "Reporting", "Report formats", "list", "pdf,xlsx", "pdf, xlsx, html."),
    S("include_importance", "Reporting", "Permutation importance / SHAP / PDP", "bool", "on (n <= 20000 for SHAP)", "Interpretability extras."),
    # ---- Reproducibility
    S("seed", "Reproducibility", "Random seed", "int", "hash of dataset + targets", "AUTO is deterministic per dataset."),
    # ---- Prediction defaults
    S(
        "alert_mode",
        "Prediction Module Defaults",
        "Alert threshold mode",
        "choice",
        "max(10 %, 2 x CV(RMSE))",
        "How exceedances are flagged.",
        ("percent", "cvrmse_multiple", "interval"),
    ),
    S("alert_value", "Prediction Module Defaults", "Alert threshold value", "float", "10 (% or multiple)", "Value used by the mode."),
    S(
        "default_source",
        "Prediction Module Defaults",
        "Default data source",
        "choice",
        "file",
        "Preselected input mode.",
        ("file", "sql", "manual"),
    ),
]
SETTINGS_BY_KEY = {s.key: s for s in SETTINGS}
CATEGORIES = list(dict.fromkeys(s.category for s in SETTINGS))


def default_config() -> dict[str, str]:
    return {s.key: AUTO for s in SETTINGS}


# ---------------------------------------------------------------------------
# Phase 2 Column Metadata Model (DAT-1, DAT-2, DAT-4, DAT-7, DAT-8)
# ---------------------------------------------------------------------------
SCHEMA_FORMAT_VERSION = "2.0"


class SchemaValidationError(ValueError):
    """Raised when a ``ColumnSpec`` or ``DatasetSchema`` violates domain rules."""


class SchemaIntegrityError(SchemaValidationError):
    """Raised when a serialized ``DatasetSchema`` fails SHA-256 verification."""


class ColumnRole(str, Enum):
    """Declared analytical role of a dataset column (DAT-1)."""

    TIME = "time"
    TARGET = "target"
    PREDICTOR = "predictor"
    STATIC_FACTOR = "static_factor"
    GROUP = "group"
    WEIGHT = "weight"
    IGNORE = "ignore"


class QuantityType(str, Enum):
    """Physical / statistical quantity type governing temporal aggregation (DAT-1, DAT-3)."""

    FLOW = "flow"
    STOCK = "stock"
    RATE = "rate"
    QUALITY = "quality"
    INDEX = "index"


class Inclusion(str, Enum):
    """Information-timing classification for forecasting / M&V evaluation (DAT-1, DAT-2)."""

    KNOWN_IN_ADVANCE = "known_in_advance"
    OBSERVED_ONLY = "observed_only"


class Availability(str, Enum):
    """Standard operational availability classifications for a column (DAT-2)."""

    ALWAYS = "always"
    REAL_TIME = "real_time"
    SCHEDULED = "scheduled"
    DELAYED = "delayed"
    BASELINE_ONLY = "baseline_only"
    REPORTING_ONLY = "reporting_only"
    INTERMITTENT = "intermittent"
    UNKNOWN = "unknown"


class OutlierActionPolicy(str, Enum):
    """Policy governing whether detected outliers mutate the training frame (Gate G2)."""

    FLAG_ONLY = "flag_only"
    ASK_USER = "ask_user"
    AUTO_APPLY = "auto_apply"


_QUANTITATIVE_ROLES = frozenset(
    {
        ColumnRole.TARGET,
        ColumnRole.PREDICTOR,
        ColumnRole.STATIC_FACTOR,
        ColumnRole.WEIGHT,
    }
)
_VALID_QUALITY_AGGS = frozenset({"mean", "median"})
_VALID_NRE_ACTIONS = frozenset({"exclude", "flag", "adjust_indicator"})


def _coerce_enum(cls: type[Enum], value: Any, field_name: str) -> Any:
    if isinstance(value, cls):
        return value
    if isinstance(value, str):
        norm = value.strip().lower()
        try:
            return cls(norm)
        except ValueError as exc:
            allowed = ", ".join(e.value for e in cls)
            raise SchemaValidationError(f"Invalid {field_name} '{value}'. Expected one of: {allowed}.") from exc
    allowed = ", ".join(e.value for e in cls)
    raise SchemaValidationError(f"Invalid {field_name} {value!r}. Expected one of: {allowed}.")


@dataclass
class ColumnSpec:
    """Typed column metadata specification persisted in project files and SQLite (DAT-1, DAT-2)."""

    name: str
    role: ColumnRole = ColumnRole.PREDICTOR
    quantity_type: QuantityType | None = None
    inclusion: Inclusion = Inclusion.OBSERVED_ONLY
    unit: str = ""
    availability: str = Availability.ALWAYS.value
    quality_agg: str = "mean"
    suggested_by_heuristic: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> ColumnSpec:
        if not isinstance(self.name, str) or not self.name.strip():
            raise SchemaValidationError("ColumnSpec.name must be a non-empty string.")
        self.name = self.name.strip()
        self.role = _coerce_enum(ColumnRole, self.role, "role")
        self.inclusion = _coerce_enum(Inclusion, self.inclusion, "inclusion")

        if self.quantity_type is None or (isinstance(self.quantity_type, str) and not self.quantity_type.strip()):
            if self.role in _QUANTITATIVE_ROLES:
                self.quantity_type = QuantityType.QUALITY
            else:
                self.quantity_type = None
        else:
            self.quantity_type = _coerce_enum(QuantityType, self.quantity_type, "quantity_type")

        if not isinstance(self.unit, str):
            raise SchemaValidationError(f"ColumnSpec.unit for '{self.name}' must be a string.")
        self.unit = self.unit.strip()

        if isinstance(self.availability, Availability):
            self.availability = self.availability.value
        elif isinstance(self.availability, str):
            self.availability = self.availability.strip() or Availability.ALWAYS.value
        else:
            raise SchemaValidationError(f"ColumnSpec.availability for '{self.name}' must be a string.")
        if any(ord(ch) < 32 for ch in self.availability):
            raise SchemaValidationError(f"ColumnSpec.availability for '{self.name}' contains control characters.")

        if not isinstance(self.quality_agg, str) or self.quality_agg.strip().lower() not in _VALID_QUALITY_AGGS:
            raise SchemaValidationError(
                f"ColumnSpec.quality_agg for '{self.name}' must be 'mean' or 'median'; got {self.quality_agg!r}."
            )
        self.quality_agg = self.quality_agg.strip().lower()
        self.suggested_by_heuristic = bool(self.suggested_by_heuristic)
        self.notes = str(self.notes or "").strip()
        if self.role in _QUANTITATIVE_ROLES and self.quantity_type is None:
            raise SchemaValidationError(
                f"Column '{self.name}' with role '{self.role.value}' requires a QuantityType."
            )
        return self

    def with_updates(self, **changes: Any) -> ColumnSpec:
        data = self.to_dict()
        if any(k in changes for k in ("role", "quantity_type", "inclusion", "unit", "availability", "quality_agg")):
            data["suggested_by_heuristic"] = changes.get("suggested_by_heuristic", False)
        data.update(changes)
        return ColumnSpec.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "name": self.name,
            "role": self.role.value,
            "quantity_type": self.quantity_type.value if self.quantity_type is not None else None,
            "inclusion": self.inclusion.value,
            "unit": self.unit,
            "availability": self.availability,
            "quality_agg": self.quality_agg,
            "suggested_by_heuristic": self.suggested_by_heuristic,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ColumnSpec:
        if not isinstance(data, Mapping):
            raise SchemaValidationError("ColumnSpec payload must be a mapping.")
        return cls(
            name=str(data.get("name", "")),
            role=data.get("role", ColumnRole.PREDICTOR),  # type: ignore[arg-type]
            quantity_type=data.get("quantity_type"),
            inclusion=data.get("inclusion", Inclusion.OBSERVED_ONLY),  # type: ignore[arg-type]
            unit=str(data.get("unit", "") or ""),
            availability=str(data.get("availability", Availability.ALWAYS.value) or Availability.ALWAYS.value),
            quality_agg=str(data.get("quality_agg", "mean") or "mean"),
            suggested_by_heuristic=bool(data.get("suggested_by_heuristic", False)),
            notes=str(data.get("notes", "") or ""),
        )


@dataclass
class NonRoutineEventSpec:
    """Declared non-routine event window (shutdown, retrofit, occupancy shift, metering fault)."""

    name: str
    start: str
    end: str
    action: str = "exclude"
    affected_targets: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> NonRoutineEventSpec:
        if not isinstance(self.name, str) or not self.name.strip():
            raise SchemaValidationError("NonRoutineEventSpec.name must be a non-empty string.")
        self.name = self.name.strip()
        try:
            t0 = pd.Timestamp(self.start)
            t1 = pd.Timestamp(self.end)
        except Exception as exc:
            raise SchemaValidationError(
                f"NonRoutineEventSpec '{self.name}' has unparseable timestamps ({self.start!r} .. {self.end!r})."
            ) from exc
        if pd.isna(t0) or pd.isna(t1):
            raise SchemaValidationError(f"NonRoutineEventSpec '{self.name}' requires non-null start and end timestamps.")
        if t1 < t0:
            raise SchemaValidationError(
                f"NonRoutineEventSpec '{self.name}' has end ({self.end}) earlier than start ({self.start})."
            )
        self.start = str(self.start).strip()
        self.end = str(self.end).strip()
        act = str(self.action or "").strip().lower()
        if act == "indicator":
            act = "adjust_indicator"
        elif act == "flag_only":
            act = "flag"
        if act not in _VALID_NRE_ACTIONS:
            raise SchemaValidationError(
                f"NonRoutineEventSpec.action must be one of {sorted(_VALID_NRE_ACTIONS)}; got {self.action!r}."
            )
        self.action = act
        if isinstance(self.affected_targets, str):
            raw_targets = [x.strip() for x in self.affected_targets.split(",") if x.strip()]
        else:
            raw_targets = [str(x).strip() for x in (self.affected_targets or ()) if str(x).strip()]
        self.affected_targets = tuple(raw_targets)
        self.reason = str(self.reason or "").strip()
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "action": self.action,
            "affected_targets": list(self.affected_targets),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NonRoutineEventSpec:
        if not isinstance(data, Mapping):
            raise SchemaValidationError("NonRoutineEventSpec payload must be a mapping.")
        return cls(
            name=str(data.get("name", "")),
            start=str(data.get("start", "")),
            end=str(data.get("end", "")),
            action=str(data.get("action", "exclude") or "exclude"),
            affected_targets=tuple(data.get("affected_targets") or ()),
            reason=str(data.get("reason", "") or ""),
        )


@dataclass
class DatasetSchema:
    """Complete modelled column schema for a dataset with deterministic SHA-256 integrity (DAT-1..8)."""

    columns: list[ColumnSpec]
    dataset_name: str = ""
    dataset_sha256: str = ""
    timezone: str = ""
    storage_timezone: str = "UTC"
    outlier_policy: OutlierActionPolicy = OutlierActionPolicy.FLAG_ONLY
    non_routine_events: list[NonRoutineEventSpec] = field(default_factory=list)
    schema_version: str = SCHEMA_FORMAT_VERSION

    def __post_init__(self) -> None:
        self.columns = [
            c if isinstance(c, ColumnSpec) else ColumnSpec.from_dict(c)  # type: ignore[arg-type]
            for c in list(self.columns or [])
        ]
        self.non_routine_events = [
            e if isinstance(e, NonRoutineEventSpec) else NonRoutineEventSpec.from_dict(e)  # type: ignore[arg-type]
            for e in list(self.non_routine_events or [])
        ]
        self.outlier_policy = _coerce_enum(OutlierActionPolicy, self.outlier_policy, "outlier_policy")
        self.dataset_name = str(self.dataset_name or "").strip()
        self.dataset_sha256 = str(self.dataset_sha256 or "").strip().lower()
        self.timezone = str(self.timezone or "").strip()
        self.storage_timezone = str(self.storage_timezone or "UTC").strip() or "UTC"
        self.schema_version = str(self.schema_version or SCHEMA_FORMAT_VERSION).strip()
        self.validate()

    def validate(self, df_columns: Sequence[str] | None = None) -> DatasetSchema:
        """Validate schema structural invariants, including DAT-7 (1 time column, >=1 target)."""
        if not self.columns:
            raise SchemaValidationError("DatasetSchema must define at least two columns (one 'time' and one 'target').")

        seen: set[str] = set()
        for spec in self.columns:
            spec.validate()
            if spec.name in seen:
                raise SchemaValidationError(f"Duplicate column name '{spec.name}' in DatasetSchema.")
            seen.add(spec.name)
            if spec.role in _QUANTITATIVE_ROLES and spec.quantity_type is None:
                raise SchemaValidationError(
                    f"Column '{spec.name}' with role '{spec.role.value}' requires a QuantityType."
                )

        self.outlier_policy = _coerce_enum(OutlierActionPolicy, self.outlier_policy, "outlier_policy")
        for ev in self.non_routine_events:
            ev.validate()

        time_cols = [c.name for c in self.columns if c.role == ColumnRole.TIME]
        if len(time_cols) != 1:
            raise SchemaValidationError(
                f"DatasetSchema requires exactly one 'time' column; found {len(time_cols)}"
                + (f" ({time_cols})." if time_cols else ".")
            )

        target_cols = [c.name for c in self.columns if c.role == ColumnRole.TARGET]
        if len(target_cols) < 1:
            raise SchemaValidationError("DatasetSchema requires at least one 'target' column; found 0.")

        tz_clean = str(self.timezone or "").strip()
        if tz_clean and tz_clean.lower() not in ("naive", "auto"):
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(tz_clean)
            except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
                raise SchemaValidationError(f"Unknown IANA timezone '{tz_clean}': {exc}") from exc

        if df_columns is not None:
            present = {str(c).strip() for c in df_columns}
            missing = [
                c.name
                for c in self.columns
                if c.role != ColumnRole.IGNORE and c.name not in present
            ]
            if missing:
                raise SchemaValidationError(
                    f"DatasetSchema references columns absent from DataFrame: {missing}."
                )
        return self

    @property
    def time_column(self) -> str:
        self.validate()
        return next(c.name for c in self.columns if c.role == ColumnRole.TIME)

    @property
    def target_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role == ColumnRole.TARGET]

    @property
    def predictor_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role == ColumnRole.PREDICTOR]

    @property
    def static_factor_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role == ColumnRole.STATIC_FACTOR]

    @property
    def group_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role == ColumnRole.GROUP]

    @property
    def weight_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role == ColumnRole.WEIGHT]

    @property
    def weight_column(self) -> str | None:
        cols = self.weight_columns
        return cols[0] if cols else None

    @property
    def ignored_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role == ColumnRole.IGNORE]

    @property
    def known_in_advance_columns(self) -> list[str]:
        return [
            c.name
            for c in self.columns
            if c.inclusion == Inclusion.KNOWN_IN_ADVANCE and c.role != ColumnRole.TIME
        ]

    def get(self, col_name: str) -> ColumnSpec | None:
        key = str(col_name).strip()
        for c in self.columns:
            if c.name == key:
                return c
        return None

    def get_column(self, col_name: str) -> ColumnSpec:
        spec = self.get(col_name)
        if spec is None:
            raise KeyError(f"Column '{col_name}' not found in DatasetSchema.")
        return spec

    def with_column(self, spec: ColumnSpec) -> DatasetSchema:
        replaced = False
        new_cols: list[ColumnSpec] = []
        for c in self.columns:
            if c.name == spec.name:
                new_cols.append(spec)
                replaced = True
            else:
                new_cols.append(c)
        if not replaced:
            new_cols.append(spec)
        return DatasetSchema(
            columns=new_cols,
            dataset_name=self.dataset_name,
            dataset_sha256=self.dataset_sha256,
            timezone=self.timezone,
            storage_timezone=self.storage_timezone,
            outlier_policy=self.outlier_policy,
            non_routine_events=list(self.non_routine_events),
            schema_version=self.schema_version,
        )

    def units_map(self) -> dict[str, str]:
        return {c.name: c.unit for c in self.columns if c.unit}

    def update_column(self, col_name: str, **changes: Any) -> DatasetSchema:
        """Return a new validated ``DatasetSchema`` with ``col_name`` updated explicitly."""
        key = str(col_name).strip()
        found = False
        new_cols: list[ColumnSpec] = []
        for c in self.columns:
            if c.name == key:
                found = True
                new_cols.append(c.with_updates(**changes))
            else:
                new_cols.append(ColumnSpec.from_dict(c.to_dict()))
        if not found:
            raise SchemaValidationError(f"Column '{key}' not found in DatasetSchema.")
        return DatasetSchema(
            columns=new_cols,
            dataset_name=self.dataset_name,
            dataset_sha256=self.dataset_sha256,
            timezone=self.timezone,
            storage_timezone=self.storage_timezone,
            outlier_policy=self.outlier_policy,
            non_routine_events=[NonRoutineEventSpec.from_dict(e.to_dict()) for e in self.non_routine_events],
            schema_version=self.schema_version,
        )

    def canonical_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "dataset_name": self.dataset_name,
            "dataset_sha256": self.dataset_sha256,
            "timezone": self.timezone,
            "storage_timezone": self.storage_timezone,
            "outlier_policy": self.outlier_policy.value,
            "columns": [c.to_dict() for c in self.columns],
            "non_routine_events": [e.to_dict() for e in self.non_routine_events],
        }

    def compute_sha256(self) -> str:
        """Compute deterministic SHA-256 over canonical UTF-8 JSON representation (DAT-8)."""
        canonical_bytes = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_payload()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DatasetSchema:
        if not isinstance(data, Mapping):
            raise SchemaValidationError("DatasetSchema payload must be a mapping.")
        if "schema" in data and isinstance(data["schema"], Mapping) and "columns" not in data:
            return cls.from_envelope(data, verify_sha256=bool(data.get("sha256")))
        return cls(
            columns=[ColumnSpec.from_dict(c) for c in list(data.get("columns") or [])],
            dataset_name=str(data.get("dataset_name", "") or ""),
            dataset_sha256=str(data.get("dataset_sha256", "") or ""),
            timezone=str(data.get("timezone", "") or ""),
            storage_timezone=str(data.get("storage_timezone", "UTC") or "UTC"),
            outlier_policy=data.get("outlier_policy", OutlierActionPolicy.FLAG_ONLY),  # type: ignore[arg-type]
            non_routine_events=[NonRoutineEventSpec.from_dict(e) for e in list(data.get("non_routine_events") or [])],
            schema_version=str(data.get("schema_version", SCHEMA_FORMAT_VERSION) or SCHEMA_FORMAT_VERSION),
        )

    def to_envelope(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        return {
            "format": "energybaseline.dataset_schema",
            "schema_version": self.schema_version,
            "sha256": self.compute_sha256(),
            "schema": payload,
        }

    @classmethod
    def from_envelope(cls, envelope: Mapping[str, Any], *, verify_sha256: bool = True) -> DatasetSchema:
        if not isinstance(envelope, Mapping):
            raise SchemaValidationError("DatasetSchema envelope must be a mapping.")
        raw_schema = envelope.get("schema")
        if not isinstance(raw_schema, Mapping):
            raise SchemaValidationError("DatasetSchema envelope is missing 'schema' mapping.")
        expected_sha = str(envelope.get("sha256", "") or "").strip().lower()
        if verify_sha256 and not expected_sha:
            raise SchemaIntegrityError("DatasetSchema envelope is missing mandatory 'sha256' digest.")
        # Verify raw canonical payload digest BEFORE trusting contents so extra/altered keys are caught too.
        if verify_sha256:
            raw_canonical = json.dumps(
                raw_schema,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            actual_raw_sha = hashlib.sha256(raw_canonical).hexdigest()
            if actual_raw_sha != expected_sha:
                raise SchemaIntegrityError(
                    f"DatasetSchema SHA-256 mismatch: expected {expected_sha}, computed {actual_raw_sha}."
                )
        instance = cls.from_dict(raw_schema)
        if verify_sha256:
            recomputed = instance.compute_sha256()
            if recomputed != expected_sha:
                raise SchemaIntegrityError(
                    f"DatasetSchema SHA-256 mismatch after normalization: expected {expected_sha}, got {recomputed}."
                )
        return instance

    def to_json_bytes(self, *, indent: int | None = 2) -> bytes:
        return (json.dumps(self.to_envelope(), indent=indent, sort_keys=True) + "\n").encode("utf-8")

    @classmethod
    def from_json_bytes(cls, raw: bytes | str, *, verify_sha256: bool = True) -> DatasetSchema:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SchemaValidationError(f"Invalid DatasetSchema JSON: {exc}") from exc
        if isinstance(parsed, Mapping) and "schema" in parsed:
            return cls.from_envelope(parsed, verify_sha256=verify_sha256)
        if verify_sha256:
            raise SchemaIntegrityError("DatasetSchema JSON lacks integrity envelope ('sha256' + 'schema').")
        return cls.from_dict(parsed)

    def save_json(self, path: str | Path) -> Path:
        """Atomically serialize ``DatasetSchema`` + SHA-256 envelope to ``path`` (DAT-2, DAT-8)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_json_bytes(indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix=".eb_schema_", suffix=".tmp", dir=str(target.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target

    @classmethod
    def load_json(cls, path: str | Path, *, verify_sha256: bool = True) -> DatasetSchema:
        """Load a serialized ``DatasetSchema`` from ``path`` and verify SHA-256 (DAT-2, DAT-8)."""
        return cls.from_json_bytes(Path(path).read_bytes(), verify_sha256=verify_sha256)


# ---------------------------------------------------------------------------
# Heuristic Column Suggestion ONLY — never authoritative over explicit metadata (DAT-4)
# ---------------------------------------------------------------------------
_HEURISTIC_TIME_RE = re.compile(r"\b(time|timestamp|date|datetime|stamp|period)\b", re.I)
_HEURISTIC_FLOW_RE = re.compile(
    r"(kwh|mwh|\bwh\b|gj|mj|kbtu|mmbtu|btu|therm|m3|volume|liter|gal|consum|energy|usage|steam|chilled|elec|gas|water|production|output|tonnes|units)",
    re.I,
)
_HEURISTIC_RATE_RE = re.compile(
    r"(\bkw\b|\bmw\b|\bw\b|power|demand|flow[_\s-]*rate|m3[/_]h|kg[/_]h|\brate\b|cfm|gpm|l[/_]s)",
    re.I,
)
_HEURISTIC_STOCK_RE = re.compile(
    r"(stock|inventory|level|tank|storage|headcount|balance|\bsoc\b|reserve|backlog)",
    re.I,
)
_HEURISTIC_INDEX_RE = re.compile(
    r"(\bhdd\b|\bcdd\b|degree[_\s-]*day|index|\bidx\b|ratio|share|\bpct\b|percent|occupancy|tariff|factor)",
    re.I,
)
_HEURISTIC_GROUP_RE = re.compile(r"\b(group|site|building|facility|plant|cluster|cohort|treatment|arm)\b", re.I)
_HEURISTIC_WEIGHT_RE = re.compile(r"\b(weight|sample_weight|obs_weight)\b", re.I)
_HEURISTIC_STATIC_RE = re.compile(r"\b(floor_area|area_m2|sqft|rated_capacity|vintage|envelope)\b", re.I)
_HEURISTIC_KNOWN_ADVANCE_RE = re.compile(
    r"\b(schedule|planned|holiday|weekend|weekday|shift|tariff|contract|calendar)\b",
    re.I,
)

_UNIT_TOKEN_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|[_\s(\[])mwh(?:$|[_\s)\]])", re.I), "MWh"),
    (re.compile(r"(?:^|[_\s(\[])kwh(?:$|[_\s)\]])", re.I), "kWh"),
    (re.compile(r"(?:^|[_\s(\[])wh(?:$|[_\s)\]])", re.I), "Wh"),
    (re.compile(r"(?:^|[_\s(\[])gj(?:$|[_\s)\]])", re.I), "GJ"),
    (re.compile(r"(?:^|[_\s(\[])mmbtu(?:$|[_\s)\]])", re.I), "MMBtu"),
    (re.compile(r"(?:^|[_\s(\[])therm(?:s)?(?:$|[_\s)\]])", re.I), "therm"),
    (re.compile(r"(?:^|[_\s(\[])mw(?:$|[_\s)\]])", re.I), "MW"),
    (re.compile(r"(?:^|[_\s(\[])kw(?:$|[_\s)\]])", re.I), "kW"),
    (re.compile(r"(?:^|[_\s(\[])(?:degf|fahrenheit|temp_f|t_f)(?:$|[_\s)\]])", re.I), "degF"),
    (re.compile(r"(?:^|[_\s(\[])(?:degc|celsius|temp_c|t_c|_c)(?:$|[_\s)\]])", re.I), "degC"),
    (re.compile(r"(?:^|[_\s(\[])m3(?:$|[_\s)\]])", re.I), "m3"),
)


def suggest_quantity_type(col_name: str, *, unit: str = "") -> QuantityType:
    """Suggest a ``QuantityType`` from column name / unit hints (suggestion only — DAT-4)."""
    probe = f"{col_name} {unit}".strip()
    if _HEURISTIC_RATE_RE.search(probe) and not re.search(r"kwh|mwh|\bwh\b", probe, re.I):
        return QuantityType.RATE
    if _HEURISTIC_STOCK_RE.search(probe):
        return QuantityType.STOCK
    if _HEURISTIC_INDEX_RE.search(probe):
        return QuantityType.INDEX
    if _HEURISTIC_FLOW_RE.search(probe) and not re.search(r"temp|humid|press", probe, re.I):
        return QuantityType.FLOW
    return QuantityType.QUALITY


def suggest_unit(col_name: str) -> str:
    """Suggest a physical unit string from common column-name suffixes (suggestion only)."""
    for pat, canon in _UNIT_TOKEN_HINTS:
        if pat.search(col_name):
            return canon
    return ""


def suggest_column_spec(
    col_name: str,
    series: pd.Series | None = None,
    *,
    is_time: bool = False,
    is_target: bool = False,
    unit: str = "",
) -> ColumnSpec:
    """Build a heuristic ``ColumnSpec`` suggestion (`suggested_by_heuristic=True`, DAT-4)."""
    name = str(col_name).strip()
    inferred_unit = str(unit).strip() or suggest_unit(name)

    if is_time:
        return ColumnSpec(
            name=name,
            role=ColumnRole.TIME,
            quantity_type=None,
            inclusion=Inclusion.KNOWN_IN_ADVANCE,
            unit="",
            availability=Availability.ALWAYS.value,
            suggested_by_heuristic=True,
            notes="Heuristic suggestion: timestamp column",
        )

    is_numeric = True if series is None else bool(pd.api.types.is_numeric_dtype(series))
    if is_target:
        role = ColumnRole.TARGET
    elif not is_numeric:
        if series is not None and 1 < int(series.nunique(dropna=True)) <= 20 and _HEURISTIC_GROUP_RE.search(name):
            role = ColumnRole.GROUP
        elif series is not None and 1 < int(series.nunique(dropna=True)) <= 12:
            role = ColumnRole.PREDICTOR
        else:
            role = ColumnRole.IGNORE
    elif _HEURISTIC_WEIGHT_RE.search(name):
        role = ColumnRole.WEIGHT
    elif _HEURISTIC_STATIC_RE.search(name):
        role = ColumnRole.STATIC_FACTOR
    elif _HEURISTIC_GROUP_RE.search(name) and series is not None and int(series.nunique(dropna=True)) <= 20:
        role = ColumnRole.GROUP
    else:
        role = ColumnRole.PREDICTOR

    qtype: QuantityType | None
    if role in (ColumnRole.IGNORE, ColumnRole.GROUP):
        qtype = None
    else:
        qtype = suggest_quantity_type(name, unit=inferred_unit)

    inclusion = (
        Inclusion.KNOWN_IN_ADVANCE
        if _HEURISTIC_KNOWN_ADVANCE_RE.search(name)
        else Inclusion.OBSERVED_ONLY
    )
    return ColumnSpec(
        name=name,
        role=role,
        quantity_type=qtype,
        inclusion=inclusion,
        unit=inferred_unit,
        availability=Availability.ALWAYS.value,
        suggested_by_heuristic=True,
        notes="Heuristic suggestion (override via ColumnSpec metadata)",
    )


def suggest_dataset_schema(
    df: pd.DataFrame,
    *,
    time_col: str | None = None,
    targets: Sequence[str] | None = None,
    units: Mapping[str, str] | None = None,
    dataset_name: str = "",
    dataset_sha256: str = "",
    timezone: str = "",
    outlier_policy: OutlierActionPolicy | str = OutlierActionPolicy.FLAG_ONLY,
    auto: Any | None = None,
) -> DatasetSchema:
    """Construct a heuristic ``DatasetSchema`` suggestion from ``df`` (logged to ``AutoLog``, DAT-4)."""
    cols = [str(c).strip() for c in df.columns]
    if len(cols) < 2:
        raise SchemaValidationError("Cannot suggest a DatasetSchema for a DataFrame with fewer than 2 columns.")

    resolved_time = str(time_col).strip() if time_col and str(time_col).strip() in cols else None
    if resolved_time is None:
        for c in cols:
            s = df[c]
            if pd.api.types.is_datetime64_any_dtype(s) or (
                not pd.api.types.is_numeric_dtype(s) and _HEURISTIC_TIME_RE.search(c)
            ):
                resolved_time = c
                break
    if resolved_time is None:
        resolved_time = cols[0]

    explicit_targets = [str(t).strip() for t in (targets or ()) if str(t).strip() in cols and str(t).strip() != resolved_time]
    if not explicit_targets:
        numeric_candidates = [
            c for c in cols if c != resolved_time and pd.api.types.is_numeric_dtype(df[c])
        ]
        flow_candidates = [c for c in numeric_candidates if suggest_quantity_type(c) == QuantityType.FLOW]
        if flow_candidates:
            explicit_targets = [flow_candidates[0]]
        elif numeric_candidates:
            explicit_targets = [numeric_candidates[0]]
        else:
            fallback = [c for c in cols if c != resolved_time]
            explicit_targets = [fallback[0]]

    units_lookup = {str(k).strip(): str(v).strip() for k, v in (units or {}).items() if str(v).strip()}
    specs = [
        suggest_column_spec(
            c,
            df[c],
            is_time=(c == resolved_time),
            is_target=(c in explicit_targets),
            unit=units_lookup.get(c, ""),
        )
        for c in cols
    ]
    schema = DatasetSchema(
        columns=specs,
        dataset_name=dataset_name,
        dataset_sha256=dataset_sha256,
        timezone=timezone,
        outlier_policy=_coerce_enum(OutlierActionPolicy, outlier_policy, "outlier_policy"),
    )
    if auto is not None and hasattr(auto, "note"):
        summary = ", ".join(
            f"{s.name}:{s.role.value}/{s.quantity_type.value if s.quantity_type else '-'}"
            for s in specs[:8]
        )
        if len(specs) > 8:
            summary += " ..."
        auto.note(
            "schema_suggestion",
            summary,
            f"columns={len(specs)}, time={resolved_time}, targets={explicit_targets}",
            "heuristic suggestion only; explicit DatasetSchema metadata overrides all name heuristics",
        )
    return schema


def coerce_dataset_schema(schema: DatasetSchema | Mapping[str, Any] | None) -> DatasetSchema | None:
    """Normalize an optional ``DatasetSchema`` or serialized dict across IPC boundaries."""
    if schema is None:
        return None
    if isinstance(schema, DatasetSchema):
        return schema.validate()
    if isinstance(schema, Mapping):
        return DatasetSchema.from_dict(schema)
    raise SchemaValidationError(f"Expected DatasetSchema or mapping, got {type(schema).__name__}.")


def save_schema_to_db(
    schema: DatasetSchema,
    *,
    dataset_id: int | None = None,
    name: str | None = None,
    ingestion_summary: dict[str, Any] | None = None,
) -> int:
    """Persist ``schema`` to the application database via ``core.store`` (DAT-2, DAT-5, DAT-8)."""
    from . import store

    return store.save_dataset_schema(
        schema,
        dataset_id=dataset_id,
        name=name,
        ingestion_summary=ingestion_summary,
    )


def load_schema_from_db(
    schema_id: int | None = None,
    *,
    dataset_id: int | None = None,
    verify_sha256: bool = True,
) -> DatasetSchema:
    """Load and SHA-256-verify a ``DatasetSchema`` from the application database (DAT-2, DAT-5, DAT-8)."""
    from . import store

    return store.load_dataset_schema(
        schema_id=schema_id,
        dataset_id=dataset_id,
        verify_sha256=verify_sha256,
    )

