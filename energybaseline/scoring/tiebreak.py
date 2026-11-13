"""Parsimony and physical-interpretability tie-breaking within the Model Confidence Set (SC-4, T10/T13)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


# Tier 0: closed-form physical/linear ASHRAE/IPMVP baselines (preferred under MCS statistical equivalence)
# Tier 1: regularized linear, additive splines/GAM, dynamic ARIMAX, seasonal STL+Fourier
# Tier 2: bagged trees & kernel machines (RF, ExtraTrees, SVR-RBF)
# Tier 3: boosted ensembles, deep neural nets, and stacked meta-learners
FAMILY_PARSIMONY_TIER: dict[str, int] = {
    "F1": 0,
    "F2": 0,
    "Linear & Change-Point": 0,
    "Piecewise / Degree-Day": 0,
    "Mean": 0,
    "Degree-Day": 0,
    "F3": 1,
    "F4": 1,
    "F7": 1,
    "F8": 1,
    "F9": 1,
    "Regularized & Robust Linear": 1,
    "GAM / Spline / Polynomial": 1,
    "Time-Series (ARIMAX / SARIMAX)": 1,
    "Time-Series / Decomposition": 1,
    "Regime-Aware": 1,
    "F5": 2,
    "Tree & Kernel": 2,
    "F6": 3,
    "F10": 3,
    "Neural Network": 3,
    "Ensemble": 3,
}


def get_parsimony_tier(family: str, *, is_parametric: bool | None = None, model_name: str = "") -> int:
    """Return integer interpretability/parsimony tier ``0`` (simplest physical) .. ``3`` (most complex)."""
    if family in FAMILY_PARSIMONY_TIER:
        return FAMILY_PARSIMONY_TIER[family]
    low_name = f"{family} {model_name}".lower()
    if any(tok in low_name for tok in ("1p", "2p", "3p", "4p", "5p", "ols", "mlr", "degree", "piecewise", "towt", "mean")):
        return 0
    if any(tok in low_name for tok in ("ridge", "lasso", "elastic", "huber", "gam", "spline", "sarimax", "arimax", "stl")):
        return 1
    if is_parametric is True:
        return 1
    if any(tok in low_name for tok in ("rf", "forest", "extratrees", "svr")):
        return 2
    return 3


def deterministic_candidate_sort_key(
    *,
    composite_score: float,
    in_mcs: bool,
    family: str,
    n_params: int,
    cvrmse: float,
    model_name: str,
    is_parametric: bool | None = None,
    score_tie_tol: float = 0.25,
) -> tuple[Any, ...]:
    """Deterministic total-order sort key implementing SC-4 parsimony tie-breaking.

    Scores within ``score_tie_tol`` (default 0.25 points on a 0-100 scale) are bucketed together so
    MCS membership, physical interpretability tier, fewer parameters, lower CV(RMSE), and lexical
    model name break near-ties deterministically.
    """
    tol = max(1e-6, float(score_tie_tol))
    score_bucket = round(float(composite_score) / tol)
    tier = get_parsimony_tier(family, is_parametric=is_parametric, model_name=model_name)
    return (
        -score_bucket,
        -int(bool(in_mcs)),
        tier,
        int(n_params),
        round(float(cvrmse), 6),
        -round(float(composite_score), 8),
        str(model_name),
    )


@dataclass(frozen=True)
class ParsimonyTieBreakDecision:
    """Audit record explaining whether SC-4 MCS parsimony tie-breaking promoted a simpler physical model."""

    selected_model: str
    raw_highest_score_model: str
    parsimony_applied: bool
    score_delta: float
    selected_tier: int
    raw_best_tier: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsimony_applied": bool(self.parsimony_applied),
            "raw_best_tier": int(self.raw_best_tier),
            "raw_highest_score_model": self.raw_highest_score_model,
            "reason": self.reason,
            "score_delta": round(float(self.score_delta), 4),
            "selected_model": self.selected_model,
            "selected_tier": int(self.selected_tier),
        }


def select_parsimonious_mcs_leader(
    candidates: Sequence[dict[str, Any]],
    *,
    mcs_score_band: float = 3.5,
) -> ParsimonyTieBreakDecision:
    """Select the recommended baseline favoring simpler physical/linear models inside the MCS (SC-4).

    Parameters
    ----------
    candidates:
        Sequence of dicts with keys ``model_name``, ``family``, ``composite_score``, ``in_mcs``,
        ``n_params``, ``cvrmse``, and optional ``is_parametric``.
    mcs_score_band:
        Maximum composite-score gap (on 0-100 scale) within which an MCS-included physical/linear
        model (tier 0 or 1) is preferred over a higher-scoring black-box model (tier >= 2) when
        both belong to the Hansen Model Confidence Set (statistically indistinguishable OOS loss).
    """
    if not candidates:
        raise ValueError("select_parsimonious_mcs_leader requires >= 1 candidate.")

    ordered_raw = sorted(
        candidates,
        key=lambda c: (
            -round(float(c["composite_score"]), 8),
            get_parsimony_tier(str(c.get("family", "")), is_parametric=c.get("is_parametric"), model_name=str(c["model_name"])),
            int(c.get("n_params", 1)),
            round(float(c.get("cvrmse", 999.0)), 6),
            str(c["model_name"]),
        ),
    )
    top_raw = ordered_raw[0]
    raw_name = str(top_raw["model_name"])
    raw_score = float(top_raw["composite_score"])
    raw_tier = get_parsimony_tier(
        str(top_raw.get("family", "")),
        is_parametric=top_raw.get("is_parametric"),
        model_name=raw_name,
    )

    if raw_tier >= 2 and bool(top_raw.get("in_mcs", False)):
        simpler_in_mcs = [
            c
            for c in ordered_raw
            if bool(c.get("in_mcs", False))
            and get_parsimony_tier(str(c.get("family", "")), is_parametric=c.get("is_parametric"), model_name=str(c["model_name"])) <= 1
            and (raw_score - float(c["composite_score"])) <= float(mcs_score_band)
        ]
        if simpler_in_mcs:
            chosen = sorted(
                simpler_in_mcs,
                key=lambda c: (
                    get_parsimony_tier(str(c.get("family", "")), is_parametric=c.get("is_parametric"), model_name=str(c["model_name"])),
                    int(c.get("n_params", 1)),
                    -round(float(c["composite_score"]), 6),
                    str(c["model_name"]),
                ),
            )[0]
            ch_name = str(chosen["model_name"])
            ch_tier = get_parsimony_tier(
                str(chosen.get("family", "")),
                is_parametric=chosen.get("is_parametric"),
                model_name=ch_name,
            )
            delta = raw_score - float(chosen["composite_score"])
            return ParsimonyTieBreakDecision(
                selected_model=ch_name,
                raw_highest_score_model=raw_name,
                parsimony_applied=True,
                score_delta=delta,
                selected_tier=ch_tier,
                raw_best_tier=raw_tier,
                reason=(
                    f"SC-4 parsimony tie-break promoted interpretable MCS member '{ch_name}' (tier {ch_tier}, "
                    f"p={chosen.get('n_params', 1)}) over complex '{raw_name}' (tier {raw_tier}) within "
                    f"statistically equivalent MCS band (delta={delta:.2f} <= {mcs_score_band:.2f})."
                ),
            )

    return ParsimonyTieBreakDecision(
        selected_model=raw_name,
        raw_highest_score_model=raw_name,
        parsimony_applied=False,
        score_delta=0.0,
        selected_tier=raw_tier,
        raw_best_tier=raw_tier,
        reason=f"Selected '{raw_name}' (tier {raw_tier}, score={raw_score:.2f}) with deterministic tie-break order.",
    )
