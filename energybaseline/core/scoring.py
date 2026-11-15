"""Transparent multi-criteria scoring, acceptance thresholds, Top-5 shortlist and rejection register."""
from __future__ import annotations

import numpy as np

from .auto import AutoLog, is_auto
from .domain import resolve_domain
from .temporal import Granularity

WEIGHTS = {
    "balanced": dict(cvrmse=.18, r2=.08, nmbe=.06, mae=.05, smape=.03, robustness=.10, parsimony=.08, diagnostics=.08,
                     plausibility=.10, interpretability=.10, ashrae=.06, uncertainty=.05, data_sufficiency=.03),
    "accuracy_first": dict(cvrmse=.28, r2=.12, nmbe=.08, mae=.08, smape=.04, robustness=.10, parsimony=.03, diagnostics=.05,
                           plausibility=.08, interpretability=.03, ashrae=.05, uncertainty=.04, data_sufficiency=.02),
    "interpretable": dict(cvrmse=.14, r2=.06, nmbe=.05, mae=.04, smape=.02, robustness=.08, parsimony=.12, diagnostics=.08,
                          plausibility=.12, interpretability=.20, ashrae=.05, uncertainty=.02, data_sufficiency=.02),
}
CRITERIA_TEXT = {
    "cvrmse": "CV(RMSE) on out-of-fold data (lower is better)", "r2": "R² on out-of-fold data", "nmbe": "|NMBE| (bias)",
    "mae": "Mean absolute error", "smape": "sMAPE", "robustness": "Stability of CV(RMSE) across folds",
    "parsimony": "Parameter count and BIC rank", "diagnostics": "Residual tests passed (Jarque–Bera, Durbin–Watson, Ljung–Box, Breusch–Pagan)",
    "plausibility": "Physical plausibility (sign/monotonicity, non-negativity)", "interpretability": "Interpretability tier (1 equation … 5 black-box)",
    "ashrae": "ASHRAE Guideline 14 / IPMVP thresholds met", "uncertainty": "Width of the 95 % residual interval relative to mean load",
    "data_sufficiency": "Data-sufficiency penalty for high-capacity models (parameters vs observations)",
}


def thresholds(g: Granularity, auto: AutoLog) -> dict:
    policy = resolve_domain(auto.config)
    if policy.key != "legacy_energy":
        # Only recognized energy calibration intervals receive built-in limits.
        # Daily/weekly are NOT represented as ASHRAE standard thresholds.
        energy_limits = (15.0, 5.0, 0.75) if g.tier == "monthly" and g.freq.upper() in ("M", "MS", "ME") else ((30.0, 10.0, 0.5) if g.seconds == 3600 else (None, None, None))
        values = energy_limits if policy.energy else (None, None, None)
        out = {}
        for key, default in zip(("max_cvrmse", "max_nmbe", "min_r2"), values):
            raw = auto.config.get(key)
            if is_auto(raw):
                out[key] = default
                auto.note(key, default, f"domain={policy.key}, granularity={g.label}",
                          "No universal acceptance threshold; only the selected energy policy supplies recognized calibration limits.")
            else:
                value = float(auto.decide(key, lambda: (default, f"domain={policy.key}", "explicit acceptance override")))
                if not np.isfinite(value) or (key != "min_r2" and value <= 0) or (key == "min_r2" and value > 1):
                    raise ValueError(f"Invalid acceptance limit: {key}.")
                out[key] = value
        return out
    def _cv():
        if g.tier in ("subdaily", "daily"):
            return 30.0, f"granularity={g.label}", "ASHRAE Guideline 14: CV(RMSE) <= 30 % for hourly/daily calibration"
        if g.tier == "weekly":
            return 20.0, f"granularity={g.label}", "interpolated between hourly (30 %) and monthly (15 %) limits"
        return 15.0, f"granularity={g.label}", "ASHRAE Guideline 14: CV(RMSE) <= 15 % for monthly calibration"

    def _nm():
        if g.tier in ("subdaily", "daily"):
            return 10.0, f"granularity={g.label}", "ASHRAE Guideline 14: |NMBE| <= 10 % for hourly/daily"
        if g.tier == "weekly":
            return 7.0, f"granularity={g.label}", "interpolated between 10 % and 5 %"
        return 5.0, f"granularity={g.label}", "ASHRAE Guideline 14: |NMBE| <= 5 % for monthly"

    def _r2():
        v = 0.75 if g.tier == "monthly" else 0.5
        return v, f"granularity={g.label}", "IPMVP guidance R² >= 0.75 for monthly regressions; 0.5 for finer data"

    return {"max_cvrmse": float(auto.decide("max_cvrmse", _cv)), "max_nmbe": float(auto.decide("max_nmbe", _nm)),
            "min_r2": float(auto.decide("min_r2", _r2))}


