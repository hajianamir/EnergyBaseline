"""Deterministic result cache, stage invalidation DAG, and zero-recomputation What-If engine (CACHE-1, CACHE-2, AC-23, AC-24).

Compliance & performance invariants:
* Every raw statistical computation is cached strictly by the 6-tuple
  ``(data_hash, variable_set, test_id, parameters, seed, library_versions)`` (CACHE-1).
* Inferential test execution strictly enforces ``tcp.assert_accepted()`` (which enforces
  ``vsg.assert_accepted()``) BEFORE touching any compute primitive; unaccepted gates raise
  :class:`ComplianceGateError` with ``engine_call_count == 0`` (PIPE-1, Gate G3 #1).
* Policy / threshold / multiple-testing / decision-rule / sign / gate-role changes are
  evaluated by ``what_if`` and ``decision_sensitivity_table`` purely from cached
  :class:`RawTestArtifact` records with **0 engine calls** (CACHE-2, Gate G3 #3).
* Every evaluated result stores **both** ``p_raw`` and ``p_adj`` (TCP-2, Gate G3 #2).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import math
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from .. import __version__
from .tcp import (
    TEST_CATALOG_SPECS,
    DecisionRule,
    ExpectedSign,
    FamilyDefinition,
    GateRole,
    MultipleTestingCorrection,
    TestActionPolicy,
    TestConfigPanel,
    adjust_p_values,
)
from .vsg import ComplianceGateError, VariableSelectionGate


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _sha256_str(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_library_versions(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Collect deterministic versions of core statistical libraries affecting test numerics."""
    if overrides is not None:
        return {str(k): str(v) for k, v in sorted(overrides.items())}
    out: dict[str, str] = {"energybaseline": __version__}
    for dist_name, key in (
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("statsmodels", "statsmodels"),
        ("scikit-learn", "sklearn"),
        ("pandas", "pandas"),
    ):
        try:
            out[key] = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            out[key] = "not_installed"
    return out


def compute_dataframe_hash(df: pd.DataFrame, columns: Sequence[str] | None = None) -> str:
    """Compute deterministic SHA-256 hash of selected DataFrame columns and values."""
    cols = [c for c in (columns if columns is not None else list(df.columns)) if c in df.columns]
    sub = df[cols]
    row_hashes = pd.util.hash_pandas_object(sub, index=True).to_numpy(dtype="uint64")
    meta = _canonical_json({"columns": cols, "dtypes": [str(sub[c].dtype) for c in cols], "n_rows": len(sub)})
    h = hashlib.sha256(meta.encode("utf-8"))
    h.update(row_hashes.tobytes())
    return h.hexdigest()


@dataclass(frozen=True)
class CacheKey:
    """Six-part deterministic cache key (CACHE-1)."""

    data_hash: str
    variable_set: tuple[str, ...]
    test_id: str
    parameters: str
    seed: int
    library_versions: str

    @classmethod
    def build(
        cls,
        *,
        data_hash: str,
        variable_set: Sequence[str],
        test_id: str,
        parameters: Mapping[str, Any] | None = None,
        seed: int = 0,
        library_versions: Mapping[str, str] | None = None,
    ) -> "CacheKey":
        norm_vars = tuple(sorted(str(v) for v in variable_set))
        norm_params = _canonical_json(dict(parameters or {}))
        norm_libs = _canonical_json(canonical_library_versions(library_versions))
        return cls(
            data_hash=str(data_hash),
            variable_set=norm_vars,
            test_id=str(test_id),
            parameters=norm_params,
            seed=int(seed),
            library_versions=norm_libs,
        )

    @property
    def key_sha256(self) -> str:
        return _sha256_str(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_hash": self.data_hash,
            "library_versions": json.loads(self.library_versions),
            "parameters": json.loads(self.parameters),
            "seed": self.seed,
            "test_id": self.test_id,
            "variable_set": list(self.variable_set),
        }


@dataclass(frozen=True)
class RawTestArtifact:
    """Engine-computed test statistic & raw p-value invariant to alpha/correction/decision policy."""

    test_id: str
    target: str
    variable: str
    family: str
    stage: str
    statistic: float
    p_raw: float
    df: float
    effect_size: float
    std_error: float
    null_value: float
    observed_sign: str
    n_used: int
    cache_key_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_key_sha256": self.cache_key_sha256,
            "df": self.df,
            "effect_size": self.effect_size,
            "family": self.family,
            "n_used": self.n_used,
            "null_value": self.null_value,
            "observed_sign": self.observed_sign,
            "p_raw": self.p_raw,
            "stage": self.stage,
            "statistic": self.statistic,
            "std_error": self.std_error,
            "target": self.target,
            "test_id": self.test_id,
            "variable": self.variable,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RawTestArtifact":
        return cls(
            test_id=str(d["test_id"]),
            target=str(d["target"]),
            variable=str(d.get("variable", "") or ""),
            family=str(d["family"]),
            stage=str(d["stage"]),
            statistic=float(d["statistic"]),
            p_raw=float(d["p_raw"]),
            df=float(d["df"]),
            effect_size=float(d["effect_size"]),
            std_error=float(d["std_error"]),
            null_value=float(d.get("null_value", 0.0)),
            observed_sign=str(d.get("observed_sign", "any")),
            n_used=int(d["n_used"]),
            cache_key_sha256=str(d["cache_key_sha256"]),
        )


