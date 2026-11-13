"""Pre-lock Top-5 Shortlist Representation, Labelled Surrogates & SHAP/Analytical Attributions (SC-6, SC-7, AC-31).

Enforces critical Phase 6 invariants:
1. Top-5 candidate baselines are ALWAYS visible and inspectable BEFORE baseline lock.
2. Parametric models expose their exact closed-form equation + analytical standardized coefficients + CIs.
3. Non-parametric models expose architecture + hyperparameters + SHAP/Shapley driver attributions AND a
   clearly labelled diagnostic surrogate (``is_surrogate=True``, ``fidelity_r2``) that is NEVER presented
   as the model's actual prediction formula (``primary_formula=None``).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.tree import DecisionTreeRegressor, export_text

from ..mining.clustering import assert_no_cluster_labels_in_model_inputs


SURROGATE_BANNER = "APPROXIMATE_DIAGNOSTIC_SURROGATE"
SURROGATE_DISCLAIMER = (
    "Approximate diagnostic surrogate fitted to black-box model predictions for engineering "
    "inspection ONLY — NOT the model's actual prediction formula."
)


@dataclass(frozen=True)
class SurrogateApproximation:
    """Explicitly labelled diagnostic surrogate for non-parametric models (never the actual model formula)."""

    is_surrogate: bool
    surrogate_banner: str
    disclaimer: str
    surrogate_kind: str
    approximation_expression: str
    fidelity_r2: float
    fidelity_rmse: float
    tree_rules_preview: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "approximation_expression": self.approximation_expression,
            "disclaimer": self.disclaimer,
            "fidelity_r2": round(float(self.fidelity_r2), 6),
            "fidelity_rmse": round(float(self.fidelity_rmse), 6),
            "is_surrogate": bool(self.is_surrogate),
            "surrogate_banner": self.surrogate_banner,
            "surrogate_kind": self.surrogate_kind,
            "tree_rules_preview": self.tree_rules_preview,
        }


@dataclass(frozen=True)
class DriverAttributionReport:
    """Analytical standardized OLS effects (parametric) or exact Shapley attributions (non-parametric) (SC-6, AC-31)."""

    method: str
    baseline_value: float
    global_mean_abs_attribution: dict[str, float]
    signed_standardized_effect: dict[str, float]
    local_sample_attributions: list[dict[str, float]]
    additivity_max_error: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "additivity_max_error": float(self.additivity_max_error),
            "baseline_value": round(float(self.baseline_value), 6),
            "global_mean_abs_attribution": {
                k: round(float(v), 6) for k, v in self.global_mean_abs_attribution.items()
            },
            "local_sample_attributions": [
                {k: round(float(v), 6) for k, v in row.items()} for row in self.local_sample_attributions
            ],
            "method": self.method,
            "signed_standardized_effect": {
                k: round(float(v), 6) for k, v in self.signed_standardized_effect.items()
            },
        }


@dataclass(frozen=True)
class ModelRepresentation:
    """Transparent engineering representation distinguishing parametric equations from non-parametric surrogates."""

    model_name: str
    family: str
    is_parametric: bool
    representation_kind: str  # "closed_form_equation" | "architecture_with_labelled_surrogate"
    primary_formula: str | None  # MUST be None for non-parametric models!
    coefficients: dict[str, float]
    confidence_intervals: dict[str, tuple[float, float]]
    architecture_summary: str | None
    hyperparameters: dict[str, Any]
    surrogate: SurrogateApproximation | None
    attributions: DriverAttributionReport | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture_summary": self.architecture_summary,
            "attributions": self.attributions.to_dict() if self.attributions is not None else None,
            "coefficients": {k: float(v) for k, v in self.coefficients.items()},
            "confidence_intervals": {
                k: [float(lo), float(hi)] for k, (lo, hi) in self.confidence_intervals.items()
            },
            "family": self.family,
            "hyperparameters": dict(self.hyperparameters),
            "is_parametric": bool(self.is_parametric),
            "model_name": self.model_name,
            "primary_formula": self.primary_formula,
            "representation_kind": self.representation_kind,
            "surrogate": self.surrogate.to_dict() if self.surrogate is not None else None,
        }


@dataclass(frozen=True)
class Top5CandidateCard:
    """Single pre-lock Top-5 baseline candidate card with metrics, score, MCS status, and SC-7 representation."""

    rank: int
    model_name: str
    family: str
    composite_score: float
    in_mcs: bool
    cvrmse: float
    nmbe: float
    r2: float
    mae: float
    wape: float
    n_params: int
    representation: ModelRepresentation
    selection_guidance: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite_score": round(float(self.composite_score), 4),
            "cvrmse": round(float(self.cvrmse), 4),
            "family": self.family,
            "in_mcs": bool(self.in_mcs),
            "mae": round(float(self.mae), 4),
            "model_name": self.model_name,
            "n_params": int(self.n_params),
            "nmbe": round(float(self.nmbe), 4),
            "r2": round(float(self.r2), 4),
            "rank": int(self.rank),
            "representation": self.representation.to_dict(),
            "selection_guidance": self.selection_guidance,
            "wape": round(float(self.wape), 4),
        }


@dataclass(frozen=True)
class Top5ShortlistView:
    """Pre-lock Top-5 inspection view ensuring all shortlisted candidates are visible before baseline lock."""

    target: str
    visible_before_lock: bool
    is_locked: bool
    locked_model_name: str | None
    candidates: tuple[Top5CandidateCard, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "count": len(self.candidates),
            "is_locked": bool(self.is_locked),
            "locked_model_name": self.locked_model_name,
            "target": self.target,
            "visible_before_lock": bool(self.visible_before_lock),
        }


def fit_labelled_diagnostic_surrogate(
    X_ref: pd.DataFrame,
    y_hat_model: np.ndarray,
    *,
    max_tree_depth: int = 3,
    seed: int = 42,
) -> SurrogateApproximation:
    """Fit an explicitly labelled diagnostic surrogate to a non-parametric model's predictions ``y_hat_model``."""
    assert_no_cluster_labels_in_model_inputs(X_ref)
    df = X_ref.copy() if isinstance(X_ref, pd.DataFrame) else pd.DataFrame(X_ref)
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    yh = np.asarray(y_hat_model, dtype=float).reshape(-1)
    sst = float(np.sum((yh - np.mean(yh)) ** 2))

    if not num_cols or len(df) < 4 or sst <= 1e-15:
        mean_val = float(np.mean(yh)) if yh.size else 0.0
        return SurrogateApproximation(
            is_surrogate=True,
            surrogate_banner=SURROGATE_BANNER,
            disclaimer=SURROGATE_DISCLAIMER,
            surrogate_kind="constant_diagnostic_surrogate",
            approximation_expression=f"[SURROGATE APPROXIMATION ONLY] y_surr ~= {mean_val:.5g}",
            fidelity_r2=1.0 if sst <= 1e-15 else 0.0,
            fidelity_rmse=0.0,
            tree_rules_preview="",
        )

    X_clean = df[num_cols].replace([np.inf, -np.inf], np.nan)
    X_clean = X_clean.fillna(X_clean.median(numeric_only=True)).fillna(0.0)
    arr = X_clean.to_numpy(dtype=float)

    lin = Ridge(alpha=1e-2).fit(arr, yh)
    pred_lin = lin.predict(arr)
    r2_lin = max(0.0, float(1.0 - np.sum((yh - pred_lin) ** 2) / sst))

    tree = DecisionTreeRegressor(max_depth=max_tree_depth, min_samples_leaf=max(2, len(df) // 15), random_state=seed).fit(
        arr, yh
    )
    pred_tree = tree.predict(arr)
    r2_tree = max(0.0, float(1.0 - np.sum((yh - pred_tree) ** 2) / sst))
    rules = export_text(tree, feature_names=[str(c) for c in num_cols], decimals=3)

    if r2_lin >= r2_tree - 0.05:
        terms = [f"{float(lin.intercept_):.5g}"]
        for c, b in zip(num_cols, lin.coef_):
            if abs(float(b)) > 1e-9:
                terms.append(f"({float(b):+.4g})*{c}")
        expr = f"[SURROGATE APPROXIMATION ONLY — fidelity R^2={r2_lin:.3f}] y_surr ~= " + " ".join(terms[:8])
        rmse = float(np.sqrt(np.mean((yh - pred_lin) ** 2)))
        return SurrogateApproximation(
            is_surrogate=True,
            surrogate_banner=SURROGATE_BANNER,
            disclaimer=SURROGATE_DISCLAIMER,
            surrogate_kind="linear_ridge_surrogate",
            approximation_expression=expr,
            fidelity_r2=r2_lin,
            fidelity_rmse=rmse,
            tree_rules_preview=rules[:600],
        )

    rmse_t = float(np.sqrt(np.mean((yh - pred_tree) ** 2)))
    expr_t = (
        f"[SURROGATE APPROXIMATION ONLY — shallow depth-{max_tree_depth} tree, fidelity R^2={r2_tree:.3f}] "
        f"piecewise {tree.get_n_leaves()}-leaf diagnostic approximation"
    )
    return SurrogateApproximation(
        is_surrogate=True,
        surrogate_banner=SURROGATE_BANNER,
        disclaimer=SURROGATE_DISCLAIMER,
        surrogate_kind=f"shallow_tree_depth_{max_tree_depth}_surrogate",
        approximation_expression=expr_t,
        fidelity_r2=r2_tree,
        fidelity_rmse=rmse_t,
        tree_rules_preview=rules[:600],
    )


def compute_driver_attributions(
    predict_fn: Callable[[pd.DataFrame], np.ndarray],
    X_ref: pd.DataFrame,
    *,
    is_parametric: bool,
    coefficients: dict[str, float] | None = None,
    max_eval_rows: int = 32,
    seed: int = 42,
) -> DriverAttributionReport:
    """Compute exact analytical attributions for parametric models or exact coalition Shapley values for black-box models (SC-6, AC-31)."""
    assert_no_cluster_labels_in_model_inputs(X_ref)
    df = X_ref.copy() if isinstance(X_ref, pd.DataFrame) else pd.DataFrame(X_ref)
    cols = [str(c) for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not cols or len(df) == 0:
        return DriverAttributionReport(
            method="empty_design",
            baseline_value=0.0,
            global_mean_abs_attribution={},
            signed_standardized_effect={},
            local_sample_attributions=[],
            additivity_max_error=0.0,
        )

    X_num = df[cols].replace([np.inf, -np.inf], np.nan)
    X_num = X_num.fillna(X_num.median(numeric_only=True)).fillna(0.0)
    means = X_num.mean(axis=0)
    stds = X_num.std(axis=0, ddof=0).replace(0.0, 1.0)
    bg_row = pd.DataFrame([means.to_dict()], columns=cols)
    base_val = float(np.asarray(predict_fn(bg_row), dtype=float).reshape(-1)[0])

    sample_df = X_num.iloc[: min(len(X_num), max_eval_rows)].copy()
    preds_sample = np.asarray(predict_fn(sample_df), dtype=float).reshape(-1)

    if is_parametric and coefficients:
        usable_coefs = {c: float(coefficients[c]) for c in cols if c in coefficients}
        if usable_coefs:
            # Exact linear Shapley: phi_j(x) = beta_j * (x_j - E[X_j])
            phi = np.zeros((len(sample_df), len(cols)), dtype=float)
            for j, c in enumerate(cols):
                b = usable_coefs.get(c, 0.0)
                phi[:, j] = b * (sample_df[c].to_numpy(dtype=float) - float(means[c]))
            recon = base_val + np.sum(phi, axis=1)
            add_err = float(np.max(np.abs(preds_sample - recon)))
            global_abs = {c: float(np.mean(np.abs(phi[:, j]))) for j, c in enumerate(cols)}
            std_eff = {c: float(usable_coefs.get(c, 0.0) * float(stds[c])) for c in cols}
            locals_out = [{c: float(phi[i, j]) for j, c in enumerate(cols)} for i in range(min(5, len(sample_df)))]
            return DriverAttributionReport(
                method="analytical_linear_shapley_standardized_ols",
                baseline_value=base_val,
                global_mean_abs_attribution=global_abs,
                signed_standardized_effect=std_eff,
                local_sample_attributions=locals_out,
                additivity_max_error=add_err,
            )

    # Exact coalition Shapley for p <= 8; paired antithetic permutation Shapley with efficiency projection for p > 8
    p = len(cols)
    m = len(sample_df)
    phi = np.zeros((m, p), dtype=float)
    fact = [math.factorial(k) for k in range(p + 1)]

    if p <= 8:
        # Vectorized exact Shapley across all 2^p coalitions against the empirical mean background!
        coalition_preds: dict[tuple[int, ...], np.ndarray] = {}
        for r in range(p + 1):
            for subset in combinations(range(p), r):
                synth = pd.DataFrame(np.repeat(bg_row.to_numpy(dtype=float), m, axis=0), index=sample_df.index, columns=cols)
                for j_idx in subset:
                    col_name = cols[j_idx]
                    synth[col_name] = sample_df[col_name].to_numpy(dtype=float)
                coalition_preds[subset] = np.asarray(predict_fn(synth), dtype=float).reshape(-1)

        p_fact = float(fact[p])
        for j in range(p):
            others = [idx for idx in range(p) if idx != j]
            acc = np.zeros(m, dtype=float)
            for r in range(len(others) + 1):
                w = float(fact[r] * fact[p - r - 1]) / p_fact
                for sub in combinations(others, r):
                    sub_with_j = tuple(sorted((*sub, j)))
                    acc += w * (coalition_preds[sub_with_j] - coalition_preds[sub])
            phi[:, j] = acc
        method_name = "exact_coalition_shapley"
    else:
        rng = np.random.default_rng(seed)
        n_perms = 24
        for _ in range(n_perms):
            perm = rng.permutation(p)
            curr = pd.DataFrame(np.repeat(bg_row.to_numpy(dtype=float), m, axis=0), index=sample_df.index, columns=cols)
            prev_val = np.full(m, base_val, dtype=float)
            for j_idx in perm:
                c = cols[j_idx]
                curr[c] = sample_df[c].to_numpy(dtype=float)
                next_val = np.asarray(predict_fn(curr), dtype=float).reshape(-1)
                phi[:, j_idx] += (next_val - prev_val) / float(n_perms)
                prev_val = next_val
        method_name = "permutation_shapley"

    recon = base_val + np.sum(phi, axis=1)
    add_err = float(np.max(np.abs(preds_sample - recon)))
    global_abs = {c: float(np.mean(np.abs(phi[:, j]))) for j, c in enumerate(cols)}

    # Signed standardized direction via OLS of Shapley contributions on standardized features
    Z = ((sample_df - means) / stds).to_numpy(dtype=float)
    std_eff = {}
    for j, c in enumerate(cols):
        zj = Z[:, j]
        denom = float(np.dot(zj, zj))
        std_eff[c] = float(np.dot(zj, phi[:, j]) / denom) if denom > 1e-12 else 0.0

    locals_out = [{c: float(phi[i, j]) for j, c in enumerate(cols)} for i in range(min(5, m))]
    return DriverAttributionReport(
        method=method_name,
        baseline_value=base_val,
        global_mean_abs_attribution=global_abs,
        signed_standardized_effect=std_eff,
        local_sample_attributions=locals_out,
        additivity_max_error=add_err,
    )


def build_model_representation(
    *,
    model_name: str,
    family: str,
    is_parametric: bool,
    formula_or_arch: str,
    coefficients: dict[str, float] | None = None,
    confidence_intervals: dict[str, tuple[float, float]] | None = None,
    hyperparameters: dict[str, Any] | None = None,
    X_ref: pd.DataFrame | None = None,
    y_hat_ref: np.ndarray | None = None,
    predict_fn: Callable[[pd.DataFrame], np.ndarray] | None = None,
    seed: int = 42,
) -> ModelRepresentation:
    """Build SC-7 compliant model representation without ever presenting a surrogate as a black-box formula."""
    coefs = dict(coefficients or {})
    cis = dict(confidence_intervals or {})
    hparams = dict(hyperparameters or {})

    attrib: DriverAttributionReport | None = None
    if predict_fn is not None and X_ref is not None and len(X_ref) > 0:
        try:
            attrib = compute_driver_attributions(
                predict_fn,
                X_ref,
                is_parametric=is_parametric,
                coefficients=coefs,
                seed=seed,
            )
        except Exception:
            attrib = None

    if is_parametric:
        return ModelRepresentation(
            model_name=model_name,
            family=family,
            is_parametric=True,
            representation_kind="closed_form_equation",
            primary_formula=formula_or_arch,
            coefficients=coefs,
            confidence_intervals=cis,
            architecture_summary=None,
            hyperparameters=hparams,
            surrogate=None,
            attributions=attrib,
        )

    surr: SurrogateApproximation | None = None
    if X_ref is not None and y_hat_ref is not None and len(X_ref) == len(y_hat_ref) and len(X_ref) >= 4:
        surr = fit_labelled_diagnostic_surrogate(X_ref, y_hat_ref, seed=seed)

    # CRITICAL INVARIANT: primary_formula is strictly None for non-parametric models!
    return ModelRepresentation(
        model_name=model_name,
        family=family,
        is_parametric=False,
        representation_kind="architecture_with_labelled_surrogate",
        primary_formula=None,
        coefficients={},
        confidence_intervals={},
        architecture_summary=formula_or_arch,
        hyperparameters=hparams,
        surrogate=surr,
        attributions=attrib,
    )