def _ratio_to_best(vals: np.ndarray, higher_better=False) -> np.ndarray:
    v = vals.astype(float)
    ok = np.isfinite(v)
    out = np.zeros(len(v))
    if not ok.any():
        return out
    if higher_better:
        best = np.max(v[ok])
        out[ok] = np.clip(v[ok], 0, None) / best if best > 0 else 0
    else:
        best = np.nanmin(v[ok])
        out[ok] = np.where(v[ok] > 0, best / np.maximum(v[ok], 1e-12), 1.0) if best > 0 else 1.0 / (1.0 + v[ok])
    return np.clip(out, 0, 1)


def ranking_key(result: dict, *, score: float | None = None) -> tuple:
    """Shared score/name ordering, with numerical ties at 1e-10 score points."""
    value = float(result["score"] if score is None else score)
    return (-round(value, 10) if np.isfinite(value) else float("inf"), result["name"])


def score_models(results: list[dict], n_obs: int, th: dict, auto: AutoLog) -> dict:
    """Mutates ``results`` adding criteria scores, total, rank, pass flags. Returns profile info."""
    ok = [r for r in results if r.get("status") == "ok"]

    def _wp():
        if n_obs < 200:
            return "interpretable", f"n={n_obs} < 200", "small samples: favour simple, auditable equations"
        return "balanced", f"n={n_obs}", "default balanced weighting of accuracy, robustness, physics and transparency"
    prof = auto.decide("weight_profile", _wp)
    w = dict(WEIGHTS[prof])
    policy = resolve_domain(auto.config)
    if ok and all(r["diagnostics"].get("inference_status") for r in ok):
        w["diagnostics"] = 0.0
    if not policy.energy:
        w["ashrae"] = w["plausibility"] = 0.0
    if th.get("max_cvrmse") is None or th.get("max_nmbe") is None:
        w["ashrae"] = 0.0
    w = {key: value / sum(w.values()) for key, value in w.items()}
    if not ok:
        return {"schema_version": 1, "profile": prof, "weights": w, "domain_policy": policy.to_dict(), "acceptance_evaluated": any(value is not None for value in th.values())}
    M = lambda k: np.array([r["metrics"][k] for r in ok], float)  # noqa: E731
    crit: dict[str, np.ndarray] = {
        "cvrmse": _ratio_to_best(M("cvrmse")), "r2": _ratio_to_best(M("r2"), True),
        "nmbe": (np.clip(1 - np.abs(M("nmbe")) / max(th["max_nmbe"], 1e-9), 0, 1) if th["max_nmbe"] is not None else _ratio_to_best(np.abs(M("nmbe")))), "mae": _ratio_to_best(M("mae")),
        "smape": _ratio_to_best(M("smape")),
        "robustness": np.clip(1 - np.array([r["robustness"] for r in ok]), 0, 1),
    }
    k = np.array([r["n_params"] for r in ok], float)
    bic = np.array([r["bic"] for r in ok], float)
    brank = bic.argsort().argsort()
    crit["parsimony"] = 0.5 * (1 / (1 + np.log10(1 + k) / 2)) + 0.5 * (1 - brank / max(1, len(ok) - 1))
    crit["diagnostics"] = np.array([r["diagnostics"]["tests_passed"] / 4 for r in ok])
    crit["plausibility"] = np.array([r["plausibility"] for r in ok])
    crit["interpretability"] = np.array([1.0 - 0.2 * (r["tier"] - 1) for r in ok])
    crit["ashrae"] = np.array([0.5 * (th["max_cvrmse"] is not None and r["metrics"]["cvrmse"] <= th["max_cvrmse"]) + 0.5 * (th["max_nmbe"] is not None and abs(r["metrics"]["nmbe"]) <= th["max_nmbe"]) for r in ok])
    ybar = np.array([abs(r["mean_y"]) or 1.0 for r in ok])
    hw = np.array([(r["resid_q"][1] - r["resid_q"][0]) / 2 for r in ok]) / ybar
    crit["uncertainty"] = _ratio_to_best(hw)
    ds = []
    for r in ok:
        cap = 1.0
        if r["kind"] in ("nn",):
            cap = min(1.0, n_obs / (20.0 * max(r["n_params"], 1)))
        elif r["kind"] in ("tree", "kernel"):
            cap = min(1.0, n_obs / 50.0)
        ds.append(max(0.0, cap))
    crit["data_sufficiency"] = np.array(ds)
    crit = {key: np.clip(np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0), 0, 1) for key, values in crit.items()}
    for i, r in enumerate(ok):
        r["criteria"] = {c: float(v[i]) for c, v in crit.items()}
        r["criteria_weights"] = dict(w)
        r["score"] = float(100 * sum(w[c] * r["criteria"][c] for c in w))
        m = r["metrics"]
        fails = []
        for metric in ("cvrmse", "nmbe", "mae", "smape"):
            if not np.isfinite(m[metric]):
                fails.append(f"Non-finite validation metric: {metric}")
        if th["min_r2"] is not None and not np.isfinite(m["r2"]):
            fails.append("R2 acceptance cannot be evaluated: validation metric is undefined.")
        if th["max_cvrmse"] is not None and m["cvrmse"] > th["max_cvrmse"]:
            fails.append(f"CV(RMSE) {m['cvrmse']:.1f} % > {th['max_cvrmse']:.0f} %")
        if th["max_nmbe"] is not None and abs(m["nmbe"]) > th["max_nmbe"]:
            fails.append(f"|NMBE| {abs(m['nmbe']):.1f} % > {th['max_nmbe']:.0f} %")
        if th["min_r2"] is not None and m["r2"] == m["r2"] and m["r2"] < th["min_r2"]:
            fails.append(f"R² {m['r2']:.2f} < {th['min_r2']:.2f}")
        if policy.energy and r["plausibility"] < 0.5:
            fails.append("physically implausible response (" + "; ".join(r["plaus_notes"]) + ")")
        r["threshold_failures"] = fails
        r["qualified"] = not fails
        r["acceptance_evaluated"] = any(value is not None for value in th.values())
        r["acceptance_status"] = ("passed" if not fails else "failed") if r["acceptance_evaluated"] else "not_assessed"
        r["domain_policy"] = policy.key
    for i, r in enumerate(sorted(ok, key=ranking_key), 1):
        r["rank"] = i
    return {"schema_version": 1, "profile": prof, "weights": w, "domain_policy": policy.to_dict(), "acceptance_evaluated": any(value is not None for value in th.values())}