@dataclass(frozen=True)
class EvaluatedTestResult:
    """Policy-evaluated test decision storing BOTH raw and adjusted p-values (TCP-2, CACHE-2)."""

    test_id: str
    target: str
    variable: str
    family: str
    family_key: str
    family_size: int
    statistic: float
    p_raw: float
    p_adj: float
    alpha: float
    correction: str
    confidence_level: float
    ci_low: float
    ci_high: float
    effect_size: float
    min_effect_size: float
    decision_rule: str
    rejected_null: bool
    expected_sign: str
    observed_sign: str
    sign_conflict: bool
    gate_role: str
    action_policy: str
    decision: str
    downstream_action: str
    n_used: int
    cache_key_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_policy": self.action_policy,
            "alpha": self.alpha,
            "cache_key_sha256": self.cache_key_sha256,
            "ci_high": self.ci_high,
            "ci_low": self.ci_low,
            "confidence_level": self.confidence_level,
            "correction": self.correction,
            "decision": self.decision,
            "decision_rule": self.decision_rule,
            "downstream_action": self.downstream_action,
            "effect_size": self.effect_size,
            "expected_sign": self.expected_sign,
            "family": self.family,
            "family_key": self.family_key,
            "family_size": self.family_size,
            "gate_role": self.gate_role,
            "min_effect_size": self.min_effect_size,
            "n_used": self.n_used,
            "observed_sign": self.observed_sign,
            "p_adj": self.p_adj,
            "p_raw": self.p_raw,
            "rejected_null": self.rejected_null,
            "sign_conflict": self.sign_conflict,
            "statistic": self.statistic,
            "target": self.target,
            "test_id": self.test_id,
            "variable": self.variable,
        }


class ResultCache:
    """Content-addressed deterministic result store for raw statistical artifacts and stage outputs."""

    def __init__(self) -> None:
        self._entries: dict[str, RawTestArtifact] = {}
        self._keys: dict[str, CacheKey] = {}
        self._stage_entries: dict[str, Any] = {}
        self.hits: int = 0
        self.misses: int = 0

    def get(self, key: CacheKey) -> RawTestArtifact | None:
        sha = key.key_sha256
        if sha in self._entries:
            self.hits += 1
            return self._entries[sha]
        self.misses += 1
        return None

    def put(self, key: CacheKey, artifact: RawTestArtifact) -> str:
        sha = key.key_sha256
        self._keys[sha] = key
        self._entries[sha] = artifact
        return sha

    def get_or_compute_stage(
        self,
        stage_name: str,
        upstream_key_payload: Mapping[str, Any],
        compute_fn: Callable[[], Any],
    ) -> tuple[Any, bool, str]:
        """Return ``(value, was_cached, stage_key_sha256)`` for selective stage invalidation (AC-23)."""
        stage_sha = _sha256_str(_canonical_json({"payload": dict(upstream_key_payload), "stage": stage_name}))
        if stage_sha in self._stage_entries:
            return self._stage_entries[stage_sha], True, stage_sha
        val = compute_fn()
        self._stage_entries[stage_sha] = val
        return val, False, stage_sha

    def invalidate_where(
        self,
        *,
        data_hash: str | None = None,
        test_id: str | None = None,
        variable: str | None = None,
    ) -> int:
        """Selectively evict matching cache entries without touching independent entries."""
        to_remove: list[str] = []
        for sha, key in self._keys.items():
            if data_hash is not None and key.data_hash != data_hash:
                continue
            if test_id is not None and key.test_id != test_id:
                continue
            if variable is not None and variable not in key.variable_set:
                continue
            to_remove.append(sha)
        for sha in to_remove:
            self._keys.pop(sha, None)
            self._entries.pop(sha, None)
        return len(to_remove)

    @property
    def size(self) -> int:
        return len(self._entries)

    def snapshot_entries(self) -> list[dict[str, Any]]:
        return [
            {"artifact": self._entries[sha].to_dict(), "key": self._keys[sha].to_dict(), "key_sha256": sha}
            for sha in sorted(self._entries.keys())
        ]


# ===================================================================== Deterministic Test Primitives
# Real fast statistical primitives over (df, target, variable, params, seed) used by Phase 3 compliance
# caching/what-if verification; Phase 4 expands T1..T14 into full R-referenced test contracts.


def _clean_series(df: pd.DataFrame, col: str) -> np.ndarray:
    vals = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
    return vals[np.isfinite(vals)]


