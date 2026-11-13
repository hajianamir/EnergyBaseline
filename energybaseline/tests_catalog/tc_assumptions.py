"""Unified TestContractResult schema, assumption checks, fallbacks, HAC/naive labelling, and subsampling (TC-1..8, G-5)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
from typing import Any, Final, Mapping, Sequence

import numpy as np

from ..core.cache import canonical_library_versions
from ..core.tcp import DecisionRule, MultipleTestingCorrection, adjust_p_values
from .ci_method import ConfidenceInterval, detect_serial_autocorrelation

library_versions_snapshot = canonical_library_versions

STATUS_NOT_RUN_USER: Final[str] = "not run (user)"
STATUS_NOT_APPLICABLE_NO_GROUP: Final[str] = "not applicable (no group column)"
STATUS_INSUFFICIENT_DATA: Final[str] = "insufficient_data"
STATUS_PASS: Final[str] = "pass"
STATUS_FAIL: Final[str] = "fail"

VALID_DECISIONS: Final[frozenset[str]] = frozenset(
    {
        STATUS_PASS,
        STATUS_FAIL,
        STATUS_NOT_RUN_USER,
        STATUS_NOT_APPLICABLE_NO_GROUP,
        STATUS_INSUFFICIENT_DATA,
    }
)


class TestContractError(ValueError):
    """Raised when a test contract violates Phase 4 schema or statistical invariants."""


class ObservedPowerForbiddenError(TestContractError):
    """Raised if post-hoc 'observed power' is injected as a reported metric or decision input (Gate G4 #3)."""


def _guard_no_observed_power(obj: Any) -> None:
    """Recursively reject retrospective 'observed_power' keys or decision tokens."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            ks = str(k).strip().lower().replace("-", "_").replace("_", "")
            if ks in ("observedpower", "posttestpower", "retrospectivepower") or (
                ks.startswith("post") and ks.endswith("power")
            ):
                raise ObservedPowerForbiddenError(
                    f"Retrospective '{k}' must never be reported or used as a statistical decision input."
                )
            _guard_no_observed_power(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _guard_no_observed_power(item)


@dataclass(frozen=True)
class AssumptionCheck:
    """Single pre-test statistical assumption evaluation and fallback trigger (TC-3, G-5)."""

    name: str
    passed: bool
    metric_name: str
    metric_value: float | str | None
    threshold: float | str | None
    action: str

    def to_dict(self) -> dict[str, Any]:
        val = (
            round(float(self.metric_value), 6)
            if isinstance(self.metric_value, (int, float)) and math.isfinite(float(self.metric_value))
            else self.metric_value
        )
        return {
            "action": self.action,
            "metric_name": self.metric_name,
            "metric_value": val,
            "name": self.name,
            "passed": bool(self.passed),
            "threshold": self.threshold,
        }


def evaluate_contract_decision(
    *,
    p_for_decision: float | None,
    alpha: float,
    decision_rule: str,
    effect_size: float | None,
    min_effect_size: float,
    ci: Mapping[str, Any],
    null_value: float = 0.0,
    reject_means_fail: bool = True,
) -> tuple[str, bool, str]:
    """Evaluate statistical decision strictly honouring ``decision_rule`` and ``min_effect_size``.

    Never ignores ``effect_size`` when ``decision_rule`` is ``p_and_effect_size`` or ``composite_strict``.
    Returns ``(decision, rejected_null, rule_note)``.
    """
    if p_for_decision is None or not math.isfinite(float(p_for_decision)):
        return STATUS_INSUFFICIENT_DATA, False, "p-value unavailable due to insufficient/degenerate data."

    p_val = float(p_for_decision)
    eff = abs(float(effect_size)) if (effect_size is not None and math.isfinite(float(effect_size))) else 0.0
    eff_floor = max(0.0, float(min_effect_size))
    ci_low = ci.get("low")
    ci_high = ci.get("high")
    if ci_low is not None and ci_high is not None and math.isfinite(float(ci_low)) and math.isfinite(float(ci_high)):
        ci_excludes_null = (float(ci_low) > null_value) or (float(ci_high) < null_value)
    else:
        ci_excludes_null = p_val <= alpha

    rule = str(decision_rule or DecisionRule.P_ONLY.value).strip().lower()
    if rule == DecisionRule.P_ONLY.value:
        rejected = p_val <= alpha
        note = f"p={p_val:.4g} {'<=' if rejected else '>'} alpha={alpha:.4g} (p_only)."
    elif rule == DecisionRule.P_AND_EFFECT_SIZE.value:
        if eff_floor <= 0.0:
            raise TestContractError("decision_rule='p_and_effect_size' requires min_effect_size > 0.")
        p_sig = p_val <= alpha
        eff_sig = eff >= eff_floor
        rejected = p_sig and eff_sig
        if p_sig and not eff_sig:
            note = (
                f"p={p_val:.4g} <= alpha={alpha:.4g}, but |effect_size|={eff:.4g} < "
                f"min_effect_size={eff_floor:.4g}; null retained per p_and_effect_size."
            )
        else:
            note = (
                f"p={p_val:.4g} (alpha={alpha:.4g}) and |effect_size|={eff:.4g} "
                f"(floor={eff_floor:.4g}) -> rejected={rejected}."
            )
    elif rule == DecisionRule.CI_EXCLUDES_NULL.value:
        rejected = ci_excludes_null
        note = f"CI [{ci_low}, {ci_high}] excludes_null({null_value})={ci_excludes_null}."
    elif rule == DecisionRule.COMPOSITE_STRICT.value:
        if eff_floor <= 0.0:
            raise TestContractError("decision_rule='composite_strict' requires min_effect_size > 0.")
        rejected = (p_val <= alpha) and (eff >= eff_floor) and ci_excludes_null
        note = (
            f"composite_strict(p={p_val:.4g}<=alpha={alpha:.4g}, |eff|={eff:.4g}>={eff_floor:.4g}, "
            f"ci_excludes_null={ci_excludes_null}) -> rejected={rejected}."
        )
    else:
        raise TestContractError(f"Unsupported decision_rule {decision_rule!r}.")

    if reject_means_fail:
        decision = STATUS_FAIL if rejected else STATUS_PASS
    else:
        decision = STATUS_PASS if rejected else STATUS_FAIL
    return decision, rejected, note


@dataclass(frozen=True)
class TestContractResult:
    """Canonical immutable Phase 4 statistical test contract (`T1..T14`, `TC-1..8`)."""

    test_id: str
    name: str
    statistic: float | None
    p_raw: float | None
    p_adj: float | None
    df: float | tuple[float, ...] | None
    effect_size: float | None
    ci: dict[str, Any]
    n_used: int
    assumptions_checked: tuple[dict[str, Any], ...]
    decision: str
    interpretation: str
    downstream_action: str
    mode: str
    library_versions: dict[str, str] = field(default_factory=canonical_library_versions)
    # Rich metadata fields supporting TCP / reporting / What-If
    subtest: str = ""
    stage: str = "pre_model"
    family: str = "general"
    target: str = ""
    variable: str = ""
    alpha: float = 0.05
    correction: str = "none"
    decision_rule: str = DecisionRule.P_ONLY.value
    min_effect_size: float = 0.0
    null_value: float = 0.0
    reject_means_fail: bool = True
    rejected_null: bool = False
    effect_size_name: str = "standardized_effect"
    assumptions_met: bool = True
    fallback_used: str = ""
    inference_label: str = "standard"
    pass_action: str = "proceed_standard"
    fail_action: str = "flag_in_report"
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision not in VALID_DECISIONS:
            raise TestContractError(f"Invalid decision {self.decision!r} on {self.test_id}; expected {sorted(VALID_DECISIONS)}.")
        if self.mode not in ("fast", "full"):
            raise TestContractError(f"Invalid mode {self.mode!r} on {self.test_id}; expected 'fast' or 'full'.")
        if self.p_raw is not None:
            if not math.isfinite(self.p_raw) or self.p_raw < -1e-12 or self.p_raw > 1.0 + 1e-12:
                raise TestContractError(f"Invalid p_raw={self.p_raw!r} on {self.test_id}.")
        if self.p_adj is not None:
            if not math.isfinite(self.p_adj) or self.p_adj < -1e-12 or self.p_adj > 1.0 + 1e-12:
                raise TestContractError(f"Invalid p_adj={self.p_adj!r} on {self.test_id}.")
        if self.p_raw is not None and self.p_adj is not None and self.correction != "none":
            if self.p_adj + 1e-9 < self.p_raw:
                raise TestContractError(
                    f"Adjusted p_adj ({self.p_adj}) < p_raw ({self.p_raw}) on {self.test_id} under {self.correction}."
                )
        _guard_no_observed_power(self.details)
        _guard_no_observed_power(self.assumptions_checked)
        if "observed power" in self.interpretation.lower() or "observed_power" in self.interpretation.lower():
            raise ObservedPowerForbiddenError("Post-hoc observed power is forbidden in TestContractResult.interpretation.")

    def with_multiplicity_and_policy(
        self,
        *,
        p_adj: float,
        correction: str,
        alpha: float | None = None,
        decision_rule: str | None = None,
        min_effect_size: float | None = None,
    ) -> "TestContractResult":
        """Return a new ``TestContractResult`` with updated ``p_adj`` and policy decision (0 stat recomputation)."""
        if self.decision in (STATUS_NOT_RUN_USER, STATUS_NOT_APPLICABLE_NO_GROUP, STATUS_INSUFFICIENT_DATA):
            return self
        if self.p_raw is None:
            return self
        eff_alpha = self.alpha if alpha is None else float(alpha)
        eff_rule = self.decision_rule if decision_rule is None else str(decision_rule)
        eff_min = self.min_effect_size if min_effect_size is None else float(min_effect_size)
        clipped_adj = min(1.0, max(float(self.p_raw), float(p_adj))) if correction != "none" else float(p_adj)

        # Custom multi-criterion tests (e.g. T1 2x2 ADF/KPSS, T6 VIF ceiling, T14 causal 3-gate) preserve
        # custom decision override only when explicitly locked; otherwise evaluate strictly from p_adj & effect_size.
        if self.details.get("lock_custom_matrix_decision", False) and eff_rule == DecisionRule.P_ONLY.value:
            new_dec, new_rej, rule_note = evaluate_contract_decision(
                p_for_decision=clipped_adj,
                alpha=eff_alpha,
                decision_rule=eff_rule,
                effect_size=self.effect_size,
                min_effect_size=eff_min,
                ci=self.ci,
                null_value=self.null_value,
                reject_means_fail=self.reject_means_fail,
            )
            # Combine matrix non-stationarity with multiplicity-adjusted check if custom matrix failed
            if self.details.get("matrix_force_fail", False):
                new_dec = STATUS_FAIL
                new_rej = True
        else:
            new_dec, new_rej, rule_note = evaluate_contract_decision(
                p_for_decision=clipped_adj,
                alpha=eff_alpha,
                decision_rule=eff_rule,
                effect_size=self.effect_size,
                min_effect_size=eff_min,
                ci=self.ci,
                null_value=self.null_value,
                reject_means_fail=self.reject_means_fail,
            )

        action = self.fail_action if new_dec == STATUS_FAIL else self.pass_action
        updated_details = dict(self.details)
        updated_details["policy_evaluation_note"] = rule_note
        return replace(
            self,
            p_adj=round(clipped_adj, 10),
            correction=correction,
            alpha=eff_alpha,
            decision_rule=eff_rule,
            min_effect_size=eff_min,
            decision=new_dec,
            rejected_null=new_rej,
            downstream_action=action,
            details=updated_details,
        )

    def to_dict(self) -> dict[str, Any]:
        df_out: Any = list(self.df) if isinstance(self.df, tuple) else self.df
        return {
            "alpha": self.alpha,
            "assumptions_checked": list(self.assumptions_checked),
            "assumptions_met": self.assumptions_met,
            "ci": dict(self.ci),
            "correction": self.correction,
            "decision": self.decision,
            "decision_rule": self.decision_rule,
            "details": dict(self.details),
            "df": df_out,
            "downstream_action": self.downstream_action,
            "effect_size": round(float(self.effect_size), 8) if self.effect_size is not None else None,
            "effect_size_name": self.effect_size_name,
            "fallback_used": self.fallback_used,
            "family": self.family,
            "inference_label": self.inference_label,
            "interpretation": self.interpretation,
            "library_versions": dict(self.library_versions),
            "min_effect_size": self.min_effect_size,
            "mode": self.mode,
            "n_used": int(self.n_used),
            "name": self.name,
            "p_adj": round(float(self.p_adj), 10) if self.p_adj is not None else None,
            "p_raw": round(float(self.p_raw), 10) if self.p_raw is not None else None,
            "rejected_null": self.rejected_null,
            "stage": self.stage,
            "statistic": round(float(self.statistic), 8) if self.statistic is not None else None,
            "subtest": self.subtest,
            "target": self.target,
            "test_id": self.test_id,
            "variable": self.variable,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def make_not_run_user_result(
    *,
    test_id: str,
    name: str,
    stage: str = "pre_model",
    family: str = "general",
    target: str = "",
    variable: str = "",
    mode: str = "full",
    confidence_level: float = 0.95,
) -> TestContractResult:
    """Explicitly label a user-disabled test as ``'not run (user)'`` (Gate G4 Criterion #2)."""
    return TestContractResult(
        test_id=test_id,
        name=name,
        subtest="disabled_by_user",
        stage=stage,
        family=family,
        target=target,
        variable=variable,
        statistic=None,
        p_raw=None,
        p_adj=None,
        df=None,
        effect_size=None,
        ci={"confidence_level": confidence_level, "high": None, "low": None, "method": "not_run"},
        n_used=0,
        assumptions_checked=(
            AssumptionCheck(
                name="user_execution_selection",
                passed=False,
                metric_name="enabled",
                metric_value="false",
                threshold="true",
                action=STATUS_NOT_RUN_USER,
            ).to_dict(),
        ),
        assumptions_met=False,
        decision=STATUS_NOT_RUN_USER,
        interpretation=f"{test_id} ({name}) was explicitly disabled by the user and not executed.",
        downstream_action="none (not run by user)",
        mode=mode if mode in ("fast", "full") else "full",
    )


def make_insufficient_data_result(
    *,
    test_id: str,
    name: str,
    n_used: int,
    min_required: int,
    reason: str,
    stage: str = "pre_model",
    family: str = "general",
    target: str = "",
    variable: str = "",
    mode: str = "full",
    confidence_level: float = 0.95,
) -> TestContractResult:
    """Return structured insufficient/degenerate result rather than NaN or exception (TC-2)."""
    return TestContractResult(
        test_id=test_id,
        name=name,
        subtest="precheck_halted",
        stage=stage,
        family=family,
        target=target,
        variable=variable,
        statistic=None,
        p_raw=None,
        p_adj=None,
        df=None,
        effect_size=None,
        ci={"confidence_level": confidence_level, "high": None, "low": None, "method": "insufficient_data"},
        n_used=int(n_used),
        assumptions_checked=(
            AssumptionCheck(
                name="minimum_sample_and_non_degeneracy",
                passed=False,
                metric_name="n_valid",
                metric_value=int(n_used),
                threshold=int(min_required),
                action="halt_test_structured",
            ).to_dict(),
        ),
        assumptions_met=False,
        decision=STATUS_INSUFFICIENT_DATA,
        interpretation=f"{test_id} ({name}) skipped cleanly: {reason} (n={n_used}, required>={min_required}).",
        downstream_action="skip_insufficient_sample",
        mode=mode if mode in ("fast", "full") else "full",
    )


def make_not_applicable_group_result(
    *,
    target: str = "",
    mode: str = "full",
    confidence_level: float = 0.95,
) -> TestContractResult:
    """Structured result for T14 when no ``group`` column is declared in DatasetSchema."""
    return TestContractResult(
        test_id="T14",
        name="Causal M&V Group Balance & Parallel Trends",
        subtest="group_column_absent",
        stage="causal_group",
        family="causal_group",
        target=target,
        variable="",
        statistic=None,
        p_raw=None,
        p_adj=None,
        df=None,
        effect_size=None,
        ci={"confidence_level": confidence_level, "high": None, "low": None, "method": "not_applicable"},
        n_used=0,
        assumptions_checked=(
            AssumptionCheck(
                name="declared_group_column",
                passed=False,
                metric_name="group_column",
                metric_value="none",
                threshold="present",
                action=STATUS_NOT_APPLICABLE_NO_GROUP,
            ).to_dict(),
        ),
        assumptions_met=False,
        decision=STATUS_NOT_APPLICABLE_NO_GROUP,
        interpretation="T14 causal M&V tests require a declared 'group' column (treatment vs control); none present.",
        downstream_action="skip_causal_group_mv",
        mode=mode if mode in ("fast", "full") else "full",
    )


def deterministic_subsample(
    arr: np.ndarray,
    *,
    mode: str = "full",
    max_fast_n: int = 5000,
    preserve_order: bool = True,
    seed: int = 42,
) -> tuple[np.ndarray, dict[str, Any], AssumptionCheck]:
    """Documented deterministic subsampling for large ``n`` in ``fast`` mode (TC-8)."""
    n_orig = int(arr.shape[0])
    m = mode.strip().lower()
    if m == "fast" and n_orig > max_fast_n:
        if preserve_order:
            idx = np.linspace(0, n_orig - 1, num=max_fast_n, dtype=int)
            strategy = "deterministic_uniform_stride_order_preserving"
        else:
            rng = np.random.default_rng(int(seed))
            idx = np.sort(rng.choice(n_orig, size=max_fast_n, replace=False))
            strategy = f"deterministic_seeded_without_replacement(seed={int(seed)})"
        sub = arr[idx]
        info = {
            "applied": True,
            "max_fast_n": int(max_fast_n),
            "original_n": n_orig,
            "strategy": strategy,
            "used_n": int(sub.shape[0]),
        }
        chk = AssumptionCheck(
            name="large_n_subsampling_policy",
            passed=True,
            metric_name="original_n",
            metric_value=n_orig,
            threshold=max_fast_n,
            action=f"subsampled_to_{max_fast_n}_via_{strategy}",
        )
        return sub, info, chk

    info = {
        "applied": False,
        "max_fast_n": int(max_fast_n),
        "original_n": n_orig,
        "strategy": "full_sample_exact",
        "used_n": n_orig,
    }
    chk = AssumptionCheck(
        name="large_n_subsampling_policy",
        passed=True,
        metric_name="original_n",
        metric_value=n_orig,
        threshold=max_fast_n,
        action="full_sample_retained",
    )
    return arr, info, chk


def resolve_autocorrelation_inference(
    series: np.ndarray,
    *,
    allow_hac: bool = True,
    prefer_block_bootstrap: bool = False,
    alpha: float = 0.05,
) -> tuple[str, str, AssumptionCheck, dict[str, Any]]:
    """Evaluate serial independence assumption and return ``(inference_label, fallback_used, check, ac_info)``.

    Guarantees Gate G4 Criterion #4: when autocorrelation is present and HAC/block-bootstrap is
    not applied (``allow_hac=False`` and ``prefer_block_bootstrap=False``), ``inference_label`` is
    strictly ``'naive'``.
    """
    ac = detect_serial_autocorrelation(series, alpha=alpha)
    if not ac["autocorrelated"]:
        chk = AssumptionCheck(
            name="serial_independence",
            passed=True,
            metric_name="lag1_r",
            metric_value=ac["lag1_r"],
            threshold=0.20,
            action="standard_inference",
        )
        return "standard", "", chk, ac

    if prefer_block_bootstrap:
        chk = AssumptionCheck(
            name="serial_independence",
            passed=False,
            metric_name="lag1_r",
            metric_value=ac["lag1_r"],
            threshold=0.20,
            action="switched_to_moving_block_bootstrap",
        )
        return "block_bootstrap", "moving_block_bootstrap", chk, ac

    if allow_hac:
        chk = AssumptionCheck(
            name="serial_independence",
            passed=False,
            metric_name="lag1_r",
            metric_value=ac["lag1_r"],
            threshold=0.20,
            action="switched_to_hac_newey_west",
        )
        return "hac_robust", "hac_newey_west", chk, ac

    chk = AssumptionCheck(
        name="serial_independence",
        passed=False,
        metric_name="lag1_r",
        metric_value=ac["lag1_r"],
        threshold=0.20,
        action="labelled_naive_uncorrected_autocorrelation",
    )
    return "naive", "none_naive_uncorrected", chk, ac


def stationarity_decision_matrix(adf_p: float, kpss_p: float, alpha: float = 0.05) -> dict[str, Any]:
    """2x2 ADF (H0: unit root) x KPSS (H0: stationary) classification matrix (TC-3 / T1)."""
    adf_rejects = float(adf_p) <= float(alpha)
    kpss_rejects = float(kpss_p) <= float(alpha)

    if adf_rejects and not kpss_rejects:
        return {
            "adf_rejects_unit_root": True,
            "cell": "stationary",
            "decision": STATUS_PASS,
            "downstream_action": "proceed_stationary",
            "kpss_rejects_stationarity": False,
            "summary": "ADF rejects unit root and KPSS retains level stationarity -> stationary series.",
        }
    if (not adf_rejects) and kpss_rejects:
        return {
            "adf_rejects_unit_root": False,
            "cell": "unit_root_nonstationary",
            "decision": STATUS_FAIL,
            "downstream_action": "difference_or_cointegrate",
            "kpss_rejects_stationarity": True,
            "summary": "ADF retains unit root and KPSS rejects stationarity -> integrated I(1) non-stationary series.",
        }
    if adf_rejects and kpss_rejects:
        return {
            "adf_rejects_unit_root": True,
            "cell": "trend_stationary_or_structural_shift",
            "decision": STATUS_FAIL,
            "downstream_action": "detrend_or_check_structural_breaks",
            "kpss_rejects_stationarity": True,
            "summary": "Both ADF and KPSS reject -> deterministic trend, fractional integration, or level shift.",
        }
    return {
        "adf_rejects_unit_root": False,
        "cell": "inconclusive_low_power",
        "decision": STATUS_FAIL,
        "downstream_action": "use_robust_differencing_sensitivity",
        "kpss_rejects_stationarity": False,
        "summary": "Neither ADF nor KPSS rejects -> low-power / short-span inconclusive stationarity.",
    }


def apply_family_multiplicity(
    results: Sequence[TestContractResult],
    *,
    correction: str = MultipleTestingCorrection.BENJAMINI_HOCHBERG.value,
    family_by: str = "stage",
    alpha: float | None = None,
    decision_rule: str | None = None,
    min_effect_size: float | None = None,
) -> list[TestContractResult]:
    """Apply family-wise multiple-testing adjustment preserving BOTH ``p_raw`` and ``p_adj`` (TC-4, AC-11)."""
    out = list(results)
    buckets: dict[str, list[int]] = {}
    for idx, res in enumerate(out):
        if res.p_raw is None or res.decision in (
            STATUS_NOT_RUN_USER,
            STATUS_NOT_APPLICABLE_NO_GROUP,
            STATUS_INSUFFICIENT_DATA,
        ):
            continue
        if family_by == "global":
            key = "global"
        elif family_by == "target":
            key = res.target or "default"
        elif family_by == "family":
            key = f"{res.target}:{res.family}"
        else:
            key = f"{res.target}:{res.stage}"
        buckets.setdefault(key, []).append(idx)

    for indices in buckets.values():
        raw_ps = [float(out[i].p_raw) for i in indices if out[i].p_raw is not None]
        adj_rows = adjust_p_values(raw_ps, method=correction)
        for loc_i, adj_info in zip(indices, adj_rows):
            out[loc_i] = out[loc_i].with_multiplicity_and_policy(
                p_adj=float(adj_info["p_adj"]),
                correction=str(adj_info["method"]),
                alpha=alpha,
                decision_rule=decision_rule,
                min_effect_size=min_effect_size,
            )
    return out


def build_ci_dict(ci_obj: ConfidenceInterval) -> dict[str, Any]:
    return ci_obj.to_dict()