def nn_gate(results: list[dict], n_obs: int, auto: AutoLog) -> None:
    """Explicit NN rejection when data is insufficient or no improvement over classical models."""
    ok_pre = [r for r in results if r.get("status") == "ok" and r["kind"] not in ("nn", "blend")]
    best_classical_cv = min((r["metrics"]["cvrmse"] for r in ok_pre), default=0.0)
    min_gain = float(
        auto.decide(
            "nn_min_gain",
            lambda: (
                (7.5 if n_obs < 500 else 5.0 if n_obs < 2500 else 3.0),
                f"n_obs={n_obs}, best_classical_cvrmse={best_classical_cv:.2f}%",
                "required relative CV(RMSE) gain over classical models scales inversely with sample size (7.5% n<500, 5% n<2500, 3% n>=2500)",
            ),
        )
    )
    ok = [r for r in results if r.get("status") == "ok"]
    classical = [r for r in ok if r["kind"] != "nn" and r["kind"] != "blend"]
    if not classical:
        return
    best_c = min(r["metrics"]["cvrmse"] for r in classical)
    for r in ok:
        if r["kind"] != "nn":
            continue
        gain = 100 * (best_c - r["metrics"]["cvrmse"]) / best_c if best_c > 0 else 0.0
        r["nn_gain_pct"] = gain
        if gain < min_gain:
            r["threshold_failures"].append(f"NN rejected: CV(RMSE) gain {gain:.1f} % vs best classical model < required {min_gain:.0f} % (no meaningful improvement)")
            r["qualified"] = False
        if n_obs < r["n_params"]:
            r["threshold_failures"].append(f"data insufficient: {r['n_params']} parameters exceed n={n_obs} observations")
            r["qualified"] = False