def _clean_pair(df: pd.DataFrame, x_col: str, y_col: str) -> tuple[np.ndarray, np.ndarray]:
    x = pd.to_numeric(df[x_col], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(df[y_col], errors="coerce").to_numpy(dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    return x[m], y[m]


def _sign_label(val: float, piecewise: bool = False) -> str:
    if piecewise:
        return ExpectedSign.PIECEWISE_V.value
    if abs(val) <= 1e-12:
        return ExpectedSign.ANY.value
    return ExpectedSign.POSITIVE.value if val > 0.0 else ExpectedSign.NEGATIVE.value


def _prim_stationarity_diff_t(
    df: pd.DataFrame, target: str, variable: str, params: Mapping[str, Any], seed: int
) -> tuple[float, float, float, float, float, str, int]:
    _ = (params, seed)
    col = variable or target
    s = _clean_series(df, col)
    n = int(s.size)
    if n < 6 or float(np.std(s)) <= 1e-12:
        return 0.0, 1.0, max(1.0, float(n - 2)), 0.0, 0.1, ExpectedSign.ANY.value, n
    y_lag = s[:-1] - float(np.mean(s[:-1]))
    dy = np.diff(s)
    ssx = float(np.dot(y_lag, y_lag)) + 1e-12
    gamma = float(np.dot(y_lag, dy) / ssx)
    resid = dy - gamma * y_lag
    dof = max(1.0, float(dy.size - 1))
    s2 = float(np.dot(resid, resid) / dof)
    se = math.sqrt(max(s2 / ssx, 1e-18))
    t_stat = gamma / se
    p_raw = float(np.clip(2.0 * sp_stats.t.sf(abs(t_stat), df=dof), 0.0, 1.0))
    eff = float(np.clip(abs(gamma), 0.0, 2.0))
    return t_stat, p_raw, dof, gamma, se, _sign_label(eff), n


def _prim_autocorrelation_box_pierce(
    df: pd.DataFrame, target: str, variable: str, params: Mapping[str, Any], seed: int
) -> tuple[float, float, float, float, float, str, int]:
    _ = (variable, seed)
    s = _clean_series(df, target)
    n = int(s.size)
    max_lags = max(1, min(int(params.get("max_lags", 4)), max(1, n // 4)))
    if n < 8 or float(np.std(s)) <= 1e-12:
        return 0.0, 1.0, float(max_lags), 0.0, 0.1, ExpectedSign.ANY.value, n
    sc = s - float(np.mean(s))
    denom = float(np.dot(sc, sc)) + 1e-12
    rhos = [float(np.dot(sc[:-k], sc[k:]) / denom) for k in range(1, max_lags + 1)]
    q_stat = float(n * (n + 2) * sum((r * r) / max(1, n - k) for k, r in enumerate(rhos, 1)))
    p_raw = float(np.clip(sp_stats.chi2.sf(q_stat, df=max_lags), 0.0, 1.0))
    rho1 = rhos[0]
    se = 1.0 / math.sqrt(max(float(n), 4.0))
    return q_stat, p_raw, float(max_lags), rho1, se, _sign_label(rho1), n


def _prim_heteroscedasticity_levene(
    df: pd.DataFrame, target: str, variable: str, params: Mapping[str, Any], seed: int
) -> tuple[float, float, float, float, float, str, int]:
    _ = (variable, params, seed)
    s = _clean_series(df, target)
    n = int(s.size)
    if n < 8 or float(np.std(s)) <= 1e-12:
        return 0.0, 1.0, 1.0, 0.0, 0.1, ExpectedSign.ANY.value, n
    half = n // 2
    stat, p_val = sp_stats.levene(s[:half], s[half:], center="median")
    stat_f = 0.0 if not math.isfinite(stat) else float(stat)
    p_f = 1.0 if not math.isfinite(p_val) else float(np.clip(p_val, 0.0, 1.0))
    s1 = float(np.std(s[:half], ddof=1)) + 1e-12
    s2 = float(np.std(s[half:], ddof=1)) + 1e-12
    log_vr = math.log(s2 / s1)
    se = math.sqrt(2.0 / max(float(half - 1), 2.0))
    return stat_f, p_f, float(n - 2), log_vr, se, _sign_label(log_vr), n


def _prim_normality_jarque_bera(
    df: pd.DataFrame, target: str, variable: str, params: Mapping[str, Any], seed: int
) -> tuple[float, float, float, float, float, str, int]:
    _ = (variable, params, seed)
    s = _clean_series(df, target)
    n = int(s.size)
    if n < 8 or float(np.std(s)) <= 1e-12:
        return 0.0, 1.0, 2.0, 0.0, 0.1, ExpectedSign.ANY.value, n
    jb_res = sp_stats.jarque_bera(s)
    stat_f = float(jb_res.statistic) if math.isfinite(jb_res.statistic) else 0.0
    p_f = float(np.clip(jb_res.pvalue, 0.0, 1.0)) if math.isfinite(jb_res.pvalue) else 1.0
    skew_v = float(sp_stats.skew(s, bias=False))
    se = math.sqrt(6.0 / max(float(n), 6.0))
    return stat_f, p_f, 2.0, skew_v, se, _sign_label(skew_v), n


def _prim_structural_break_welch(
    df: pd.DataFrame, target: str, variable: str, params: Mapping[str, Any], seed: int
) -> tuple[float, float, float, float, float, str, int]:
    _ = (variable, params, seed)
    s = _clean_series(df, target)
    n = int(s.size)
    if n < 8 or float(np.std(s)) <= 1e-12:
        return 0.0, 1.0, 1.0, 0.0, 0.1, ExpectedSign.ANY.value, n
    split = int(max(3, min(n - 3, round(float(params.get("split_ratio", 0.5)) * n))))
    a, b = s[:split], s[split:]
    res = sp_stats.ttest_ind(b, a, equal_var=False)
    stat_f = float(res.statistic) if math.isfinite(res.statistic) else 0.0
    p_f = float(np.clip(res.pvalue, 0.0, 1.0)) if math.isfinite(res.pvalue) else 1.0
    pooled_sd = float(np.std(s, ddof=1)) + 1e-12
    cohen_d = float((np.mean(b) - np.mean(a)) / pooled_sd)
    se = math.sqrt((1.0 / a.size) + (1.0 / b.size))
    return stat_f, p_f, float(n - 2), cohen_d, se, _sign_label(cohen_d), n


def _prim_mann_whitney_shift(
    df: pd.DataFrame, target: str, variable: str, params: Mapping[str, Any], seed: int
) -> tuple[float, float, float, float, float, str, int]:
    _ = (variable, params, seed)
    s = _clean_series(df, target)
    n = int(s.size)
    if n < 8 or float(np.std(s)) <= 1e-12:
        return 0.0, 1.0, 1.0, 0.0, 0.1, ExpectedSign.ANY.value, n
    half = n // 2
    u_stat, p_val = sp_stats.mannwhitneyu(s[:half], s[half:], alternative="two-sided")
    n1, n2 = float(half), float(n - half)
    rank_biserial = float(1.0 - (2.0 * u_stat) / max(1.0, n1 * n2))
    se = 1.0 / math.sqrt(max(float(n), 4.0))
    return float(u_stat), float(np.clip(p_val, 0.0, 1.0)), float(n - 2), rank_biserial, se, _sign_label(rank_biserial), n


def _prim_bivariate_association(
    df: pd.DataFrame, target: str, variable: str, params: Mapping[str, Any], seed: int
) -> tuple[float, float, float, float, float, str, int]:
    _ = (params, seed)
    if not variable or variable not in df.columns:
        return _prim_autocorrelation_box_pierce(df, target, "", params, seed)
    if not (pd.api.types.is_numeric_dtype(df[variable]) or pd.api.types.is_bool_dtype(df[variable])):
        return 0.0, 1.0, 1.0, 0.0, 0.1, ExpectedSign.ANY.value, len(df)
    x, y = _clean_pair(df, variable, target)
    n = int(x.size)
    if n < 6 or float(np.std(x)) <= 1e-12 or float(np.std(y)) <= 1e-12:
        return 0.0, 1.0, max(1.0, float(n - 2)), 0.0, 0.1, ExpectedSign.ANY.value, n
    r_val, p_val = sp_stats.pearsonr(x, y)
    r_f = 0.0 if not math.isfinite(r_val) else float(np.clip(r_val, -0.999999, 0.999999))
    p_f = 1.0 if not math.isfinite(p_val) else float(np.clip(p_val, 0.0, 1.0))
    dof = max(1.0, float(n - 2))
    t_stat = r_f * math.sqrt(dof / max(1e-12, 1.0 - r_f * r_f))
    se = math.sqrt(max(1e-12, (1.0 - r_f * r_f) / dof))
    # Detect V-shaped piecewise temperature relation when linear r is weak
    knot = float(np.median(x))
    x_abs = np.abs(x - knot)
    is_v = False
    if float(np.std(x_abs)) > 1e-12:
        rv_abs, pv_abs = sp_stats.pearsonr(x_abs, y)
        if math.isfinite(rv_abs) and abs(rv_abs) > abs(r_f) + 0.25 and pv_abs < p_f:
            is_v = True
            r_f = float(rv_abs)
            p_f = float(np.clip(pv_abs, 0.0, 1.0))
            t_stat = r_f * math.sqrt(dof / max(1e-12, 1.0 - r_f * r_f))
    return t_stat, p_f, dof, r_f, se, _sign_label(r_f, piecewise=is_v), n


def _prim_missingness_mcar_shift(
    df: pd.DataFrame, target: str, variable: str, params: Mapping[str, Any], seed: int
) -> tuple[float, float, float, float, float, str, int]:
    _ = (variable, params, seed)
    y = pd.to_numeric(df[target], errors="coerce")
    any_miss = df.isna().any(axis=1).to_numpy()
    y_obs_complete = y[~any_miss].dropna().to_numpy(dtype=float)
    y_obs_partial = y[any_miss].dropna().to_numpy(dtype=float)
    n = int(y.notna().sum())
    if y_obs_complete.size < 3 or y_obs_partial.size < 3:
        return 0.0, 1.0, max(1.0, float(n - 2)), 0.0, 0.1, ExpectedSign.ANY.value, n
    res = sp_stats.ttest_ind(y_obs_complete, y_obs_partial, equal_var=False)
    stat_f = float(res.statistic) if math.isfinite(res.statistic) else 0.0
    p_f = float(np.clip(res.pvalue, 0.0, 1.0)) if math.isfinite(res.pvalue) else 1.0
    sd = float(np.std(y.dropna().to_numpy(dtype=float), ddof=1)) + 1e-12
    eff = float((np.mean(y_obs_partial) - np.mean(y_obs_complete)) / sd)
    se = math.sqrt((1.0 / y_obs_complete.size) + (1.0 / y_obs_partial.size))
    return stat_f, p_f, float(max(1, n - 2)), eff, se, _sign_label(eff), n


_ENGINE_PRIMITIVES: MappingProxyType[
    str,
    Callable[[pd.DataFrame, str, str, Mapping[str, Any], int], tuple[float, float, float, float, float, str, int]],
] = MappingProxyType(
    {
        "T1": _prim_stationarity_diff_t,
        "T2": _prim_autocorrelation_box_pierce,
        "T3": _prim_heteroscedasticity_levene,
        "T4": _prim_normality_jarque_bera,
        "T5": _prim_structural_break_welch,
        "T6": _prim_structural_break_welch,
        "T7": _prim_mann_whitney_shift,
        "T8": _prim_bivariate_association,
        "T9": _prim_missingness_mcar_shift,
        "T10": _prim_structural_break_welch,
        "T11": _prim_mann_whitney_shift,
        "T12": _prim_structural_break_welch,
        "T13": _prim_bivariate_association,
        "T14": _prim_bivariate_association,
    }
)


def _family_bucket_key(art: RawTestArtifact, family_def: str) -> str:
    if family_def == FamilyDefinition.GLOBAL.value:
        return "global"
    if family_def == FamilyDefinition.PER_TARGET.value:
        return art.target
    if family_def == FamilyDefinition.PER_VARIABLE.value:
        return f"{art.target}:{art.variable or '(target)'}"
    return f"{art.target}:{art.family}"


def _evaluate_artifacts_under_tcp(
    raw_artifacts: Sequence[RawTestArtifact],
    tcp: TestConfigPanel,
) -> list[EvaluatedTestResult]:
    """Pure in-memory policy evaluation over cached RawTestArtifacts (0 engine calls)."""
    if not raw_artifacts:
        return []

    buckets: dict[str, list[int]] = {}
    for idx, art in enumerate(raw_artifacts):
        fk = _family_bucket_key(art, tcp.family_definition)
        buckets.setdefault(fk, []).append(idx)

    adj_by_idx: dict[int, tuple[float, int, str]] = {}
    for fk, indices in buckets.items():
        raw_ps = [raw_artifacts[i].p_raw for i in indices]
        adjusted_rows = adjust_p_values(raw_ps, method=tcp.correction)
        for loc_i, row in zip(indices, adjusted_rows):
            adj_by_idx[loc_i] = (float(row["p_adj"]), int(row["family_size"]), fk)

    tail_prob = 0.5 * (1.0 + tcp.confidence_level)
    results: list[EvaluatedTestResult] = []

    for idx, art in enumerate(raw_artifacts):
        p_adj, fam_size, fam_key = adj_by_idx[idx]
        dof = max(1.0, art.df)
        crit = float(sp_stats.t.ppf(tail_prob, df=dof)) if dof < 5000 else float(sp_stats.norm.ppf(tail_prob))
        ci_low = round(art.effect_size - crit * art.std_error, 8)
        ci_high = round(art.effect_size + crit * art.std_error, 8)

        panel = tcp.test_panels[art.test_id]
        eff_min = max(tcp.min_effect_size, panel.min_effect_size)
        ci_excludes_null = (ci_low > art.null_value) or (ci_high < art.null_value)

        if tcp.decision_rule == DecisionRule.P_ONLY.value:
            rejected = p_adj <= tcp.alpha
        elif tcp.decision_rule == DecisionRule.P_AND_EFFECT_SIZE.value:
            rejected = (p_adj <= tcp.alpha) and (abs(art.effect_size) >= eff_min)
        elif tcp.decision_rule == DecisionRule.CI_EXCLUDES_NULL.value:
            rejected = ci_excludes_null
        else:
            rejected = (p_adj <= tcp.alpha) and (abs(art.effect_size) >= eff_min) and ci_excludes_null

        exp_sign = (
            tcp.expected_signs.get(art.target, {}).get(art.variable, ExpectedSign.ANY.value)
            if art.variable
            else ExpectedSign.ANY.value
        )
        sign_conflict = (
            exp_sign != ExpectedSign.ANY.value
            and art.observed_sign != ExpectedSign.ANY.value
            and exp_sign != art.observed_sign
        )

        if sign_conflict and panel.gate_role == GateRole.HARD_GATE.value:
            decision = "blocked_sign_conflict"
            downstream = TestActionPolicy.BLOCK_CANDIDATE.value
        elif sign_conflict:
            decision = "sign_conflict"
            downstream = panel.action_policy
        elif rejected and panel.gate_role == GateRole.HARD_GATE.value:
            decision = "reject_null_hard_gate"
            downstream = panel.action_policy
        elif rejected:
            decision = "reject_null"
            downstream = panel.action_policy
        else:
            decision = "retain_null"
            downstream = "proceed_standard"

        results.append(
            EvaluatedTestResult(
                test_id=art.test_id,
                target=art.target,
                variable=art.variable,
                family=art.family,
                family_key=fam_key,
                family_size=fam_size,
                statistic=round(art.statistic, 8),
                p_raw=float(art.p_raw),
                p_adj=float(p_adj),
                alpha=tcp.alpha,
                correction=tcp.correction,
                confidence_level=tcp.confidence_level,
                ci_low=ci_low,
                ci_high=ci_high,
                effect_size=round(art.effect_size, 8),
                min_effect_size=eff_min,
                decision_rule=tcp.decision_rule,
                rejected_null=bool(rejected),
                expected_sign=exp_sign,
                observed_sign=art.observed_sign,
                sign_conflict=bool(sign_conflict),
                gate_role=panel.gate_role,
                action_policy=panel.action_policy,
                decision=decision,
                downstream_action=downstream,
                n_used=art.n_used,
                cache_key_sha256=art.cache_key_sha256,
            )
        )
    return results


class CachedComplianceRunner:
    """Gated test execution engine + zero-recomputation What-If evaluator (PIPE-1..3, CACHE-1..2)."""

    def __init__(
        self,
        cache: ResultCache | None = None,
        *,
        seed: int = 42,
        library_versions: Mapping[str, str] | None = None,
    ) -> None:
        self.cache: ResultCache = cache if cache is not None else ResultCache()
        self.seed: int = int(seed)
        self.library_versions: dict[str, str] = canonical_library_versions(library_versions)
        self.engine_call_count: int = 0
        self.calls_by_test: dict[str, int] = {}
        self.stage_call_counts: dict[str, int] = {
            "ingest": 0,
            "temporal": 0,
            "vsg_profile": 0,
            "test_engine": 0,
            "decision_eval": 0,
        }
        self._last_raw_artifacts: list[RawTestArtifact] = []
        self._last_evaluated: list[EvaluatedTestResult] = []

    def run_compliance_stage_dag(
        self,
        df: pd.DataFrame,
        vsg: VariableSelectionGate,
        tcp: TestConfigPanel,
        *,
        temporal_params: Mapping[str, Any] | None = None,
        stages_to_run: Sequence[str] = ("pre_model",),
    ) -> dict[str, Any]:
        """Execute typed pipeline DAG with stage-keyed caching so invalidation recomputes only dependents."""
        # PIPE-1 / Gate G3 #1: strictly block before any stage or engine call if VSG or TCP is unaccepted!
        tcp.assert_accepted()

        raw_df_hash = vsg.dataset_sha256 or compute_dataframe_hash(df)

        def _do_ingest() -> dict[str, Any]:
            self.stage_call_counts["ingest"] += 1
            return {"columns": list(df.columns), "data_hash": raw_df_hash, "n_rows": len(df)}

        ingest_out, _, ingest_sha = self.cache.get_or_compute_stage(
            "ingest",
            {"data_hash": raw_df_hash},
            _do_ingest,
        )

        def _do_temporal() -> dict[str, Any]:
            self.stage_call_counts["temporal"] += 1
            return {"ingest_sha": ingest_sha, "temporal_params": dict(temporal_params or {})}

        _, _, temporal_sha = self.cache.get_or_compute_stage(
            "temporal",
            {"ingest_sha": ingest_sha, "temporal_params": dict(temporal_params or {})},
            _do_temporal,
        )

        vsg_stage_shas: dict[str, str] = {}
        for t in vsg.targets:
            inc_vars = vsg.included_variables(t)

            def _do_vsg_t(tgt: str = t, inc: list[str] = inc_vars) -> dict[str, Any]:
                self.stage_call_counts["vsg_profile"] += 1
                return {"included": inc, "target": tgt}

            _, _, v_sha = self.cache.get_or_compute_stage(
                "vsg_profile",
                {"included": inc_vars, "target": t, "temporal_sha": temporal_sha},
                _do_vsg_t,
            )
            vsg_stage_shas[t] = v_sha

        evaluated = self.execute_tests(df, vsg, tcp, data_hash=temporal_sha, stages=stages_to_run)
        return {
            "evaluated_results": [r.to_dict() for r in evaluated],
            "ingest": ingest_out,
            "stage_call_counts": dict(self.stage_call_counts),
            "temporal_sha256": temporal_sha,
            "vsg_stage_shas": vsg_stage_shas,
        }

    def execute_tests(
        self,
        df: pd.DataFrame,
        vsg: VariableSelectionGate,
        tcp: TestConfigPanel,
        *,
        data_hash: str | None = None,
        stages: Sequence[str] = ("pre_model",),
    ) -> list[EvaluatedTestResult]:
        """Run enabled inferential tests, enforcing VSG/TCP acceptance and 6-part result caching."""
        # Hard PIPE-1 / PIPE-3 guard BEFORE any test primitive can run!
        tcp.assert_accepted()
        if tcp.vsg is not vsg and tcp.vsg.vsg_sha256 != vsg.vsg_sha256:
            raise ComplianceGateError("TCP is bound to a stale VSG snapshot; re-accept TCP against active VSG.")

        eff_data_hash = data_hash or vsg.dataset_sha256 or compute_dataframe_hash(df)
        allowed_stages = set(stages)
        raw_artifacts: list[RawTestArtifact] = []

        for t in vsg.targets:
            inc_vars = vsg.included_variables(t)
            scope = tcp.build_scope_matrix(t)

            for tid in sorted(tcp.test_panels.keys()):
                panel = tcp.test_panels[tid]
                if not panel.enabled:
                    continue
                spec = TEST_CATALOG_SPECS[tid]
                if spec.stage not in allowed_stages:
                    continue

                targets_vars: list[tuple[str, tuple[str, ...]]] = []
                if spec.per_variable:
                    for v in inc_vars:
                        if scope.get(v, {}).get(spec.family, {}).get("in_scope", False):
                            targets_vars.append((v, (t, v)))
                else:
                    # Target-only tests depend on target; T8/T9 depend on active variable_set
                    var_tuple = (t, *inc_vars) if tid in ("T8", "T9") else (t,)
                    targets_vars.append(("", var_tuple))

                for var_name, key_var_set in targets_vars:
                    engine_params = {
                        "mode": panel.mode,
                        "parameters": dict(sorted(panel.parameters.items())),
                        "target": t,
                        "variable": var_name,
                    }
                    ckey = CacheKey.build(
                        data_hash=eff_data_hash,
                        variable_set=key_var_set,
                        test_id=tid,
                        parameters=engine_params,
                        seed=self.seed,
                        library_versions=self.library_versions,
                    )
                    cached = self.cache.get(ckey)
                    if cached is None:
                        prim = _ENGINE_PRIMITIVES[tid]
                        stat_v, p_raw, dof, eff_sz, se_v, obs_sgn, n_u = prim(
                            df, t, var_name, panel.parameters, self.seed
                        )
                        self.engine_call_count += 1
                        self.stage_call_counts["test_engine"] += 1
                        self.calls_by_test[tid] = self.calls_by_test.get(tid, 0) + 1
                        cached = RawTestArtifact(
                            test_id=tid,
                            target=t,
                            variable=var_name,
                            family=spec.family,
                            stage=spec.stage,
                            statistic=stat_v,
                            p_raw=p_raw,
                            df=dof,
                            effect_size=eff_sz,
                            std_error=se_v,
                            null_value=panel.null_value,
                            observed_sign=obs_sgn,
                            n_used=n_u,
                            cache_key_sha256=ckey.key_sha256,
                        )
                        self.cache.put(ckey, cached)
                    raw_artifacts.append(cached)

        self._last_raw_artifacts = list(raw_artifacts)
        self.stage_call_counts["decision_eval"] += 1
        evaluated = _evaluate_artifacts_under_tcp(raw_artifacts, tcp)
        self._last_evaluated = evaluated
        return evaluated

    def what_if(
        self,
        what_if_tcp: TestConfigPanel,
        *,
        raw_artifacts: Sequence[RawTestArtifact] | None = None,
        baseline_evaluated: Sequence[EvaluatedTestResult] | None = None,
    ) -> dict[str, Any]:
        """Re-evaluate decisions under ``what_if_tcp`` with strictly ZERO engine recomputations (CACHE-2)."""
        artifacts = list(raw_artifacts) if raw_artifacts is not None else list(self._last_raw_artifacts)
        if not artifacts:
            raise ComplianceGateError("No cached raw test artifacts available; run execute_tests() after gate acceptance first.")
        base_eval = list(baseline_evaluated) if baseline_evaluated is not None else list(self._last_evaluated)

        calls_before = self.engine_call_count
        self.stage_call_counts["decision_eval"] += 1
        new_eval = _evaluate_artifacts_under_tcp(artifacts, what_if_tcp)
        delta_calls = self.engine_call_count - calls_before
        if delta_calls != 0:  # Defensive invariant
            raise RuntimeError(f"CACHE-2 violation: what_if invoked {delta_calls} engine calls.")

        flips: list[dict[str, Any]] = []
        for old_r, new_r in zip(base_eval, new_eval):
            if old_r.rejected_null != new_r.rejected_null or old_r.decision != new_r.decision:
                flips.append(
                    {
                        "new_decision": new_r.decision,
                        "new_p_adj": new_r.p_adj,
                        "new_rejected_null": new_r.rejected_null,
                        "old_decision": old_r.decision,
                        "old_p_adj": old_r.p_adj,
                        "old_rejected_null": old_r.rejected_null,
                        "p_raw": new_r.p_raw,
                        "target": new_r.target,
                        "test_id": new_r.test_id,
                        "variable": new_r.variable,
                    }
                )
        return {
            "alpha": what_if_tcp.alpha,
            "confidence_level": what_if_tcp.confidence_level,
            "correction": what_if_tcp.correction,
            "decision_rule": what_if_tcp.decision_rule,
            "engine_calls_delta": delta_calls,
            "evaluated_results": [r.to_dict() for r in new_eval],
            "flip_count": len(flips),
            "flipped_decisions": flips,
        }

    def decision_sensitivity_table(
        self,
        base_tcp: TestConfigPanel,
        *,
        alphas: Sequence[float] = (0.01, 0.05, 0.10),
        corrections: Sequence[str] = tuple(c.value for c in MultipleTestingCorrection),
        rules: Sequence[str] = (DecisionRule.P_ONLY.value, DecisionRule.P_AND_EFFECT_SIZE.value),
        raw_artifacts: Sequence[RawTestArtifact] | None = None,
    ) -> dict[str, Any]:
        """Sweep (alpha x correction x decision_rule) over cached artifacts with ZERO engine calls."""
        artifacts = list(raw_artifacts) if raw_artifacts is not None else list(self._last_raw_artifacts)
        if not artifacts:
            raise ComplianceGateError("Cannot build sensitivity table before initial test execution.")

        calls_before = self.engine_call_count
        scenario_summaries: list[dict[str, Any]] = []
        per_test_rejects: dict[str, list[bool]] = {
            f"{a.target}:{a.test_id}:{a.variable}": [] for a in artifacts
        }

        for a_val in alphas:
            for corr in corrections:
                for rule in rules:
                    eff_min = max(0.05, base_tcp.min_effect_size) if rule != DecisionRule.P_ONLY.value else 0.0
                    probe_tcp = base_tcp.clone_for_what_if(
                        alpha=a_val,
                        correction=corr,
                        decision_rule=rule,
                        min_effect_size=eff_min,
                    )
                    eval_rows = _evaluate_artifacts_under_tcp(artifacts, probe_tcp)
                    n_rej = sum(1 for r in eval_rows if r.rejected_null)
                    scenario_summaries.append(
                        {
                            "alpha": a_val,
                            "correction": corr,
                            "decision_rule": rule,
                            "rejected_count": n_rej,
                            "total_tests": len(eval_rows),
                        }
                    )
                    for r in eval_rows:
                        key = f"{r.target}:{r.test_id}:{r.variable}"
                        per_test_rejects[key].append(r.rejected_null)

        test_stability: list[dict[str, Any]] = []
        for art in artifacts:
            key = f"{art.target}:{art.test_id}:{art.variable}"
            decisions = per_test_rejects[key]
            rej_rate = sum(1 for x in decisions if x) / max(1, len(decisions))
            fragile = 0.0 < rej_rate < 1.0
            test_stability.append(
                {
                    "fragile": fragile,
                    "p_raw": art.p_raw,
                    "rejection_rate": round(rej_rate, 4),
                    "scenarios_evaluated": len(decisions),
                    "target": art.target,
                    "test_id": art.test_id,
                    "variable": art.variable,
                }
            )

        return {
            "engine_calls_delta": self.engine_call_count - calls_before,
            "fragile_test_count": sum(1 for row in test_stability if row["fragile"]),
            "scenarios": scenario_summaries,
            "tests": test_stability,
        }

    # ------------------------------------------------------------------ Phase 4 Test Catalog Suite (T1..T14)
    def execute_catalog_suite(
        self,
        df: pd.DataFrame,
        vsg: VariableSelectionGate,
        tcp: TestConfigPanel,
        *,
        oof_errors_by_target: Mapping[str, Mapping[str, Any]] | None = None,
        mode: str = "full",
        allow_hac: bool = True,
    ) -> dict[str, list[Any]]:
        """Execute Phase 4 ``T1..T14`` catalog per target after VSG & TCP acceptance, caching raw statistics."""
        from ..tests_catalog import ALL_CATALOG_TEST_IDS, apply_family_multiplicity, run_full_test_catalog

        tcp.assert_accepted()
        group_col: str | None = None
        if vsg.schema is not None:
            for col_spec in vsg.schema.columns:
                if col_spec.role.value == "group" and col_spec.name in df.columns:
                    group_col = col_spec.name
                    break

        out_by_target: dict[str, list[Any]] = {}
        for target in vsg.targets:
            preds = list(vsg.included_variables(target))
            oof_map = (oof_errors_by_target or {}).get(target)
            stage_payload = {
                "allow_hac": allow_hac,
                "dataset_sha256": vsg.dataset_sha256,
                "enabled": {tid: p.enabled for tid, p in tcp.test_panels.items()},
                "group_col": group_col,
                "mode": mode,
                "oof_models": sorted(oof_map.keys()) if oof_map else [],
                "params": {tid: p.parameters for tid, p in tcp.test_panels.items()},
                "predictors": preds,
                "target": target,
            }
            cat_list, _, _ = self.cache.get_or_compute_stage(
                f"phase4_catalog:{target}",
                stage_payload,
                lambda t=target, pr=preds, om=oof_map: run_full_test_catalog(
                    df,
                    target_col=t,
                    predictor_cols=pr,
                    group_col=group_col,
                    oof_errors_by_model=om,
                    panel=tcp,
                    mode=mode,
                    allow_hac=allow_hac,
                    seed=self.seed,
                ),
            )
            out_by_target[target] = apply_family_multiplicity(
                cat_list,
                correction=tcp.correction,
                alpha=tcp.alpha,
                decision_rule=tcp.decision_rule,
            )
            _ = ALL_CATALOG_TEST_IDS
        self._last_catalog_by_target = out_by_target
        return out_by_target

    def what_if_catalog(
        self,
        catalog_results: Sequence[Any],
        *,
        alpha: float = 0.05,
        correction: str = MultipleTestingCorrection.HOLM.value,
        decision_rule: str = DecisionRule.P_ONLY.value,
        min_effect_size: float | None = None,
    ) -> dict[str, Any]:
        """Re-evaluate ``TestContractResult`` list under new ``alpha``/``correction``/``decision_rule`` with 0 engine calls."""
        from ..tests_catalog import apply_family_multiplicity

        calls_before = self.engine_call_count
        updated = apply_family_multiplicity(
            list(catalog_results),
            correction=correction,
            alpha=alpha,
            decision_rule=decision_rule,
            min_effect_size=min_effect_size,
        )
        delta_calls = self.engine_call_count - calls_before
        if delta_calls != 0:
            raise RuntimeError(f"CACHE-2 violation: what_if_catalog invoked {delta_calls} engine calls.")
        flips = [
            {
                "new_decision": n_r.decision,
                "new_p_adj": n_r.p_adj,
                "old_decision": o_r.decision,
                "old_p_adj": o_r.p_adj,
                "p_raw": n_r.p_raw,
                "test_id": n_r.test_id,
            }
            for o_r, n_r in zip(catalog_results, updated)
            if o_r.decision != n_r.decision
        ]
        return {
            "alpha": alpha,
            "correction": correction,
            "decision_rule": decision_rule,
            "engine_calls_delta": delta_calls,
            "flip_count": len(flips),
            "flipped_decisions": flips,
            "results": updated,
        }

