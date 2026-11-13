"""Test Configuration Panel (TCP), five multiple-testing corrections, scope matrix, pre-registration lock, and 14-D characterization (TCP-1..14, PIPE-1..3).

Compliance guarantees:
* ``alpha`` is bounded strictly to ``[0.001, 0.20]`` with presets ``(0.001, 0.01, 0.05, 0.10, 0.20)``
  and an explicit warning whenever ``alpha > 0.10`` (TCP-1).
* Confidence supports ``80 / 90 / 95 / 99 / custom`` in ``[50%, 99.9%]`` (TCP-1).
* All five multiple-testing corrections (``none``, ``bonferroni``, ``holm``,
  ``benjamini_hochberg``, ``benjamini_yekutieli``) preserve **both** ``p_raw`` and ``p_adj``
  monotonically in ``[0, 1]`` (TCP-2).
* Guard rails forbid disabling or weakening non-disableable integrity rules and forbid
  silent mutation after pre-registration lock (TCP-4, TCP-5).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Final

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from .auto import AUTO, AutoLog, is_auto
from .integrity import NON_DISABLEABLE_RULES
from .provenance import ProvenanceLedger, ProvenancePhase, ProvenanceSource, normalize_phase
from .schema import ColumnRole
from .vsg import ComplianceGateError, VariableDisposition, VariableSelectionGate

ALPHA_MIN: Final[float] = 0.001
ALPHA_MAX: Final[float] = 0.20
ALPHA_WARN_THRESHOLD: Final[float] = 0.10
ALPHA_PRESETS: Final[tuple[float, ...]] = (0.001, 0.01, 0.05, 0.10, 0.20)
CONFIDENCE_PRESETS: Final[tuple[str, ...]] = ("80", "90", "95", "99", "custom")


class TCPValidationError(ValueError):
    """Raised when Test Configuration Panel parameters violate compliance bounds."""


class TCPGuardRailError(TCPValidationError):
    """Raised when an operation attempts to bypass TCP guard rails or lock immutability."""


class PreregistrationLockedError(TCPGuardRailError):
    """Raised when mutating a locked TCP without explicit audited post-test amendment."""


class MultipleTestingCorrection(str, Enum):
    """Five mandatory multiple-hypothesis testing correction procedures (TCP-2)."""

    NONE = "none"
    BONFERRONI = "bonferroni"
    HOLM = "holm"
    BENJAMINI_HOCHBERG = "benjamini_hochberg"
    BENJAMINI_YEKUTIELI = "benjamini_yekutieli"


class FamilyDefinition(str, Enum):
    """Hypothesis family grouping rules for multiple-testing adjustment (TCP-3)."""

    PER_TARGET = "per_target"
    PER_TEST_FAMILY = "per_test_family"
    PER_VARIABLE = "per_variable"
    GLOBAL = "global"


class DecisionRule(str, Enum):
    """Statistical decision rules applied during evaluation and zero-recompute what-if (TCP-3)."""

    P_ONLY = "p_only"
    P_AND_EFFECT_SIZE = "p_and_effect_size"
    CI_EXCLUDES_NULL = "ci_excludes_null"
    COMPOSITE_STRICT = "composite_strict"


class ExpectedSign(str, Enum):
    """Domain-expected predictor effect sign on energy consumption (TCP-4)."""

    ANY = "any"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    PIECEWISE_V = "piecewise_v"


class GateRole(str, Enum):
    """Role of a statistical test in downstream candidate gating (TCP-4)."""

    HARD_GATE = "hard_gate"
    SOFT_GATE = "soft_gate"
    DIAGNOSTIC_ONLY = "diagnostic_only"


class TestActionPolicy(str, Enum):
    """Downstream action taken when a test triggers (TCP-4)."""

    BLOCK_CANDIDATE = "block_candidate"
    WARN_AND_CONTINUE = "warn_and_continue"
    FALLBACK_ROBUST = "fallback_robust"
    FLAG_IN_REPORT = "flag_in_report"


@dataclass(frozen=True)
class TestCatalogItem:
    """Compile-time metadata for statistical test catalog T1..T14 (TCP-3)."""

    test_id: str
    name: str
    family: str
    stage: str  # pre_model | post_model | causal_group
    per_variable: bool
    default_gate_role: str
    default_action_policy: str
    fast_cost_ms: float
    full_cost_ms: float


TEST_CATALOG_SPECS: MappingProxyType[str, TestCatalogItem] = MappingProxyType(
    {
        "T1": TestCatalogItem("T1", "ADF + KPSS Joint Stationarity", "stationarity_trend", "pre_model", True, GateRole.SOFT_GATE.value, TestActionPolicy.FALLBACK_ROBUST.value, 4.0, 18.0),
        "T2": TestCatalogItem("T2", "Ljung-Box Serial Autocorrelation", "dependence_collinearity", "pre_model", False, GateRole.SOFT_GATE.value, TestActionPolicy.FALLBACK_ROBUST.value, 3.0, 12.0),
        "T3": TestCatalogItem("T3", "Breusch-Pagan & ARCH-LM Heteroscedasticity", "residual_diagnostics", "pre_model", False, GateRole.SOFT_GATE.value, TestActionPolicy.FALLBACK_ROBUST.value, 3.5, 15.0),
        "T4": TestCatalogItem("T4", "Jarque-Bera & Tail Normality", "residual_diagnostics", "pre_model", False, GateRole.DIAGNOSTIC_ONLY.value, TestActionPolicy.FLAG_IN_REPORT.value, 2.0, 8.0),
        "T5": TestCatalogItem("T5", "Chow Known-Date Structural Break", "breaks_regimes", "pre_model", False, GateRole.SOFT_GATE.value, TestActionPolicy.WARN_AND_CONTINUE.value, 3.5, 16.0),
        "T6": TestCatalogItem("T6", "Quandt-Andrews Unknown Breakpoint", "breaks_regimes", "pre_model", False, GateRole.SOFT_GATE.value, TestActionPolicy.WARN_AND_CONTINUE.value, 8.0, 65.0),
        "T7": TestCatalogItem("T7", "Pettitt Non-Parametric Change-Point", "breaks_regimes", "pre_model", False, GateRole.DIAGNOSTIC_ONLY.value, TestActionPolicy.FLAG_IN_REPORT.value, 4.0, 22.0),
        "T8": TestCatalogItem("T8", "VIF & Condition Number Collinearity", "dependence_collinearity", "pre_model", True, GateRole.SOFT_GATE.value, TestActionPolicy.WARN_AND_CONTINUE.value, 2.5, 9.0),
        "T9": TestCatalogItem("T9", "Little MCAR Missingness Mechanism", "missingness_balance", "pre_model", False, GateRole.SOFT_GATE.value, TestActionPolicy.WARN_AND_CONTINUE.value, 5.0, 24.0),
        "T10": TestCatalogItem("T10", "Diebold-Mariano (HLN) OOF Forecast Accuracy", "model_comparison", "post_model", False, GateRole.DIAGNOSTIC_ONLY.value, TestActionPolicy.FLAG_IN_REPORT.value, 3.0, 14.0),
        "T11": TestCatalogItem("T11", "Friedman + Nemenyi Multi-Model Rank", "model_comparison", "post_model", False, GateRole.DIAGNOSTIC_ONLY.value, TestActionPolicy.FLAG_IN_REPORT.value, 3.5, 15.0),
        "T12": TestCatalogItem("T12", "Model Confidence Set (MCS)", "model_comparison", "post_model", False, GateRole.DIAGNOSTIC_ONLY.value, TestActionPolicy.FLAG_IN_REPORT.value, 9.0, 75.0),
        "T13": TestCatalogItem("T13", "Lagged Cross-Correlation & Granger Content", "dependence_collinearity", "pre_model", True, GateRole.DIAGNOSTIC_ONLY.value, TestActionPolicy.FLAG_IN_REPORT.value, 5.0, 28.0),
        "T14": TestCatalogItem("T14", "Causal Group Covariate Balance & Parallel Trends", "missingness_balance", "causal_group", True, GateRole.HARD_GATE.value, TestActionPolicy.WARN_AND_CONTINUE.value, 6.0, 32.0),
    }
)

ALL_TEST_FAMILIES: Final[tuple[str, ...]] = (
    "stationarity_trend",
    "breaks_regimes",
    "dependence_collinearity",
    "residual_diagnostics",
    "missingness_balance",
    "model_comparison",
)


# ===================================================================== Multiple-Testing Corrections (TCP-2)


def _adj_none(p: np.ndarray) -> np.ndarray:
    return p.copy()


def _adj_bonferroni(p: np.ndarray) -> np.ndarray:
    m = float(p.size)
    return np.minimum(1.0, m * p)


def _adj_holm(p: np.ndarray) -> np.ndarray:
    m = p.size
    order = np.argsort(p, kind="mergesort")
    sorted_p = p[order]
    mult = (m - np.arange(m, dtype=float)) * sorted_p
    step_down = np.maximum.accumulate(mult)
    clipped = np.minimum(1.0, step_down)
    out = np.empty_like(p)
    out[order] = clipped
    return out


def _adj_bh(p: np.ndarray) -> np.ndarray:
    m = p.size
    order = np.argsort(p, kind="mergesort")
    sorted_p = p[order]
    ranks = np.arange(1, m + 1, dtype=float)
    scaled = (float(m) / ranks) * sorted_p
    step_up = np.minimum.accumulate(scaled[::-1])[::-1]
    clipped = np.minimum(1.0, step_up)
    out = np.empty_like(p)
    out[order] = clipped
    return out


def _adj_by(p: np.ndarray) -> np.ndarray:
    m = p.size
    c_m = float(np.sum(1.0 / np.arange(1, m + 1, dtype=float)))
    order = np.argsort(p, kind="mergesort")
    sorted_p = p[order]
    ranks = np.arange(1, m + 1, dtype=float)
    scaled = (float(m) * c_m / ranks) * sorted_p
    step_up = np.minimum.accumulate(scaled[::-1])[::-1]
    clipped = np.minimum(1.0, step_up)
    out = np.empty_like(p)
    out[order] = clipped
    return out


_CORRECTION_DISPATCH: MappingProxyType[str, Callable[[np.ndarray], np.ndarray]] = MappingProxyType(
    {
        MultipleTestingCorrection.NONE.value: _adj_none,
        MultipleTestingCorrection.BONFERRONI.value: _adj_bonferroni,
        MultipleTestingCorrection.HOLM.value: _adj_holm,
        MultipleTestingCorrection.BENJAMINI_HOCHBERG.value: _adj_bh,
        MultipleTestingCorrection.BENJAMINI_YEKUTIELI.value: _adj_by,
    }
)


def normalize_correction(method: str | MultipleTestingCorrection) -> str:
    val = method.value if isinstance(method, MultipleTestingCorrection) else str(method or "").strip().lower()
    aliases = {
        "none": MultipleTestingCorrection.NONE.value,
        "unadjusted": MultipleTestingCorrection.NONE.value,
        "bonferroni": MultipleTestingCorrection.BONFERRONI.value,
        "holm": MultipleTestingCorrection.HOLM.value,
        "holm_bonferroni": MultipleTestingCorrection.HOLM.value,
        "bh": MultipleTestingCorrection.BENJAMINI_HOCHBERG.value,
        "fdr_bh": MultipleTestingCorrection.BENJAMINI_HOCHBERG.value,
        "benjamini_hochberg": MultipleTestingCorrection.BENJAMINI_HOCHBERG.value,
        "by": MultipleTestingCorrection.BENJAMINI_YEKUTIELI.value,
        "fdr_by": MultipleTestingCorrection.BENJAMINI_YEKUTIELI.value,
        "benjamini_yekutieli": MultipleTestingCorrection.BENJAMINI_YEKUTIELI.value,
    }
    if val not in aliases:
        raise TCPValidationError(
            f"Unsupported multiple-testing correction {method!r}; "
            f"expected one of {[c.value for c in MultipleTestingCorrection]}."
        )
    return aliases[val]


def adjust_p_values(
    p_raw_values: Sequence[float],
    method: str | MultipleTestingCorrection = MultipleTestingCorrection.BENJAMINI_HOCHBERG,
) -> list[dict[str, Any]]:
    """Compute adjusted p-values under ``method`` and store BOTH ``p_raw`` and ``p_adj`` (TCP-2)."""
    norm_method = normalize_correction(method)
    if len(p_raw_values) == 0:
        return []
    arr = np.asarray(p_raw_values, dtype=float)
    if not np.all(np.isfinite(arr)) or np.any(arr < 0.0) or np.any(arr > 1.0):
        raise TCPValidationError("All raw p-values must be finite numbers in [0.0, 1.0].")
    fn = _CORRECTION_DISPATCH[norm_method]
    adj = np.clip(fn(arr), 0.0, 1.0)
    # Guarantee numerical monotonicity p_adj >= p_raw up to floating-point rounding
    adj = np.maximum(adj, arr)
    m = int(arr.size)
    return [
        {
            "family_size": m,
            "index": i,
            "method": norm_method,
            "p_adj": float(adj[i]),
            "p_raw": float(arr[i]),
        }
        for i in range(m)
    ]


def validate_alpha(alpha: float) -> tuple[float, list[dict[str, str]]]:
    """Validate ``alpha`` in ``[0.001, 0.20]`` and emit elevated-risk warning above ``0.10`` (TCP-1)."""
    try:
        val = float(alpha)
    except (TypeError, ValueError) as exc:
        raise TCPValidationError(f"Significance level alpha must be numeric, got {alpha!r}.") from exc
    if not math.isfinite(val) or val < ALPHA_MIN - 1e-12 or val > ALPHA_MAX + 1e-12:
        raise TCPValidationError(
            f"Significance level alpha={val!r} is outside allowed compliance bounds [{ALPHA_MIN}, {ALPHA_MAX}]."
        )
    val = round(min(ALPHA_MAX, max(ALPHA_MIN, val)), 6)
    warnings: list[dict[str, str]] = []
    if val > ALPHA_WARN_THRESHOLD + 1e-12:
        warnings.append(
            {
                "code": "ALPHA_ABOVE_0_10",
                "message": (
                    f"Selected alpha={val:.3f} exceeds 0.10: elevated Type I false-positive risk must be "
                    "justified in engineering notes."
                ),
                "severity": "warning",
            }
        )
    return val, warnings


def validate_confidence(preset: str, custom_level: float | None = None) -> tuple[str, float]:
    """Normalize confidence preset (``80/90/95/99/custom``) to proportion in ``[0.50, 0.999]`` (TCP-1)."""
    p = str(preset or "").strip().lower().rstrip("%")
    preset_map = {"80": 0.80, "90": 0.90, "95": 0.95, "99": 0.99}
    if p in preset_map:
        return p, preset_map[p]
    if p == "custom" or custom_level is not None:
        raw_val = custom_level if custom_level is not None else preset
        try:
            lv = float(str(raw_val).strip().rstrip("%"))
        except (TypeError, ValueError) as exc:
            raise TCPValidationError(f"Invalid custom confidence level {raw_val!r}.") from exc
        if not math.isfinite(lv):
            raise TCPValidationError("Confidence level must be finite.")
        if lv > 1.0:
            lv = lv / 100.0
        if lv < 0.50 - 1e-12 or lv > 0.999 + 1e-12:
            raise TCPValidationError(
                f"Confidence level {lv:.4f} is outside allowed compliance bounds [0.50, 0.999] (50%..99.9%)."
            )
        return "custom", round(min(0.999, max(0.50, lv)), 6)
    raise TCPValidationError(
        f"Unsupported confidence preset {preset!r}; expected one of {CONFIDENCE_PRESETS}."
    )


@dataclass
class TestPanelConfig:
    """Per-test configuration entry in the Test Control Panel (TCP-3, TCP-4)."""

    test_id: str
    enabled: bool
    mode: str  # "fast" | "full"
    gate_role: str
    action_policy: str
    min_effect_size: float = 0.0
    null_value: float = 0.0
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_policy": self.action_policy,
            "enabled": bool(self.enabled),
            "gate_role": self.gate_role,
            "min_effect_size": float(self.min_effect_size),
            "mode": self.mode,
            "null_value": float(self.null_value),
            "parameters": dict(sorted(self.parameters.items())),
            "test_id": self.test_id,
        }


class TestConfigPanel:
    """Full compliance Test Configuration Panel with guard rails and SHA-256 pre-registration lock (TCP-1..5, PIPE-3)."""

    def __init__(
        self,
        vsg: VariableSelectionGate,
        *,
        config: Mapping[str, Any] | None = None,
        auto_log: AutoLog | None = None,
        provenance: ProvenanceLedger | None = None,
    ) -> None:
        self.vsg: VariableSelectionGate = vsg
        self.config: dict[str, Any] = dict(config or {})
        self.auto_log: AutoLog = auto_log if auto_log is not None else vsg.auto_log
        self.provenance: ProvenanceLedger = provenance if provenance is not None else vsg.provenance

        self._phase: str = ProvenancePhase.PRE_TEST.value
        self._accepted: bool = False
        self._locked: bool = False
        self._preregistration_sha256: str = ""
        self.warnings: list[dict[str, str]] = []

        # Empirical signals from dataset & VSG for AUTO resolutions (never hidden constants)
        n_rows = max((p.n_total for t in vsg.targets for p in vsg.profiles_by_target[t].values()), default=len(vsg._df))
        has_group_col = bool(
            vsg.schema is not None
            and any(spec.role == ColumnRole.GROUP for spec in vsg.schema.columns)
        )
        max_inc_vars = max((len(vsg.included_variables(t)) for t in vsg.targets), default=0)
        est_family_m = max(1, 7 + 3 * max_inc_vars + (max_inc_vars if has_group_col else 0))

        all_abs_r = [
            abs(p.pearson_r)
            for t in vsg.targets
            for c, p in vsg.profiles_by_target[t].items()
            if c in set(vsg.included_variables(t))
        ]
        mean_abs_r = float(np.mean(all_abs_r)) if all_abs_r else 0.0

        # TCP-1: alpha & confidence level
        raw_alpha = self.config.get("tcp_alpha", AUTO)
        if is_auto(raw_alpha):
            resolved_alpha = float(
                self.auto_log.decide(
                    "tcp_alpha",
                    lambda: (
                        (0.01 if n_rows >= 2000 else 0.10 if n_rows < 30 else 0.05),
                        f"n={n_rows}, est_tests={est_family_m}",
                        "alpha=0.01 for large n>=2000 (guards trivial rejection), 0.10 for very small n<30, 0.05 otherwise",
                    ),
                    candidate_space=str(list(ALPHA_PRESETS)),
                    stage="tcp",
                )
            )
        else:
            resolved_alpha = float(raw_alpha)
            self.auto_log.record_override("tcp_alpha", resolved_alpha, stage="tcp")
        self.alpha, alpha_warns = validate_alpha(resolved_alpha)
        self.warnings.extend(alpha_warns)

        raw_conf = self.config.get("tcp_confidence", AUTO)
        if is_auto(raw_conf):
            auto_conf_level = round(min(0.999, max(0.80, 1.0 - self.alpha)), 4)
            self.auto_log.decide(
                "tcp_confidence",
                lambda: (
                    auto_conf_level,
                    f"alpha={self.alpha:.4g}, n={n_rows}",
                    "nominal confidence level matched to 1 - alpha (bounded to [0.80, 0.999])",
                ),
                candidate_space="80|90|95|99|custom",
                stage="tcp",
            )
            pct_str = str(int(round(auto_conf_level * 100)))
            if pct_str in ("80", "90", "95", "99") and abs(auto_conf_level * 100 - int(pct_str)) < 1e-6:
                self.confidence_preset, self.confidence_level = pct_str, auto_conf_level
            else:
                self.confidence_preset, self.confidence_level = "custom", auto_conf_level
        else:
            custom_lv = self.config.get("tcp_confidence_custom")
            self.confidence_preset, self.confidence_level = validate_confidence(str(raw_conf), custom_lv)
            self.auto_log.record_override("tcp_confidence", self.confidence_level, stage="tcp")

        # TCP-2: Multiple-testing correction
        raw_corr = self.config.get("tcp_correction", AUTO)
        if is_auto(raw_corr):
            corr_choice = str(
                self.auto_log.decide(
                    "tcp_correction",
                    lambda: (
                        (
                            MultipleTestingCorrection.NONE.value
                            if est_family_m <= 1
                            else MultipleTestingCorrection.BENJAMINI_YEKUTIELI.value
                            if mean_abs_r >= 0.65
                            else MultipleTestingCorrection.HOLM.value
                            if est_family_m <= 8
                            else MultipleTestingCorrection.BENJAMINI_HOCHBERG.value
                        ),
                        f"est_family_m={est_family_m}, mean_abs_r={mean_abs_r:.3f}",
                        "Holm FWER for small families (m<=8), BY FDR under strong dependence (|r|>=0.65), BH FDR otherwise",
                    ),
                    candidate_space=str([c.value for c in MultipleTestingCorrection]),
                    stage="tcp",
                )
            )
            self.correction: str = normalize_correction(corr_choice)
        else:
            self.correction = normalize_correction(str(raw_corr))
            self.auto_log.record_override("tcp_correction", self.correction, stage="tcp")

        # TCP-3: Family definition & Decision rule
        raw_fam = self.config.get("tcp_family_definition", AUTO)
        if is_auto(raw_fam):
            self.family_definition: str = str(
                self.auto_log.decide(
                    "tcp_family_definition",
                    lambda: (
                        (
                            FamilyDefinition.PER_TEST_FAMILY.value
                            if len(vsg.targets) == 1
                            else FamilyDefinition.PER_TARGET.value
                        ),
                        f"n_targets={len(vsg.targets)}, est_family_m={est_family_m}",
                        "group hypotheses by statistical test family for single target, per-target when multiple EnPIs compete",
                    ),
                    candidate_space=str([f.value for f in FamilyDefinition]),
                    stage="tcp",
                )
            )
        else:
            self.family_definition = self._validate_enum(raw_fam, FamilyDefinition, "family_definition")
            self.auto_log.record_override("tcp_family_definition", self.family_definition, stage="tcp")

        raw_rule = self.config.get("tcp_decision_rule", AUTO)
        if is_auto(raw_rule):
            self.decision_rule: str = str(
                self.auto_log.decide(
                    "tcp_decision_rule",
                    lambda: (
                        (
                            DecisionRule.P_AND_EFFECT_SIZE.value
                            if n_rows >= 500
                            else DecisionRule.P_ONLY.value
                        ),
                        f"n={n_rows}",
                        "require practical effect-size floor alongside p_adj<=alpha when n>=500 to prevent trivial rejections",
                    ),
                    candidate_space=str([d.value for d in DecisionRule]),
                    stage="tcp",
                )
            )
        else:
            self.decision_rule = self._validate_enum(raw_rule, DecisionRule, "decision_rule")
            self.auto_log.record_override("tcp_decision_rule", self.decision_rule, stage="tcp")

        raw_eff = self.config.get("tcp_min_effect_size", AUTO)
        if is_auto(raw_eff):
            eff_floor = round(min(0.20, max(0.02, 1.0 / math.sqrt(max(float(n_rows), 16.0)))), 4)
            self.min_effect_size: float = float(
                self.auto_log.decide(
                    "tcp_min_effect_size",
                    lambda: (
                        eff_floor,
                        f"n={n_rows}, rule={self.decision_rule}",
                        "minimum standardized effect size 1/sqrt(n) clamped to [0.02, 0.20]",
                    ),
                    candidate_space="[0.0, 1.0]",
                    stage="tcp",
                )
            )
        else:
            self.min_effect_size = self._validate_nonneg_float(raw_eff, "min_effect_size")
            self.auto_log.record_override("tcp_min_effect_size", self.min_effect_size, stage="tcp")

        # Per-test panels T1..T14
        raw_mode = self.config.get("tcp_execution_mode", AUTO)
        if is_auto(raw_mode):
            default_mode = str(
                self.auto_log.decide(
                    "tcp_execution_mode",
                    lambda: (
                        ("fast" if n_rows > 10000 else "full"),
                        f"n={n_rows}",
                        "full bootstrap/exact mode for n<=10000, fast asymptotic subsampling mode for n>10000",
                    ),
                    candidate_space="fast|full",
                    stage="tcp",
                )
            )
        else:
            default_mode = str(raw_mode).strip().lower()
            if default_mode not in ("fast", "full"):
                raise TCPValidationError(f"Unsupported tcp_execution_mode {raw_mode!r}; expected 'fast' or 'full'.")
            self.auto_log.record_override("tcp_execution_mode", default_mode, stage="tcp")

        self.test_panels: dict[str, TestPanelConfig] = {}
        for tid, spec in TEST_CATALOG_SPECS.items():
            enabled = has_group_col if spec.stage == "causal_group" else (n_rows >= 12)
            self.test_panels[tid] = TestPanelConfig(
                test_id=tid,
                enabled=enabled,
                mode=default_mode,
                gate_role=spec.default_gate_role,
                action_policy=spec.default_action_policy,
                min_effect_size=self.min_effect_size if self.decision_rule != DecisionRule.P_ONLY.value else 0.0,
                null_value=0.0,
                parameters={"max_lags": min(12, max(1, n_rows // 10))},
            )

        # TCP-4: Expected-sign map per (target, variable)
        self.expected_signs: dict[str, dict[str, str]] = {}
        for t in vsg.targets:
            self.expected_signs[t] = {}
            for col in vsg.profiles_by_target[t]:
                low = col.lower()
                if any(tok in low for tok in ("hdd", "cdd", "prod", "output", "ton", "occup", "flow", "load")):
                    sug = ExpectedSign.POSITIVE.value
                elif any(tok in low for tok in ("temp", "oat")):
                    sug = ExpectedSign.PIECEWISE_V.value
                else:
                    sug = ExpectedSign.ANY.value
                self.expected_signs[t][col] = sug

        # TCP-3: Explicit scope overrides (target, variable, family) -> bool
        self._scope_overrides: dict[tuple[str, str, str], bool] = {}

    @staticmethod
    def _validate_enum(val: Any, enum_cls: type[Enum], field_name: str) -> str:
        s = val.value if isinstance(val, enum_cls) else str(val or "").strip().lower()
        allowed = {e.value for e in enum_cls}
        if s not in allowed:
            raise TCPValidationError(f"Invalid {field_name}={val!r}; expected one of {sorted(allowed)}.")
        return s

    @staticmethod
    def _validate_nonneg_float(val: Any, field_name: str) -> float:
        try:
            f = float(val)
        except (TypeError, ValueError) as exc:
            raise TCPValidationError(f"{field_name} must be numeric, got {val!r}.") from exc
        if not math.isfinite(f) or f < 0.0:
            raise TCPValidationError(f"{field_name} must be a finite non-negative float, got {val!r}.")
        return round(f, 6)

    def _ensure_mutable(self) -> None:
        if self._locked:
            raise PreregistrationLockedError(
                "Test Configuration Panel is pre-registration locked (TCP-5). "
                "Call unlock_for_amendment(reason=...) to record an audited post-test amendment."
            )

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def is_locked(self) -> bool:
        return self._locked

    @property
    def preregistration_sha256(self) -> str:
        return self._preregistration_sha256

    def set_alpha(self, alpha: float, *, reason: str = "Updated significance level alpha") -> float:
        self._ensure_mutable()
        prev = self.alpha
        val, warns = validate_alpha(alpha)
        self.alpha = val
        self.warnings = [w for w in self.warnings if w.get("code") != "ALPHA_ABOVE_0_10"] + warns
        self._accepted = False
        self.auto_log.record_override("tcp_alpha", val, reason=reason, stage="tcp")
        self.provenance.append(
            entity_type="tcp_setting",
            entity_id="alpha",
            source=ProvenanceSource.USER_MODIFICATION,
            phase=self._phase,
            previous_value=prev,
            new_value=val,
            reason=reason,
        )
        return val

    def set_confidence(
        self,
        preset: str,
        custom_level: float | None = None,
        *,
        reason: str = "Updated confidence level",
    ) -> float:
        self._ensure_mutable()
        prev = self.confidence_level
        p_norm, lv = validate_confidence(preset, custom_level)
        self.confidence_preset = p_norm
        self.confidence_level = lv
        self._accepted = False
        self.auto_log.record_override("tcp_confidence", lv, reason=reason, stage="tcp")
        self.provenance.append(
            entity_type="tcp_setting",
            entity_id="confidence_level",
            source=ProvenanceSource.USER_MODIFICATION,
            phase=self._phase,
            previous_value=prev,
            new_value=lv,
            reason=reason,
        )
        return lv

    def set_correction(
        self,
        method: str | MultipleTestingCorrection,
        *,
        reason: str = "Updated multiple-testing correction",
    ) -> str:
        self._ensure_mutable()
        prev = self.correction
        norm = normalize_correction(method)
        self.correction = norm
        self._accepted = False
        self.auto_log.record_override("tcp_correction", norm, reason=reason, stage="tcp")
        self.provenance.append(
            entity_type="tcp_setting",
            entity_id="correction",
            source=ProvenanceSource.USER_MODIFICATION,
            phase=self._phase,
            previous_value=prev,
            new_value=norm,
            reason=reason,
        )
        return norm

    def set_family_definition(
        self,
        family_def: str | FamilyDefinition,
        *,
        reason: str = "Updated hypothesis family definition",
    ) -> str:
        self._ensure_mutable()
        prev = self.family_definition
        norm = self._validate_enum(family_def, FamilyDefinition, "family_definition")
        self.family_definition = norm
        self._accepted = False
        self.auto_log.record_override("tcp_family_definition", norm, reason=reason, stage="tcp")
        self.provenance.append(
            entity_type="tcp_setting",
            entity_id="family_definition",
            source=ProvenanceSource.USER_MODIFICATION,
            phase=self._phase,
            previous_value=prev,
            new_value=norm,
            reason=reason,
        )
        return norm

    def set_decision_rule(
        self,
        rule: str | DecisionRule,
        *,
        min_effect_size: float | None = None,
        reason: str = "Updated statistical decision rule",
    ) -> str:
        self._ensure_mutable()
        prev_rule = self.decision_rule
        norm = self._validate_enum(rule, DecisionRule, "decision_rule")
        if min_effect_size is not None:
            eff = self._validate_nonneg_float(min_effect_size, "min_effect_size")
            if norm in (DecisionRule.P_AND_EFFECT_SIZE.value, DecisionRule.COMPOSITE_STRICT.value) and eff <= 0.0:
                raise TCPGuardRailError(
                    f"Decision rule '{norm}' requires min_effect_size > 0.0 to enforce a practical effect threshold."
                )
            self.min_effect_size = eff
        self.decision_rule = norm
        self._accepted = False
        self.auto_log.record_override("tcp_decision_rule", norm, reason=reason, stage="tcp")
        self.provenance.append(
            entity_type="tcp_setting",
            entity_id="decision_rule",
            source=ProvenanceSource.USER_MODIFICATION,
            phase=self._phase,
            previous_value=prev_rule,
            new_value={"min_effect_size": self.min_effect_size, "rule": norm},
            reason=reason,
        )
        return norm

    def set_expected_sign(
        self,
        target: str,
        variable: str,
        expected_sign: str | ExpectedSign,
        *,
        reason: str = "Updated expected predictor sign",
    ) -> str:
        self._ensure_mutable()
        if target not in self.expected_signs or variable not in self.expected_signs[target]:
            raise TCPValidationError(f"Unknown (target, variable)=({target!r}, {variable!r}) for expected sign.")
        norm = self._validate_enum(expected_sign, ExpectedSign, "expected_sign")
        prev = self.expected_signs[target][variable]
        self.expected_signs[target][variable] = norm
        self._accepted = False
        self.provenance.append(
            entity_type="tcp_expected_sign",
            entity_id=f"{target}:{variable}",
            target=target,
            source=ProvenanceSource.USER_MODIFICATION,
            phase=self._phase,
            previous_value=prev,
            new_value=norm,
            reason=reason,
        )
        return norm

    def configure_test_panel(
        self,
        test_id: str,
        *,
        enabled: bool | None = None,
        mode: str | None = None,
        gate_role: str | GateRole | None = None,
        action_policy: str | TestActionPolicy | None = None,
        min_effect_size: float | None = None,
        parameters: Mapping[str, Any] | None = None,
        reason: str = "Updated per-test panel configuration",
    ) -> TestPanelConfig:
        """Configure a per-test panel or reject attempts to weaken non-disableable integrity rules (TCP-3, TCP-4)."""
        self._ensure_mutable()
        if test_id in NON_DISABLEABLE_RULES:
            raise TCPGuardRailError(
                f"Compliance integrity rule {test_id!r} is non-disableable and cannot be reconfigured via TCP."
            )
        if test_id not in self.test_panels:
            raise TCPValidationError(f"Unknown test_id {test_id!r}; expected one of {sorted(self.test_panels)}.")
        panel = self.test_panels[test_id]
        prev = panel.to_dict()
        new_enabled = panel.enabled if enabled is None else bool(enabled)
        if mode is not None:
            new_mode = mode.strip().lower()
            if new_mode not in ("fast", "full"):
                raise TCPValidationError(f"Invalid test mode {mode!r}; expected 'fast' or 'full'.")
        else:
            new_mode = panel.mode
        new_gate_role = (
            panel.gate_role if gate_role is None else self._validate_enum(gate_role, GateRole, "gate_role")
        )
        new_action_policy = (
            panel.action_policy
            if action_policy is None
            else self._validate_enum(action_policy, TestActionPolicy, "action_policy")
        )
        if new_gate_role == GateRole.HARD_GATE.value and new_action_policy == TestActionPolicy.FLAG_IN_REPORT.value:
            raise TCPGuardRailError(
                f"Guard rail violation on {test_id}: a 'hard_gate' test cannot use passive action_policy='flag_in_report'."
            )
        new_min_eff = (
            panel.min_effect_size
            if min_effect_size is None
            else self._validate_nonneg_float(min_effect_size, "min_effect_size")
        )
        panel.enabled = new_enabled
        panel.mode = new_mode
        panel.gate_role = new_gate_role
        panel.action_policy = new_action_policy
        panel.min_effect_size = new_min_eff
        if parameters is not None:
            panel.parameters.update(dict(parameters))
        self._accepted = False
        self.provenance.append(
            entity_type="tcp_test_panel",
            entity_id=test_id,
            source=ProvenanceSource.USER_MODIFICATION,
            phase=self._phase,
            previous_value=prev,
            new_value=panel.to_dict(),
            reason=reason,
        )
        return panel

    def set_scope_cell(
        self,
        target: str,
        variable: str,
        family: str,
        in_scope: bool,
        *,
        reason: str = "Updated variable x family scope cell",
    ) -> None:
        self._ensure_mutable()
        if target not in self.vsg.decisions_by_target:
            raise TCPValidationError(f"Unknown target {target!r}.")
        if variable not in self.vsg.decisions_by_target[target]:
            raise TCPValidationError(f"Unknown variable {variable!r} for target {target!r}.")
        if family not in ALL_TEST_FAMILIES:
            raise TCPValidationError(f"Unknown test family {family!r}; expected one of {ALL_TEST_FAMILIES}.")
        vsg_disp = self.vsg.decisions_by_target[target][variable].resolved_disposition
        if in_scope and vsg_disp != VariableDisposition.INCLUDE.value:
            raise TCPGuardRailError(
                f"Guard rail violation: cannot enable family '{family}' on '{variable}' because VSG disposition is '{vsg_disp}'."
            )
        prev = self._scope_overrides.get((target, variable, family))
        self._scope_overrides[(target, variable, family)] = bool(in_scope)
        self._accepted = False
        self.provenance.append(
            entity_type="tcp_scope_cell",
            entity_id=f"{target}:{variable}:{family}",
            target=target,
            source=ProvenanceSource.USER_MODIFICATION,
            phase=self._phase,
            previous_value=prev,
            new_value=bool(in_scope),
            reason=reason,
        )

    def build_scope_matrix(self, target: str) -> dict[str, dict[str, dict[str, Any]]]:
        """Return ``variable x test_family`` scope matrix for ``target`` (TCP-3)."""
        if target not in self.vsg.decisions_by_target:
            raise TCPValidationError(f"Unknown target {target!r}.")
        enabled_families = {
            TEST_CATALOG_SPECS[tid].family
            for tid, panel in self.test_panels.items()
            if panel.enabled
        }
        matrix: dict[str, dict[str, dict[str, Any]]] = {}
        for var, dec in self.vsg.decisions_by_target[target].items():
            matrix[var] = {}
            for fam in ALL_TEST_FAMILIES:
                if dec.resolved_disposition != VariableDisposition.INCLUDE.value:
                    matrix[var][fam] = {"in_scope": False, "status": f"excluded_by_vsg:{dec.resolved_disposition}"}
                elif fam not in enabled_families:
                    matrix[var][fam] = {"in_scope": False, "status": "family_disabled"}
                elif (target, var, fam) in self._scope_overrides:
                    flag = self._scope_overrides[(target, var, fam)]
                    matrix[var][fam] = {"in_scope": flag, "status": "user_enabled" if flag else "user_disabled"}
                else:
                    matrix[var][fam] = {"in_scope": True, "status": "in_scope"}
        return matrix

    def clone_for_what_if(
        self,
        *,
        alpha: float | None = None,
        confidence_preset: str | None = None,
        confidence_custom: float | None = None,
        correction: str | MultipleTestingCorrection | None = None,
        family_definition: str | FamilyDefinition | None = None,
        decision_rule: str | DecisionRule | None = None,
        min_effect_size: float | None = None,
        expected_signs: Mapping[str, Mapping[str, str]] | None = None,
        gate_roles: Mapping[str, str] | None = None,
        action_policies: Mapping[str, str] | None = None,
    ) -> "TestConfigPanel":
        """Create a post-hoc/what-if evaluation configuration without mutating the locked TCP."""
        clone = TestConfigPanel(
            self.vsg,
            config=self.config,
            auto_log=AutoLog(self.config, stage="what_if"),
            provenance=ProvenanceLedger(dataset_sha256=self.vsg.dataset_sha256),
        )
        clone.alpha = self.alpha
        clone.confidence_preset = self.confidence_preset
        clone.confidence_level = self.confidence_level
        clone.correction = self.correction
        clone.family_definition = self.family_definition
        clone.decision_rule = self.decision_rule
        clone.min_effect_size = self.min_effect_size
        clone.expected_signs = {t: dict(m) for t, m in self.expected_signs.items()}
        clone.test_panels = {
            tid: TestPanelConfig(
                test_id=p.test_id,
                enabled=p.enabled,
                mode=p.mode,
                gate_role=p.gate_role,
                action_policy=p.action_policy,
                min_effect_size=p.min_effect_size,
                null_value=p.null_value,
                parameters=dict(p.parameters),
            )
            for tid, p in self.test_panels.items()
        }
        clone._scope_overrides = dict(self._scope_overrides)
        if alpha is not None:
            clone.set_alpha(alpha, reason="what-if alpha sensitivity")
        if confidence_preset is not None or confidence_custom is not None:
            clone.set_confidence(confidence_preset or "custom", confidence_custom, reason="what-if confidence")
        if correction is not None:
            clone.set_correction(correction, reason="what-if correction sensitivity")
        if family_definition is not None:
            clone.set_family_definition(family_definition, reason="what-if family definition")
        if decision_rule is not None or min_effect_size is not None:
            clone.set_decision_rule(
                decision_rule or clone.decision_rule,
                min_effect_size=min_effect_size,
                reason="what-if decision rule",
            )
        if expected_signs:
            for t, vmap in expected_signs.items():
                for v, sgn in vmap.items():
                    clone.set_expected_sign(t, v, sgn, reason="what-if expected sign")
        if gate_roles:
            for tid, gr in gate_roles.items():
                clone.configure_test_panel(tid, gate_role=gr, reason="what-if gate role")
        if action_policies:
            for tid, ap in action_policies.items():
                clone.configure_test_panel(tid, action_policy=ap, reason="what-if action policy")
        clone._accepted = True
        return clone

    def accept(self, *, phase: str | ProvenancePhase | None = None) -> str:
        """Accept TCP gate after verifying VSG is accepted (PIPE-1, PIPE-3)."""
        self.vsg.assert_accepted()
        if phase is not None:
            self._phase = normalize_phase(phase)
        self._accepted = True
        self.provenance.record_stage(
            stage_name="tcp_accepted",
            payload=self.canonical_spec_dict(),
            upstream_stage_sha256=self.vsg.vsg_sha256,
            phase=self._phase,
        )
        return self.tcp_sha256

    def is_accepted(self, target: str | None = None) -> bool:
        return bool(self._accepted) and self.vsg.is_accepted(target)

    def assert_accepted(self, target: str | None = None) -> None:
        self.vsg.assert_accepted(target)
        if not self._accepted:
            raise ComplianceGateError(
                "PIPE-1 / PIPE-3 violation: Test Configuration Panel (TCP) is not accepted. "
                "Inferential tests cannot execute before both VSG and TCP gates are accepted."
            )

    def lock_preregistration(self) -> str:
        """Freeze VSG + TCP specification and emit a deterministic SHA-256 pre-registration hash (TCP-5)."""
        if not self._accepted:
            self.accept()
        self.assert_accepted()
        bundle = {
            "dataset_sha256": self.vsg.dataset_sha256,
            "tcp_spec": self.canonical_spec_dict(),
            "vsg_sha256": self.vsg.vsg_sha256,
        }
        raw = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        self._preregistration_sha256 = digest
        self._locked = True
        self.provenance.record_stage(
            stage_name="tcp_preregistration_locked",
            payload={"preregistration_sha256": digest},
            upstream_stage_sha256=self.tcp_sha256,
            phase=self._phase,
        )
        return digest

    def unlock_for_amendment(self, *, reason: str) -> None:
        """Unlock TCP for audited post-test amendment, transitioning phase to ``post_test`` (VSG-6, TCP-5)."""
        if not (reason or "").strip():
            raise TCPValidationError("Unlocking a pre-registered TCP requires a non-empty audit reason.")
        prev_hash = self._preregistration_sha256
        self._locked = False
        self._accepted = False
        self._phase = ProvenancePhase.POST_TEST.value
        self.provenance.append(
            entity_type="tcp_lock",
            entity_id="preregistration",
            source=ProvenanceSource.USER_MODIFICATION,
            phase=ProvenancePhase.POST_TEST,
            previous_value=prev_hash,
            new_value="unlocked_for_amendment",
            reason=reason.strip(),
        )

    def canonical_spec_dict(self) -> dict[str, Any]:
        scope_serializable = {
            f"{t}:{v}:{f}": flag for (t, v, f), flag in sorted(self._scope_overrides.items())
        }
        return {
            "alpha": self.alpha,
            "confidence_level": self.confidence_level,
            "confidence_preset": self.confidence_preset,
            "correction": self.correction,
            "dataset_sha256": self.vsg.dataset_sha256,
            "decision_rule": self.decision_rule,
            "expected_signs": {
                t: dict(sorted(self.expected_signs[t].items())) for t in sorted(self.expected_signs)
            },
            "family_definition": self.family_definition,
            "min_effect_size": self.min_effect_size,
            "phase": self._phase,
            "scope_overrides": scope_serializable,
            "test_panels": {tid: self.test_panels[tid].to_dict() for tid in sorted(self.test_panels)},
            "vsg_sha256": self.vsg.vsg_sha256,
        }

    @property
    def tcp_sha256(self) -> str:
        raw = json.dumps(self.canonical_spec_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        out = self.canonical_spec_dict()
        out["accepted"] = self._accepted
        out["locked"] = self._locked
        out["preregistration_sha256"] = self._preregistration_sha256
        out["scope_matrices"] = {t: self.build_scope_matrix(t) for t in self.vsg.targets}
        out["tcp_sha256"] = self.tcp_sha256
        out["warnings"] = list(self.warnings)
        return out


# ===================================================================== Impact Preview (VSG-3)


def compute_impact_preview(
    vsg: VariableSelectionGate,
    tcp: TestConfigPanel | None = None,
    *,
    n_rows: int | None = None,
) -> dict[str, Any]:
    """Compute variable counts, test counts, family sizes, and Fast vs Full runtime estimates (VSG-3)."""
    eff_tcp = tcp if tcp is not None else TestConfigPanel(vsg)
    rows = int(n_rows if n_rows is not None else len(vsg._df))
    scale = max(1.0, rows / 500.0)

    per_target_vars: dict[str, dict[str, int]] = {}
    family_Buckets: dict[str, int] = {}
    total_tests = 0
    pre_model_tests = 0
    post_model_tests = 0
    causal_tests = 0
    fast_ms = 0.0
    full_ms = 0.0

    for t in vsg.targets:
        decs = vsg.decisions_by_target[t]
        inc = [c for c, d in decs.items() if d.resolved_disposition == VariableDisposition.INCLUDE.value]
        exc = [c for c, d in decs.items() if d.resolved_disposition == VariableDisposition.EXCLUDE.value]
        grp = [c for c, d in decs.items() if d.resolved_disposition == VariableDisposition.COLLINEAR_GROUPED.value]
        per_target_vars[t] = {
            "collinear_grouped": len(grp),
            "excluded": len(exc),
            "included": len(inc),
            "total_candidates": len(decs),
        }
        scope = eff_tcp.build_scope_matrix(t)
        for tid, panel in eff_tcp.test_panels.items():
            if not panel.enabled:
                continue
            spec = TEST_CATALOG_SPECS[tid]
            if spec.per_variable:
                active_vars = [v for v in inc if scope.get(v, {}).get(spec.family, {}).get("in_scope", False)]
                count = len(active_vars)
            else:
                count = 1
            if count <= 0:
                continue
            total_tests += count
            if spec.stage == "pre_model":
                pre_model_tests += count
            elif spec.stage == "post_model":
                post_model_tests += count
            else:
                causal_tests += count

            fast_ms += count * spec.fast_cost_ms * math.sqrt(scale)
            full_ms += count * spec.full_cost_ms * scale

            if eff_tcp.family_definition == FamilyDefinition.GLOBAL.value:
                family_Buckets["global"] = family_Buckets.get("global", 0) + count
            elif eff_tcp.family_definition == FamilyDefinition.PER_TARGET.value:
                family_Buckets[t] = family_Buckets.get(t, 0) + count
            elif eff_tcp.family_definition == FamilyDefinition.PER_TEST_FAMILY.value:
                fk = f"{t}:{spec.family}"
                family_Buckets[fk] = family_Buckets.get(fk, 0) + count
            else:
                if spec.per_variable:
                    for v in inc:
                        if scope.get(v, {}).get(spec.family, {}).get("in_scope", False):
                            vk = f"{t}:{v}"
                            family_Buckets[vk] = family_Buckets.get(vk, 0) + 1
                else:
                    tk = f"{t}:(target_level)"
                    family_Buckets[tk] = family_Buckets.get(tk, 0) + 1

    fam_sizes = sorted(family_Buckets.values())
    fast_sec = round(fast_ms / 1000.0, 3)
    full_sec = round(full_ms / 1000.0, 3)
    return {
        "causal_group_tests": causal_tests,
        "family_count": len(fam_sizes),
        "family_definition": eff_tcp.family_definition,
        "family_sizes": {
            "by_family": dict(sorted(family_Buckets.items())),
            "max": int(max(fam_sizes)) if fam_sizes else 0,
            "median": float(np.median(fam_sizes)) if fam_sizes else 0.0,
            "min": int(min(fam_sizes)) if fam_sizes else 0,
        },
        "fast_vs_full": {
            "fast_estimated_seconds": fast_sec,
            "fast_test_evaluations": total_tests,
            "full_estimated_seconds": full_sec,
            "full_test_evaluations": total_tests,
            "speedup_ratio": round(full_sec / max(fast_sec, 0.001), 2) if total_tests > 0 else 1.0,
        },
        "n_rows": rows,
        "per_target_variables": per_target_vars,
        "post_model_tests": post_model_tests,
        "pre_model_tests": pre_model_tests,
        "total_included_variables": sum(v["included"] for v in per_target_vars.values()),
        "total_tests": total_tests,
    }


# ===================================================================== 14-D Target Characterization Profile
# Supports requirements.yaml TCP-1..14 descriptive pre-modeling profile dimensions cleanly alongside TestConfigPanel.


def compute_target_characterization_profile(
    df: pd.DataFrame,
    *,
    target: str,
    predictors: Sequence[str],
    time_col: str | None = None,
    dataset_sha256: str = "",
) -> dict[str, Any]:
    """Compute deterministic 14-dimension Target Characterization Profile JSON prior to candidate generation."""
    if target not in df.columns:
        raise TCPValidationError(f"Target {target!r} not in DataFrame.")
    y_ser = pd.to_numeric(df[target], errors="coerce")
    n_total = len(df)
    miss_mask = y_ser.isna().to_numpy()
    y = y_ser.dropna().to_numpy(dtype=float)
    n_eff = int(y.size)

    # Span & granularity
    span_days = 0.0
    gran_label = "index"
    if time_col and time_col in df.columns:
        ts = pd.to_datetime(df[time_col], errors="coerce", utc=True).dropna().sort_values()
        if len(ts) >= 2:
            span_days = round(float((ts.iloc[-1] - ts.iloc[0]).total_seconds() / 86400.0), 3)
            med_sec = float(np.median(np.diff(ts.astype("int64").to_numpy()) / 1e9))
            gran_label = (
                "sub_hourly"
                if med_sec < 3600
                else "hourly"
                if med_sec < 7200
                else "daily"
                if med_sec < 172800
                else "weekly"
                if med_sec < 900000
                else "monthly"
            )

    # Gap lengths
    gaps: list[int] = []
    cur_g = 0
    for m in miss_mask:
        if m:
            cur_g += 1
        elif cur_g > 0:
            gaps.append(cur_g)
            cur_g = 0
    if cur_g > 0:
        gaps.append(cur_g)

    mean_y = float(np.mean(y)) if n_eff > 0 else 0.0
    std_y = float(np.std(y, ddof=1)) if n_eff > 1 else 0.0
    skew_y = float(sp_stats.skew(y, bias=False)) if n_eff > 3 and std_y > 1e-12 else 0.0
    kurt_y = float(sp_stats.kurtosis(y, bias=False)) if n_eff > 4 and std_y > 1e-12 else 0.0
    zero_frac = float(np.mean(np.abs(y) <= 1e-12)) if n_eff > 0 else 0.0

    # Robust MAD / IQR outliers
    if n_eff >= 4:
        med_y = float(np.median(y))
        mad = float(np.median(np.abs(y - med_y))) * 1.4826 + 1e-12
        robust_z = np.abs(y - med_y) / mad
        q25, q75 = np.quantile(y, [0.25, 0.75])
        iqr = max(float(q75 - q25), 1e-12)
        iqr_out = int(np.sum((y < q25 - 1.5 * iqr) | (y > q75 + 1.5 * iqr)))
        mad_out = int(np.sum(robust_z > 3.5))
    else:
        iqr_out = mad_out = 0

    # Trend R^2 & lag-1 unit-root persistence
    if n_eff >= 6 and std_y > 1e-12:
        t_idx = np.arange(n_eff, dtype=float)
        r_trend = float(np.corrcoef(t_idx, y)[0, 1])
        r_trend = 0.0 if not math.isfinite(r_trend) else r_trend
        phi1 = float(np.corrcoef(y[:-1], y[1:])[0, 1])
        phi1 = 0.0 if not math.isfinite(phi1) else phi1
    else:
        r_trend = phi1 = 0.0

    # Spectral dominant period & seasonal strength
    dom_period = 0
    seasonal_strength = 0.0
    if n_eff >= 12 and std_y > 1e-12:
        yc = y - mean_y
        fft_mag = np.abs(np.fft.rfft(yc))[1:]
        if fft_mag.size > 0 and float(np.sum(fft_mag ** 2)) > 1e-12:
            k_max = int(np.argmax(fft_mag)) + 1
            dom_period = int(round(n_eff / k_max))
            seasonal_strength = round(float((fft_mag[k_max - 1] ** 2) / np.sum(fft_mag ** 2)), 4)

    # ACF horizon
    acf_lags: dict[str, float] = {}
    max_lag = min(12, max(0, n_eff // 3))
    if std_y > 1e-12:
        yc = y - mean_y
        denom = float(np.dot(yc, yc))
        for lg in range(1, max_lag + 1):
            acf_lags[f"lag_{lg}"] = round(float(np.dot(yc[:-lg], yc[lg:]) / denom), 4)

    # Variance ratio (first half vs second half)
    if n_eff >= 8 and std_y > 1e-12:
        half = n_eff // 2
        v1 = float(np.var(y[:half], ddof=1)) + 1e-12
        v2 = float(np.var(y[half:], ddof=1)) + 1e-12
        var_ratio = round(max(v1, v2) / min(v1, v2), 4)
        mean_shift_z = round(abs(float(np.mean(y[half:]) - np.mean(y[:half]))) / (std_y + 1e-12), 4)
    else:
        var_ratio = 1.0
        mean_shift_z = 0.0

    # Piecewise nonlinearity gain over linear on strongest numeric predictor
    piecewise_gain_r2 = 0.0
    best_lag_corr: dict[str, float] = {}
    num_preds = [
        p
        for p in predictors
        if p in df.columns and (pd.api.types.is_numeric_dtype(df[p]) or pd.api.types.is_bool_dtype(df[p]))
    ]
    for p in num_preds:
        xp = pd.to_numeric(df[p], errors="coerce").to_numpy(dtype=float)
        mask = np.isfinite(xp) & np.isfinite(y_ser.to_numpy(dtype=float))
        if int(mask.sum()) >= 10:
            xm = xp[mask]
            ym = y_ser.to_numpy(dtype=float)[mask]
            if float(np.std(xm)) > 1e-12 and float(np.std(ym)) > 1e-12:
                r_lin = float(np.corrcoef(xm, ym)[0, 1]) ** 2
                knot = float(np.median(xm))
                X_pw = np.column_stack([np.ones_like(xm), np.maximum(0.0, knot - xm), np.maximum(0.0, xm - knot)])
                beta, *_ = np.linalg.lstsq(X_pw, ym, rcond=None)
                yhat = X_pw @ beta
                ss_tot = float(np.sum((ym - np.mean(ym)) ** 2)) + 1e-12
                r_pw = max(0.0, 1.0 - float(np.sum((ym - yhat) ** 2)) / ss_tot)
                piecewise_gain_r2 = max(piecewise_gain_r2, round(r_pw - r_lin, 4))
                best_lag_corr[p] = round(float(np.corrcoef(xm, ym)[0, 1]), 4)

    # Bimodality / operational regime coefficient (Sarle's bimodality index)
    bimodality = (
        round(float((skew_y ** 2 + 1.0) / max(kurt_y + 3.0, 1e-6)), 4)
        if n_eff >= 6 and std_y > 1e-12
        else 0.0
    )

    # Signal-to-noise & normalized histogram entropy
    if n_eff >= 8 and std_y > 1e-12:
        diff_var = float(np.var(np.diff(y), ddof=1)) / 2.0
        tot_var = std_y * std_y
        snr = round(max(0.0, (tot_var - diff_var) / max(diff_var, 1e-12)), 4)
        hist_c, _ = np.histogram(y, bins=min(8, max(2, n_eff // 4)))
        probs = hist_c[hist_c > 0] / float(n_eff)
        norm_entropy = round(float(-np.sum(probs * np.log(probs)) / math.log(max(2, hist_c.size))), 4)
    else:
        snr = 0.0
        norm_entropy = 0.0

    recommended_families = ["linear"]
    if piecewise_gain_r2 >= 0.03 or any("temp" in p.lower() for p in num_preds):
        recommended_families.append("piecewise_degree_day")
    if n_eff >= 60:
        recommended_families.extend(["tree", "kernel"])
    if n_eff >= 300:
        recommended_families.append("nn")

    dims = {
        "dim_01_sample_and_span": {"dominant_granularity": gran_label, "n_effective": n_eff, "n_total": n_total, "span_days": span_days},
        "dim_02_missingness_and_gaps": {"gap_count": len(gaps), "max_gap": max(gaps, default=0), "missing_fraction": round(float(miss_mask.mean()) if n_total else 0.0, 4)},
        "dim_03_distribution_shape": {"kurtosis": round(kurt_y, 4), "positivity_min": round(float(np.min(y)) if n_eff else 0.0, 4), "skewness": round(skew_y, 4), "zero_fraction": round(zero_frac, 4)},
        "dim_04_outlier_prevalence": {"iqr_outliers": iqr_out, "mad_outliers": mad_out, "outlier_rate": round(mad_out / max(1, n_eff), 4)},
        "dim_05_trend_and_persistence": {"lag1_persistence": round(phi1, 4), "linear_trend_r2": round(r_trend ** 2, 4)},
        "dim_06_seasonality": {"dominant_period_steps": dom_period, "spectral_seasonal_strength": seasonal_strength},
        "dim_07_autocorrelation_memory": {"acf_by_lag": acf_lags, "memory_horizon_lags": sum(1 for v in acf_lags.values() if abs(v) >= 2.0 / math.sqrt(max(n_eff, 4)))},
        "dim_08_variance_nonstationarity": {"half_sample_variance_ratio": var_ratio, "heteroscedastic_flag": var_ratio > 2.25},
        "dim_09_structural_break_signal": {"half_sample_mean_shift_z": mean_shift_z, "regime_shift_flag": mean_shift_z > 1.0},
        "dim_10_driver_nonlinearity": {"piecewise_r2_gain": piecewise_gain_r2, "piecewise_recommended": piecewise_gain_r2 >= 0.03},
        "dim_11_exogenous_drivers": {"contemporaneous_r": best_lag_corr, "numeric_driver_count": len(num_preds)},
        "dim_12_operational_modes": {"bimodality_index": bimodality, "multimodal_flag": bimodality > 0.555},
        "dim_13_snr_and_forecastability": {"normalized_entropy": norm_entropy, "signal_to_noise_ratio": snr},
        "dim_14_gating_manifest": {
            "cv_strategy": "blocked_kfold" if span_days < 730.0 else "forward_chaining",
            "dataset_sha256": dataset_sha256,
            "recommended_families": recommended_families,
            "target": target,
        },
    }
    profile_sha = hashlib.sha256(
        json.dumps(dims, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return {
        "dataset_sha256": dataset_sha256,
        "dimensions": dims,
        "profile_sha256": profile_sha,
        "target": target,
    }