def build_shortlist(results: list[dict], top_k: int = 5) -> dict:
    ok = sorted([r for r in results if r.get("status") == "ok"], key=ranking_key)
    qualified = [r for r in ok if r["qualified"]]
    notice = ""
    if len(qualified) >= top_k:
        top = qualified[:top_k]
    elif qualified:
        top = qualified
        notice = f"Only {len(qualified)} model(s) meet the minimum acceptance thresholds; the shortlist contains the available ones."
    elif ok:
        top = ok[:1]
        notice = ("LIMITATIONS WARNING: no model meets the configured acceptance thresholds. The best available model is offered "
                  "for information only - treat baseline deviations with caution.")
    else:
        top = []
        notice = "No model could be fitted for this target."
    if top and all(r.get("acceptance_evaluated") is False for r in top):
        notice = "No domain acceptance limits are configured. This shortlist ranks predictive candidates; standards compliance has not been assessed."
    names = {r["name"] for r in top}
    qual_not_shortlisted: list[dict] = []
    for r in ok:
        if r["name"] in names:
            r["status_final"] = "shortlisted"
            r["reason"] = rationale(r, top)
        elif r["qualified"]:
            # CRITICAL Phase 6 invariant: qualified models outside Top-5 are NEVER commingled with rejected models!
            r["status_final"] = "qualified_not_shortlisted"
            cut = top[-1] if top else None
            r["reason"] = (
                f"Eligible under the selected acceptance policy at rank #{r['rank']} (score {r['score']:.1f}); "
                f"outside Top-{top_k} shortlist cut-off score {cut['score']:.1f} ({cut['name']})."
                if cut
                else "Qualified."
            )
            qual_not_shortlisted.append(r)
        else:
            r["status_final"] = "rejected"
            r["reason"] = (
                "Failed acceptance: " + "; ".join(r["threshold_failures"])
                if r["threshold_failures"]
                else "Failed baseline qualification thresholds."
            )
    for r in results:
        if r.get("status") != "ok":
            r["status_final"] = "rejected"
            r["reason"] = r.get("error", "not available")
    return {
        "top": top,
        "qualified_not_shortlisted": qual_not_shortlisted,
        "notice": notice,
        "n_qualified": len(qualified),
    }


def rationale(r: dict, top: list[dict]) -> str:
    c = {k: v for k, v in r["criteria"].items() if r.get("criteria_weights", {}).get(k, 1) > 0}
    best = sorted(c.items(), key=lambda kv: -kv[1])[:3]
    weak = sorted(c.items(), key=lambda kv: kv[1])[:1]
    m = r["metrics"]
    return (f"Rank #{r['rank']} with score {r['score']:.1f}/100; CV(RMSE)={m['cvrmse']:.1f} %, R²={m['r2']:.3f}, NMBE={m['nmbe']:+.2f} %. "
            f"Strongest criteria: {', '.join(f'{k} ({v:.2f})' for k, v in best)}; weakest: {weak[0][0]} ({weak[0][1]:.2f}).")


