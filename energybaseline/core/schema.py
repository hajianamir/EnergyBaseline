"""Declarative catalogue of every user-selectable setting.

Every setting defaults to ``AUTO``.  The Advanced dialog is generated from this list.
"""
from __future__ import annotations

from dataclasses import dataclass, field

AUTO = "AUTO"


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
    S("sampling_period", "Data & Temporal", "Sampling period", "choice", "AUTO (detected)",
      "Declared temporal granularity. AUTO detects it from the timestamps (you confirm in the Setup tab).",
      ("15min", "30min", "hourly", "daily", "weekly", "monthly", "quarterly", "annual", "custom:<seconds>", "mixed")),
    S("aggregation", "Data & Temporal", "Aggregation (finer data -> declared period)", "choice", "sum for energy-like columns, mean otherwise",
      "How sub-period rows are combined when data is finer than the declared period.", ("mean", "sum", "max", "first", "last")),
    S("timezone", "Data & Temporal", "Timezone", "text", "naive wall-clock (no conversion)",
      "IANA timezone of the timestamps, e.g. Europe/Berlin. AUTO keeps naive wall-clock time."),
    S("dst_handling", "Data & Temporal", "DST handling", "choice", "wall_clock", "Only used when a timezone is declared.",
      ("wall_clock", "utc", "drop_ambiguous")),
    S("baseline_window", "Data & Temporal", "Baseline window (start,end)", "text", "entire available range",
      "ISO dates 'YYYY-MM-DD,YYYY-MM-DD'. AUTO uses the full span after cleaning."),
    S("reporting_window", "Data & Temporal", "Reporting window (start,end)", "text", "none",
      "Optional. Rows in this window are excluded from model fitting and reserved for monitoring."),
    # ---- Preprocessing
    S("missing_strategy", "Preprocessing", "Missing-value strategy (predictors)", "choice", "linear (<5 %), knn (5-20 %), median (>20 %)",
      "Targets are never imputed; rows with missing target are excluded from training.",
      ("linear", "ffill", "median", "knn", "drop")),
    S("max_missing_rate", "Preprocessing", "Max column missing rate", "float", "0.40", "Columns above this rate are excluded."),
    S("outlier_method", "Preprocessing", "Outlier detection", "choice", "depends on skew, n, dimension",
      "Method used to flag outliers.",
      ("none", "robust_z", "iqr", "mad", "isolation_forest", "lof", "ocsvm", "mahalanobis", "hbos", "autoencoder")),
    S("outlier_treatment", "Preprocessing", "Outlier treatment", "choice", "depends on flagged share",
      "flag = keep; winsorize; interpolate (predictors); remove (rows leave training).",
      ("flag", "winsorize", "interpolate", "remove")),
    S("outlier_threshold", "Preprocessing", "Outlier threshold (robust z / IQR multiple)", "float", "3.5", "Higher = fewer flags."),
    S("corr_threshold", "Preprocessing", "Collinearity |r| threshold", "float", "0.95", "One of each pair above this is excluded."),
    S("vif_threshold", "Preprocessing", "VIF threshold", "float", "10 (5 for small n)", "Iterative VIF pruning."),
    S("min_variance", "Preprocessing", "Min relative variance", "float", "1e-8", "Near-constant columns are excluded."),
    S("leakage_threshold", "Preprocessing", "Leakage |r| threshold", "float", "0.99",
      "Energy-like columns with |r| above this with the target are excluded as leakage."),
    # ---- Feature engineering
    S("calendar_features", "Feature Engineering", "Calendar features", "bool", "on for sub-daily/daily", "Weekend flag, hour/day harmonics."),
    S("fourier_terms", "Feature Engineering", "Fourier harmonics", "int", "from autocorrelation", "Harmonics per detected period."),
    S("degree_days", "Feature Engineering", "Degree-day features (HDD/CDD)", "bool", "on if temperature column found", "Balance points fitted from data."),
    S("lags", "Feature Engineering", "Predictor lags (steps)", "list", "from lag-correlation gain", "Comma-separated, e.g. 1,24."),
    S("rolling_windows", "Feature Engineering", "Rolling-mean windows (steps)", "list", "from correlation gain", "Comma-separated."),
    S("interactions", "Feature Engineering", "Pairwise interactions", "bool", "on only if n > 20*p and p <= 6", "Only regularised/tree models see them."),
    S("holiday_country", "Feature Engineering", "Holiday calendar country code", "text", "none", "ISO code such as DE, US (needs 'holidays' package)."),
    # ---- Feature selection
    S("selection_method", "Feature Selection", "Selection method", "choice", "none (p<=8) / lasso / mutual_info",
      "How candidate features are pruned.", ("none", "correlation", "mutual_info", "lasso")),
    S("max_features", "Feature Selection", "Max features", "int", "min(p, n/20)", "Upper bound on retained features."),
    # ---- Modeling
    S("enable_nn", "Modeling", "Neural networks", "bool", "on if n >= 300 and torch present", "Deep models need enough data."),
    S("model_families", "Modeling", "Model families", "list", "all applicable", "linear,tree,kernel,nn (comma-separated)."),
    S("cv_scheme", "Modeling", "Validation scheme", "choice", "blocked_kfold (<2 y) / forward_chaining",
      "Time-series-aware splits with purge gap.", ("forward_chaining", "blocked_kfold")),
    S("cv_folds", "Modeling", "Validation folds", "int", "3-6 by n", "Number of outer folds."),
    S("hpo_trials", "Modeling", "HPO trials per tuned model", "int", "3-8 by n", "Optuna (TPE) trials; nested inside each fold."),
    S("nn_max_epochs", "Modeling", "NN max epochs", "int", "40-150 by n", "Early stopping is always on."),
    S("nn_seq_len", "Modeling", "NN sequence length (steps)", "int", "from granularity", "Window for LSTM/GRU/TCN/CNN."),
    S("parallel_targets", "Modeling", "Parallel targets", "int", "1 (sequential) or min(2, cores/2)", "Targets processed at once."),
    S("enable_mining", "Modeling", "Data-mining layer", "bool", "on", "Clustering, anomaly, association, TS mining, regimes."),
    # ---- Selection scoring
    S("weight_profile", "Selection Scoring", "Scoring weight profile", "choice", "balanced (interpretable if n<200)",
      "Multi-criteria weights.", ("balanced", "accuracy_first", "interpretable")),
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
    S("alert_mode", "Prediction Module Defaults", "Alert threshold mode", "choice", "max(10 %, 2 x CV(RMSE))",
      "How exceedances are flagged.", ("percent", "cvrmse_multiple", "interval")),
    S("alert_value", "Prediction Module Defaults", "Alert threshold value", "float", "10 (% or multiple)", "Value used by the mode."),
    S("default_source", "Prediction Module Defaults", "Default data source", "choice", "file", "Preselected input mode.",
      ("file", "sql", "manual")),
]
SETTINGS_BY_KEY = {s.key: s for s in SETTINGS}
CATEGORIES = list(dict.fromkeys(s.category for s in SETTINGS))


def default_config() -> dict[str, str]:
    return {s.key: AUTO for s in SETTINGS}
