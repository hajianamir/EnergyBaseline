"""Transparent multi-criteria scoring, acceptance thresholds, Top-5 shortlist and rejection register."""
from __future__ import annotations

import math

import numpy as np

from .auto import AutoLog
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
    v = np.nan_to_num(vals.astype(float), nan=np.nan)
    ok = ~np.isnan(v)
    out = np.zeros(len(v))
    if not ok.any():
        return out
    if higher_better:
        best = np.nanmax(v)
        out[ok] = np.clip(v[ok], 0, None) / best if best > 0 else 0
    else:
        best = np.nanmin(v[ok])
        out[ok] = np.where(v[ok] > 0, best / np.maximum(v[ok], 1e-12), 1.0) if best > 0 else 1.0 / (1.0 + v[ok])
    return np.clip(out, 0, 1)


def score_models(results: list[dict], n_obs: int, th: dict, auto: AutoLog) -> dict:
    """Mutates ``results`` adding criteria scores, total, rank, pass flags. Returns profile info."""
    ok = [r for r in results if r.get("status") == "ok"]

    def _wp():
        if n_obs < 200:
            return "interpretable", f"n={n_obs} < 200", "small samples: favour simple, auditable equations"
        return "balanced", f"n={n_obs}", "default balanced weighting of accuracy, robustness, physics and transparency"
    prof = auto.decide("weight_profile", _wp)
    w = WEIGHTS[prof]
    if not ok:
        return {"profile": prof, "weights": w}
    M = lambda k: np.array([r["metrics"][k] for r in ok], float)  # noqa: E731
    crit: dict[str, np.ndarray] = {
        "cvrmse": _ratio_to_best(M("cvrmse")), "r2": _ratio_to_best(M("r2"), True),
        "nmbe": np.clip(1 - np.abs(M("nmbe")) / max(th["max_nmbe"], 1e-9), 0, 1), "mae": _ratio_to_best(M("mae")),
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
    crit["ashrae"] = np.array([0.5 * (r["metrics"]["cvrmse"] <= th["max_cvrmse"]) + 0.5 * (abs(r["metrics"]["nmbe"]) <= th["max_nmbe"]) for r in ok])
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
    for i, r in enumerate(ok):
        r["criteria"] = {c: float(v[i]) for c, v in crit.items()}
        r["score"] = float(100 * sum(w[c] * r["criteria"][c] for c in w))
        m = r["metrics"]
        fails = []
        if m["cvrmse"] > th["max_cvrmse"]:
            fails.append(f"CV(RMSE) {m['cvrmse']:.1f} % > {th['max_cvrmse']:.0f} %")
        if abs(m["nmbe"]) > th["max_nmbe"]:
            fails.append(f"|NMBE| {abs(m['nmbe']):.1f} % > {th['max_nmbe']:.0f} %")
        if m["r2"] == m["r2"] and m["r2"] < th["min_r2"]:
            fails.append(f"R² {m['r2']:.2f} < {th['min_r2']:.2f}")
        if r["plausibility"] < 0.5:
            fails.append("physically implausible response (" + "; ".join(r["plaus_notes"]) + ")")
        r["threshold_failures"] = fails
        r["qualified"] = not fails
    for i, r in enumerate(sorted(ok, key=lambda r: -r["score"]), 1):
        r["rank"] = i
    return {"profile": prof, "weights": w}


def nn_gate(results: list[dict], n_obs: int, auto: AutoLog) -> None:
    """Explicit NN rejection when data is insufficient or no improvement over classical models."""
    min_gain = float(auto.decide("nn_min_gain", lambda: (5.0, "default", "NN must beat the best non-NN model's CV(RMSE) by 5 % to justify its opacity")))
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
    ok = sorted([r for r in results if r.get("status") == "ok"], key=lambda r: -r["score"])
    qualified = [r for r in ok if r["qualified"]]
    notice = ""
    if len(qualified) >= top_k:
        top = qualified[:top_k]
    elif qualified:
        top = qualified
        notice = f"Only {len(qualified)} model(s) meet the minimum acceptance thresholds; the shortlist contains the available ones."
    elif ok:
        top = ok[:1]
        notice = ("LIMITATIONS WARNING: no model meets the acceptance thresholds (ASHRAE 14 / IPMVP). The best available model is offered "
                  "for information only - treat baseline deviations with caution.")
    else:
        top = []
        notice = "No model could be fitted for this target."
    names = {r["name"] for r in top}
    for r in ok:
        if r["name"] in names:
            r["status_final"] = "shortlisted"
            r["reason"] = rationale(r, top)
        else:
            r["status_final"] = "rejected"
            if r["threshold_failures"]:
                r["reason"] = "Failed acceptance: " + "; ".join(r["threshold_failures"])
            else:
                cut = top[-1] if top else None
                r["reason"] = (f"Qualified but ranked #{r['rank']} (score {r['score']:.1f}); the Top-{top_k} cut-off score is {cut['score']:.1f} ({cut['name']})."
                               if cut else "No shortlist available.")
    for r in results:
        if r.get("status") != "ok":
            r["status_final"] = "rejected"
            r["reason"] = r.get("error", "not available")
    return {"top": top, "notice": notice, "n_qualified": len(qualified)}


def rationale(r: dict, top: list[dict]) -> str:
    c = r["criteria"]
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
        va = a["criteria"][k]
        vb = b["criteria"][k] if b else None
        rows.append({"criterion": k, "description": txt, "winner": a["name"], "winner_score": va,
                     "runner_up": b["name"] if b else "-", "runner_up_score": vb,
                     "advantage": "winner" if (vb is None or va > vb + 1e-9) else ("runner-up" if vb > va + 1e-9 else "tie")})
    return rows