def decision_rationale(top: list[dict]) -> list[dict]:
    """Winner vs runner-up per criterion."""
    if not top:
        return []
    a = top[0]
    b = top[1] if len(top) > 1 else None
    rows = []
    for k, txt in CRITERIA_TEXT.items():
        if a.get("criteria_weights", {}).get(k, 1) == 0:
            continue
        va = a["criteria"][k]
        vb = b["criteria"][k] if b else None
        rows.append({"criterion": k, "description": txt, "winner": a["name"], "winner_score": va,
                     "runner_up": b["name"] if b else "-", "runner_up_score": vb,
                     "advantage": "winner" if (vb is None or va > vb + 1e-9) else ("runner-up" if vb > va + 1e-9 else "tie")})
    return rows


def analyze_ranking_sensitivity(results: list[dict], weights: dict[str, float], *, seed: int = 42,
                                perturbation: float = 0.20, draws: int = 64) -> dict:
    """Perturb the actual production criteria/weights, not a parallel rubric.

    Qualification is held fixed, exactly as in shortlist construction. Zero-weight
    (inapplicable) domain criteria remain zero throughout all scenarios.
    """
    if not np.isfinite(perturbation) or not 0 <= perturbation < 1:
        raise ValueError("perturbation must be finite and in [0, 1).")
    if isinstance(draws, bool) or not isinstance(draws, int) or draws < 0:
        raise ValueError("draws must be a non-negative integer.")
    candidates = [r for r in results if r.get("status") == "ok"]
    if not candidates:
        raise ValueError("Sensitivity requires evaluated candidates.")
    keys = list(weights)
    w0 = np.asarray([weights[k] for k in keys], dtype=float)
    if not np.isfinite(w0).all() or (w0 < 0).any() or w0.sum() <= 0:
        raise ValueError("Ranking weights must be finite, non-negative and non-zero.")
    w0 /= w0.sum()
    matrix = np.asarray([[r["criteria"][k] for k in keys] for r in candidates])

    if not np.isfinite(matrix).all():
        raise ValueError("Sensitivity criteria must be finite.")

    def order(w):
        scores = 100 * (matrix @ (w / w.sum()))
        return sorted(range(len(candidates)), key=lambda i: ranking_key(candidates[i], score=float(scores[i])))

    def shortlist(ranked):
        qualified = [i for i in ranked if candidates[i]["qualified"]]
        return qualified[:5] if qualified else ranked[:1]

    base = order(w0)
    top = shortlist(base)
    lo = {i: rank for rank, i in enumerate(base, 1)}
    hi = dict(lo)
    scenarios = []
    for j, key in enumerate(keys):
        if w0[j] == 0:
            continue
        for factor in (1 - perturbation, 1 + perturbation):
            w = w0.copy()
            w[j] *= factor
            scenarios.append((key, w))
    rng = np.random.default_rng(seed)
    scenarios.extend((None, w0 * rng.uniform(1 - perturbation, 1 + perturbation, len(w0))) for _ in range(draws))
    flips = 0
    min_jaccard = 1.0
    coordinate_flips = {key: False for key in keys}
    for key, w in scenarios:
        ranked = order(w)
        selected = shortlist(ranked)
        changed = selected[0] != top[0]
        flips += changed
        if key is not None:
            coordinate_flips[key] |= changed
        other = set(selected)
        min_jaccard = min(min_jaccard, len(set(top) & other) / len(set(top) | other))
        for rank, i in enumerate(ranked, 1):
            lo[i], hi[i] = min(lo[i], rank), max(hi[i], rank)
    return {
        "schema_version": 1, "rubric": "production-core-ranking-v1",
        "base_weights": {key: float(w0[i]) for i, key in enumerate(keys)},
        "baseline_top1": candidates[top[0]]["name"],
        "baseline_top5": [candidates[i]["name"] for i in top],
        "perturbation_fraction": perturbation, "n_scenarios": len(scenarios),
        "top1_flip_rate": flips / max(1, len(scenarios)), "top5_min_jaccard": min_jaccard,
        "rank_stable": flips == 0,
        "rank_intervals": {candidates[i]["name"]: [lo[i], hi[i]] for i in base},
        "criterion_coordinate_flips": coordinate_flips,
    }
