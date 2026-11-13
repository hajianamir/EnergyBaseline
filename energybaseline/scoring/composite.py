"""13-Criteria Composite Scoring Engine, ±20% Weight Sensitivity & Pairwise DM/MCS Matrix (SC-1..SC-5, SC-8, AC-17, AC-18)."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np

from ..tests_catalog.t10_diebold_mariano import compute_dm_hln_exact, compute_mcs_exact
from ..tests_catalog.t11_nemenyi import compute_friedman_nemenyi_exact
from .tiebreak import deterministic_candidate_sort_key, get_parsimony_tier


CRITERIA_13: tuple[str, ...] = (
    "cvrmse",
    "nmbe",
    "r2",
    "mae_wape",
    "fold_stability",
    "residual_whiteness",
    "homoscedasticity",
    "normality_tails",
    "structural_stability",
    "physical_plausibility",
    "parsimony",
    "interpretability",
    "computational_cost",
)

DEFAULT_WEIGHTS_13: dict[str, float] = {
    "cvrmse": 0.18,
    "nmbe": 0.12,
    "r2": 0.10,
    "mae_wape": 0.08,
    "fold_stability": 0.08,
    "residual_whiteness": 0.08,
    "homoscedasticity": 0.06,
    "normality_tails": 0.05,
    "structural_stability": 0.07,
    "physical_plausibility": 0.06,
    "parsimony": 0.05,
    "interpretability": 0.04,
    "computational_cost": 0.03,
}


def normalize_weights_13(weights: Mapping[str, float] | None = None) -> dict[str, float]:
    """Validate and normalize a 13-criterion weight vector onto the unit simplex."""
    merged = dict(DEFAULT_WEIGHTS_13)
    if weights:
        for k, v in weights.items():
            if k in merged:
                merged[k] = max(0.0, float(v))
    total = sum(merged.values())
    if total <= 1e-12:
        raise ValueError("Composite weight sum must be strictly positive.")
    return {k: merged[k] / total for k in CRITERIA_13}


def _clip01(val: float) -> float:
    if not math.isfinite(val):
        return 0.0
    return float(min(1.0, max(0.0, val)))


def compute_13_subscores(
    *,
    cvrmse: float,
    nmbe: float,
    r2: float,
    wape: float,
    fold_cvrmse_std: float = 0.0,
    ljung_box_p: float = 0.5,
    durbin_watson: float = 2.0,
    breusch_pagan_p: float = 0.5,
    normality_p: float = 0.5,
    cusum_stable: bool = True,
    chow_p: float = 0.5,
    sign_plausibility: float = 1.0,
    n_obs: int = 36,
    n_params: int = 2,
    family: str = "F1",
    is_parametric: bool = True,
    fit_seconds: float = 0.05,
    max_cvrmse: float = 15.0,
    max_nmbe: float = 5.0,
    min_r2: float = 0.75,
) -> dict[str, float]:
    """Compute normalized ``[0, 100]`` sub-scores across all 13 engineering & statistical criteria."""
    s_cv = 100.0 * _clip01(1.0 - float(cvrmse) / max(1e-6, 1.5 * float(max_cvrmse)))
    s_nm = 100.0 * _clip01(1.0 - abs(float(nmbe)) / max(1e-6, 2.0 * float(max_nmbe)))
    if math.isfinite(r2):
        r2_floor = min(0.0, float(min_r2) - 0.5)
        s_r2 = 100.0 * _clip01((float(r2) - r2_floor) / max(1e-6, 1.0 - r2_floor))
    else:
        s_r2 = 0.0
    s_wape = 100.0 * _clip01(1.0 - float(wape) / max(1e-6, 1.5 * float(max_cvrmse)))
    s_fold = 100.0 * _clip01(1.0 - float(fold_cvrmse_std) / max(2.0, 0.5 * float(max_cvrmse)))

    dw_score = _clip01(1.0 - abs(float(durbin_watson) - 2.0) / 1.5)
    lb_score = 1.0 if float(ljung_box_p) >= 0.10 else (0.65 if float(ljung_box_p) >= 0.05 else 0.25 * _clip01(float(ljung_box_p) / 0.05))
    s_white = 100.0 * (0.55 * lb_score + 0.45 * dw_score)

    bp_p = float(breusch_pagan_p) if math.isfinite(breusch_pagan_p) else 0.5
    s_homo = 100.0 * (1.0 if bp_p >= 0.10 else (0.65 if bp_p >= 0.05 else 0.25))

    np_val = float(normality_p) if math.isfinite(normality_p) else 0.5
    s_norm = 100.0 * (1.0 if np_val >= 0.10 else (0.70 if np_val >= 0.05 else 0.40))

    ch_p = float(chow_p) if math.isfinite(chow_p) else 0.5
    stab_base = 0.65 * (1.0 if cusum_stable else 0.25) + 0.35 * (1.0 if ch_p >= 0.05 else 0.30)
    s_struct = 100.0 * _clip01(stab_base)

    s_phys = 100.0 * _clip01(float(sign_plausibility))

    dof_ratio = float(max(1, n_obs)) / float(max(1, n_params))
    s_pars = 100.0 * _clip01(min(1.0, dof_ratio / 20.0))

    tier = get_parsimony_tier(family, is_parametric=is_parametric)
    tier_map = {0: 100.0, 1: 82.0, 2: 55.0, 3: 38.0}
    s_interp = tier_map.get(tier, 50.0)

    ft = max(0.0, float(fit_seconds))
    s_cost = 100.0 * _clip01(1.0 / (1.0 + math.log1p(ft * 2.0) / 3.0))

    return {
        "cvrmse": s_cv,
        "nmbe": s_nm,
        "r2": s_r2,
        "mae_wape": s_wape,
        "fold_stability": s_fold,
        "residual_whiteness": s_white,
        "homoscedasticity": s_homo,
        "normality_tails": s_norm,
        "structural_stability": s_struct,
        "physical_plausibility": s_phys,
        "parsimony": s_pars,
        "interpretability": s_interp,
        "computational_cost": s_cost,
    }


def score_from_subscores(subscores: Mapping[str, float], weights: Mapping[str, float] | None = None) -> float:
    """Aggregate 13 sub-scores into a single ``[0, 100]`` composite score."""
    w = normalize_weights_13(weights)
    return float(sum(w[k] * float(subscores.get(k, 0.0)) for k in CRITERIA_13))


@dataclass(frozen=True)
class CandidateCompositeEvaluation:
    """Complete 13-criteria evaluation for a single model candidate."""

    model_name: str
    family: str
    is_parametric: bool
    qualified: bool
    in_mcs: bool
    cvrmse: float
    nmbe: float
    r2: float
    n_params: int
    subscores_13: dict[str, float]
    composite_score: float
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite_score": round(float(self.composite_score), 4),
            "cvrmse": round(float(self.cvrmse), 4),
            "family": self.family,
            "in_mcs": bool(self.in_mcs),
            "is_parametric": bool(self.is_parametric),
            "model_name": self.model_name,
            "n_params": int(self.n_params),
            "nmbe": round(float(self.nmbe), 4),
            "qualified": bool(self.qualified),
            "r2": round(float(self.r2), 4),
            "rank": int(self.rank),
            "subscores_13": {k: round(float(v), 3) for k, v in self.subscores_13.items()},
        }


@dataclass(frozen=True)
class WeightSensitivityReport:
    """±20% coordinate + Monte-Carlo hypercube weight perturbation stability report (SC-5, AC-18)."""

    perturbation_fraction: float
    n_scenarios: int
    baseline_top1: str
    baseline_top5: tuple[str, ...]
    top1_flip_rate: float
    top5_min_jaccard: float
    rank_stable: bool
    rank_intervals: dict[str, tuple[int, int]]
    criterion_coordinate_flips: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_top1": self.baseline_top1,
            "baseline_top5": list(self.baseline_top5),
            "criterion_coordinate_flips": dict(self.criterion_coordinate_flips),
            "n_scenarios": int(self.n_scenarios),
            "perturbation_fraction": float(self.perturbation_fraction),
            "rank_intervals": {k: [int(lo), int(hi)] for k, (lo, hi) in self.rank_intervals.items()},
            "rank_stable": bool(self.rank_stable),
            "top1_flip_rate": round(float(self.top1_flip_rate), 4),
            "top5_min_jaccard": round(float(self.top5_min_jaccard), 4),
        }


def rank_candidates_13(
    raw_candidates: Sequence[Mapping[str, Any]],
    *,
    weights: Mapping[str, float] | None = None,
    score_tie_tol: float = 0.05,
) -> list[CandidateCompositeEvaluation]:
    """Score and rank candidates deterministically using the 13-criteria rubric + SC-4 tie-breaker."""
    w = normalize_weights_13(weights)
    scored: list[tuple[Mapping[str, Any], float]] = []
    for c in raw_candidates:
        subs = c["subscores_13"]
        s = score_from_subscores(subs, w)
        scored.append((c, s))

    ordered = sorted(
        scored,
        key=lambda item: (
            0 if bool(item[0].get("qualified", True)) else 1,
            *deterministic_candidate_sort_key(
                composite_score=item[1],
                in_mcs=bool(item[0].get("in_mcs", False)),
                family=str(item[0].get("family", "F1")),
                n_params=int(item[0].get("n_params", 1)),
                cvrmse=float(item[0].get("cvrmse", 999.0)),
                model_name=str(item[0]["model_name"]),
                is_parametric=bool(item[0].get("is_parametric", True)),
                score_tie_tol=score_tie_tol,
            ),
        ),
    )

    out: list[CandidateCompositeEvaluation] = []
    for idx, (c, sc) in enumerate(ordered, start=1):
        out.append(
            CandidateCompositeEvaluation(
                model_name=str(c["model_name"]),
                family=str(c.get("family", "F1")),
                is_parametric=bool(c.get("is_parametric", True)),
                qualified=bool(c.get("qualified", True)),
                in_mcs=bool(c.get("in_mcs", False)),
                cvrmse=float(c.get("cvrmse", float("nan"))),
                nmbe=float(c.get("nmbe", float("nan"))),
                r2=float(c.get("r2", float("nan"))),
                n_params=int(c.get("n_params", 1)),
                subscores_13={k: float(c["subscores_13"][k]) for k in CRITERIA_13},
                composite_score=sc,
                rank=idx,
            )
        )
    return out


def analyze_weight_sensitivity_20pct(
    raw_candidates: Sequence[Mapping[str, Any]],
    *,
    base_weights: Mapping[str, float] | None = None,
    perturbation: float = 0.20,
    n_hypercube_draws: int = 64,
    seed: int = 42,
) -> WeightSensitivityReport:
    """Run exact ±20% coordinate perturbations (26 scenarios) + uniform ±20% hypercube draws (SC-5, AC-18)."""
    if not raw_candidates:
        raise ValueError("analyze_weight_sensitivity_20pct requires >= 1 candidate.")

    w0 = normalize_weights_13(base_weights)
    base_ranked = rank_candidates_13(raw_candidates, weights=w0)
    base_top1 = base_ranked[0].model_name
    base_top5 = tuple(r.model_name for r in base_ranked[:5])
    base_top5_set = set(base_top5)

    rank_min = {r.model_name: r.rank for r in base_ranked}
    rank_max = {r.model_name: r.rank for r in base_ranked}
    flips = 0
    min_jacc = 1.0
    coord_flips: dict[str, bool] = {}

    scenarios: list[tuple[str | None, dict[str, float]]] = []
    delta = float(perturbation)
    # 1. Exact +20% and -20% coordinate perturbations for each of the 13 criteria
    for crit in CRITERIA_13:
        for mult in (1.0 - delta, 1.0 + delta):
            w_p = dict(w0)
            w_p[crit] = w0[crit] * mult
            scenarios.append((crit, normalize_weights_13(w_p)))

    # 2. Simultaneous ±20% hypercube perturbations across all 13 criteria
    rng = np.random.default_rng(int(seed))
    for _ in range(max(0, int(n_hypercube_draws))):
        factors = rng.uniform(1.0 - delta, 1.0 + delta, size=len(CRITERIA_13))
        w_mc = {crit: w0[crit] * float(f) for crit, f in zip(CRITERIA_13, factors)}
        scenarios.append((None, normalize_weights_13(w_mc)))

    for crit_tag, w_scen in scenarios:
        rk = rank_candidates_13(raw_candidates, weights=w_scen)
        top1_s = rk[0].model_name
        if top1_s != base_top1:
            flips += 1
            if crit_tag is not None:
                coord_flips[crit_tag] = True
        elif crit_tag is not None and crit_tag not in coord_flips:
            coord_flips[crit_tag] = False

        s_top5 = {r.model_name for r in rk[:5]}
        union_n = len(base_top5_set | s_top5)
        jacc = float(len(base_top5_set & s_top5)) / float(max(1, union_n))
        min_jacc = min(min_jacc, jacc)
        for r in rk:
            rank_min[r.model_name] = min(rank_min[r.model_name], r.rank)
            rank_max[r.model_name] = max(rank_max[r.model_name], r.rank)

    n_scen = max(1, len(scenarios))
    flip_rate = float(flips) / float(n_scen)
    return WeightSensitivityReport(
        perturbation_fraction=delta,
        n_scenarios=len(scenarios),
        baseline_top1=base_top1,
        baseline_top5=base_top5,
        top1_flip_rate=flip_rate,
        top5_min_jaccard=min_jacc,
        rank_stable=bool(flip_rate <= 0.20),
        rank_intervals={k: (rank_min[k], rank_max[k]) for k in rank_min},
        criterion_coordinate_flips=coord_flips,
    )


@dataclass(frozen=True)
class PairwiseModelComparisonMatrix:
    """SC-8 pairwise Diebold-Mariano (T10), Hansen MCS (T13), and Friedman/Nemenyi (T12) comparison matrix."""

    model_names: tuple[str, ...]
    dm_stat_matrix: list[list[float]]
    dm_pvalue_matrix: list[list[float]]
    mcs_superior_set: tuple[str, ...]
    mcs_pvalues: dict[str, float]
    friedman_pvalue: float | None
    mean_fold_ranks: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dm_pvalue_matrix": [[round(float(v), 6) for v in row] for row in self.dm_pvalue_matrix],
            "dm_stat_matrix": [[round(float(v), 6) for v in row] for row in self.dm_stat_matrix],
            "friedman_pvalue": round(float(self.friedman_pvalue), 6) if self.friedman_pvalue is not None else None,
            "mcs_pvalues": {k: round(float(v), 6) for k, v in self.mcs_pvalues.items()},
            "mcs_superior_set": list(self.mcs_superior_set),
            "mean_fold_ranks": {k: round(float(v), 4) for k, v in self.mean_fold_ranks.items()},
            "model_names": list(self.model_names),
        }


def compute_pairwise_comparison_matrix(
    y_true: np.ndarray,
    model_oof_predictions: Mapping[str, np.ndarray],
    *,
    fold_error_matrix: np.ndarray | None = None,
    alpha: float = 0.10,
    seed: int = 42,
) -> PairwiseModelComparisonMatrix:
    """Compute pairwise HLN Diebold-Mariano statistics/p-values + MCS + optional fold Friedman ranks (SC-8)."""
    names = tuple(model_oof_predictions.keys())
    m = len(names)
    if m == 0:
        raise ValueError("compute_pairwise_comparison_matrix requires >= 1 model.")

    yt = np.asarray(y_true, dtype=float).reshape(-1)
    P = np.column_stack([np.asarray(model_oof_predictions[nm], dtype=float).reshape(-1) for nm in names])
    valid = np.isfinite(yt) & np.all(np.isfinite(P), axis=1)
    yt_v = yt[valid]
    P_v = P[valid]
    E_v = yt_v[:, None] - P_v
    L_v = E_v ** 2

    dm_stats = [[0.0 for _ in range(m)] for _ in range(m)]
    dm_pvals = [[1.0 for _ in range(m)] for _ in range(m)]

    if yt_v.size >= 8 and m >= 2:
        for i in range(m):
            for j in range(i + 1, m):
                res = compute_dm_hln_exact(E_v[:, i], E_v[:, j], h=1, power=2)
                st = float(res["hln_statistic"]) if np.isfinite(res.get("hln_statistic", np.nan)) else 0.0
                pv = float(res["p_raw"]) if np.isfinite(res.get("p_raw", np.nan)) else 1.0
                dm_stats[i][j] = st
                dm_stats[j][i] = -st
                dm_pvals[i][j] = pv
                dm_pvals[j][i] = pv

        mcs = compute_mcs_exact(L_v, model_names=list(names), alpha=alpha, n_boot=160, block_length=2, seed=seed)
        sup_set = tuple(str(s) for s in mcs["superior_set"])
        mcs_pv = {str(k): float(v) for k, v in mcs.get("p_values", {}).items()}
    else:
        sup_set = names
        mcs_pv = {nm: 1.0 for nm in names}

    fried_p: float | None = None
    mean_ranks: dict[str, float] = {}
    if fold_error_matrix is not None:
        F_mat = np.asarray(fold_error_matrix, dtype=float)
        if F_mat.ndim == 2 and F_mat.shape[1] == m and F_mat.shape[0] >= 4 and m >= 3:
            fn_res = compute_friedman_nemenyi_exact(F_mat, model_names=list(names), alpha=alpha)
            if np.isfinite(fn_res.get("friedman_p", np.nan)):
                fried_p = float(fn_res["friedman_p"])
            mean_ranks = {str(k): float(v) for k, v in fn_res.get("mean_ranks", {}).items()}

    return PairwiseModelComparisonMatrix(
        model_names=names,
        dm_stat_matrix=dm_stats,
        dm_pvalue_matrix=dm_pvals,
        mcs_superior_set=sup_set,
        mcs_pvalues=mcs_pv,
        friedman_pvalue=fried_p,
        mean_fold_ranks=mean_ranks,
    )
