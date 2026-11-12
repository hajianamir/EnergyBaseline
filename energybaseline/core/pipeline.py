"""Per-target end-to-end analysis pipeline (temporal -> quality -> EDA -> mining -> features -> models -> Top-5)."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import platform
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .. import __version__
from . import nn as nnmod
from .auto import AutoLog, is_auto
from .eda import run_eda
from .features import FeatureSpec, build_spec, select_features
from .mining import run_mining
from .models import Blend, Ctx, build_portfolio, diagnostics, evaluate_candidate, make_folds, metrics, plausibility
from .quality import preprocess_target
from .scoring import build_shortlist, decision_rationale, nn_gate, score_models, thresholds
from .temporal import prepare_temporal

log = logging.getLogger("energybaseline.pipeline")
Progress = Callable[[str, float, str], None]


class Cancelled(Exception):
    pass


@dataclass
class TargetResult:
    target: str
    status: str = "ok"
    error: str = ""
    temporal: dict = field(default_factory=dict)
    granularity: str = ""
    freq: str = ""
    exclusions: list = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    outliers: dict = field(default_factory=dict)
    eda: dict = field(default_factory=dict)
    mining: list = field(default_factory=list)
    feature_info: dict = field(default_factory=dict)
    selected_features: list = field(default_factory=list)
    required_columns: list = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)
    scoring: dict = field(default_factory=dict)
    leaderboard: list = field(default_factory=list)
    shortlist: list = field(default_factory=list)  # list of dict (see _top_entry)
    notice: str = ""
    rationale: list = field(default_factory=list)
    comparative: list = field(default_factory=list)
    register: list = field(default_factory=list)  # rejected / unavailable models and reasons
    n_obs: int = 0
    series: pd.DataFrame | None = None  # y + best-model OOF for charts
    auto_summary: dict = field(default_factory=dict)
    seconds: float = 0.0


@dataclass
class RunResult:
    seed: int
    config: dict
    versions: dict
    auto: list
    targets: dict
    created: str
    dataset: dict = field(default_factory=dict)
    run_id: int | None = None
    cancelled: bool = False


def collect_versions() -> dict:
    v = {"app": __version__, "python": sys.version.split()[0], "platform": platform.platform()}
    for mod in ("numpy", "pandas", "scipy", "sklearn", "statsmodels", "torch", "xgboost", "lightgbm", "catboost", "optuna", "shap", "PySide6", "sqlalchemy", "mlxtend", "ruptures"):
        try:
            m = __import__(mod)
            v[mod] = getattr(m, "__version__", "?")
        except Exception:
            v[mod] = "not installed"
    v["nn_device"] = nnmod.device_name()
    return v


def _seed_from(df: pd.DataFrame, targets: list[str], sha: str | None) -> int:
    h = hashlib.sha256(((sha or "") + "|".join(map(str, df.columns)) + str(len(df)) + "|".join(targets)).encode()).hexdigest()
    return int(h[:8], 16) % (2 ** 31 - 1)


def resolve_alert(config: dict, cvrmse: float, auto: AutoLog | None = None) -> dict:
    mode_u, val_u = config.get("alert_mode", "AUTO"), config.get("alert_value", "AUTO")
    if not is_auto(mode_u):
        mode = str(mode_u)
        value = float(val_u) if not is_auto(val_u) else (10.0 if mode == "percent" else 2.0)
        why = "user-specified"
    elif 2 * cvrmse > 10:
        mode, value, why = "cvrmse_multiple", 2.0, f"2 x CV(RMSE) = {2 * cvrmse:.1f} % exceeds 10 %: a 10 % band would be inside the model's own noise"
    else:
        mode, value, why = "percent", 10.0, f"2 x CV(RMSE) = {2 * cvrmse:.1f} % <= 10 %: fixed 10 % band is wider than model noise"
    if auto is not None:
        auto.note("alert_mode", f"{mode} {value:g}", f"CV(RMSE)={cvrmse:.2f} %", why)
    return {"mode": mode, "value": value, "why": why}


def _trim_spec(spec: FeatureSpec, selected: list[str]) -> None:
    sel = set(selected)
    used = set()
    for c in spec.raw_cols:
        if (c in sel or any(s.startswith((f"{c}_lag", f"{c}_roll")) for s in sel)
                or (c == spec.temp_col and any(s.startswith(("HDD", "CDD")) for s in sel))
                or any(s.startswith("ix:") and c in s[3:].split("*") for s in sel)):
            used.add(c)
    spec.raw_cols = [c for c in spec.raw_cols if c in used]
    spec.cat_levels = {c: lv for c, lv in spec.cat_levels.items() if any(s.startswith(f"{c}=") for s in sel)}
    spec.lag_cols = [c for c in spec.lag_cols if c in used]
    spec.roll_cols = [c for c in spec.roll_cols if c in used]
    if spec.temp_col not in used:
        spec.temp_col, spec.balance_heat, spec.balance_cool = None, None, None
    spec.columns_ = list(selected)
    spec.selected = list(selected)


def _importance(model, X: pd.DataFrame, y: np.ndarray, seed: int, with_shap: bool) -> dict:
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(X), min(len(X), 1500), replace=False))
    Xs, ys = X.iloc[idx], y[idx]
    base = float(np.sqrt(np.mean((model.predict(Xs) - ys) ** 2)))
    imp = {}
    for c in X.columns:
        drops = []
        for _ in range(2):
            Xp = Xs.copy()
            Xp[c] = rng.permutation(Xp[c].values)
            drops.append(float(np.sqrt(np.mean((model.predict(Xp) - ys) ** 2))) - base)
        imp[c] = float(np.mean(drops))
    order = sorted(imp, key=lambda k: -imp[k])
    out = {"permutation": {k: imp[k] for k in order}, "pdp": {}, "ice": {}}
    for c in order[:3]:
        grid = np.unique(np.quantile(X[c].values, np.linspace(0.05, 0.95, 10)))
        pdp = []
        for v in grid:
            Xc = Xs.copy()
            Xc[c] = v
            pdp.append(float(np.mean(model.predict(Xc))))
        out["pdp"][c] = {"x": grid.tolist(), "y": pdp}
    if order:
        c = order[0]
        grid = np.unique(np.quantile(X[c].values, np.linspace(0.05, 0.95, 10)))
        sub = Xs.iloc[:10]
        curves = []
        for i in range(len(sub)):
            row = []
            for v in grid:
                r = sub.iloc[[i]].copy()
                r[c] = v
                row.append(float(model.predict(r)[0]))
            curves.append(row)
        out["ice"][c] = {"x": grid.tolist(), "curves": curves}
    if with_shap and len(X) <= 20000 and getattr(model, "kind", "") == "tree":
        try:
            import shap
            e = getattr(model.est, "est", model.est)
            D = model.design.transform(Xs).values
            sv = shap.TreeExplainer(e).shap_values(D[:300])
            out["shap_mean_abs"] = dict(zip(model.names, np.abs(sv).mean(0).tolist()))
        except Exception as exc:
            out["shap_note"] = f"SHAP unavailable: {exc}"
    elif with_shap:
        out["shap_note"] = "SHAP computed for tree models only (when 'shap' is installed); permutation importance used otherwise."
    return out


def analyze_target(df: pd.DataFrame, time_col: str, target: str, all_targets: list[str], config: dict, seed: int,
                   auto_root: AutoLog, n_jobs: int, progress: Progress, cancel: Callable[[], bool],
                   formulas_artifact: Callable[[str, str], str] | None = None) -> tuple[TargetResult, dict]:
    t0 = time.time()
    res = TargetResult(target)
    bundles: dict = {}
    cfg = {k: v for k, v in config.items() if k != "per_target"}
    cfg.update((config.get("per_target") or {}).get(target, {}))
    auto = AutoLog(cfg, target)
    auto.records = auto_root.records

    def emit(p, msg):
        if cancel():
            raise Cancelled()
        progress(target, p, msg)

    try:
        emit(2, "Temporal configuration")
        tdf, trep, g = prepare_temporal(df, time_col, auto)
        res.temporal, res.granularity, res.freq = trep.as_dict(), g.label, g.freq
        emit(8, "Quality & preprocessing")
        prep = preprocess_target(tdf, target, all_targets, g, auto, seed, trep.n_duplicates)
        res.exclusions, res.quality, res.notes, res.outliers = prep.exclusions, prep.quality, prep.notes, prep.outliers
        emit(14, "Exploratory analysis")
        res.eda = run_eda(prep.df, target, prep.predictors, g)
        if auto.decide("enable_mining", lambda: (True, "default", "data-mining layer enabled")):
            emit(18, "Data mining (clustering, anomalies, association, time-series)")
            res.mining = run_mining(prep.df, target, prep.predictors, g, seed, cancel)
        emit(34, "Feature engineering & selection")
        spec, Xall, finfo = build_spec(prep.df, target, prep.predictors, prep.cat_cols, g, auto)
        valid = prep.df[target].notna().values
        X, y = Xall[valid], prep.df.loc[valid, target].values.astype(float)
        if len(y) < 8:
            raise ValueError("Fewer than 8 valid training observations after cleaning.")
        sel = select_features(X, pd.Series(y, index=X.index), auto, seed, res.exclusions)
        if spec.temp_col and spec.temp_col in X.columns and spec.temp_col not in sel:
            sel.append(spec.temp_col)
        sel = [c for c in X.columns if c in set(sel)]
        _trim_spec(spec, sel)
        X = X[sel]
        res.selected_features, res.required_columns, res.feature_info = sel, spec.required_columns(), finfo
        n = len(y)
        res.n_obs = n

        # ---- validation configuration
        span_years = (X.index.max() - X.index.min()).days / 365.25
        scheme = auto.decide("cv_scheme", lambda: (("blocked_kfold", f"span={span_years:.2f} y < 2 y", "short histories: every season must appear in training -> purged blocked K-fold") if span_years < 2
                                                    else ("forward_chaining", f"span={span_years:.2f} y >= 2 y", "enough history: expanding-window forward chaining")))
        k = int(auto.decide("cv_folds", lambda: ((3 if n < 100 else 4 if n < 1000 else 5 if n < 10000 else 6), f"n={n}", "more folds as data grows")))
        trials = int(auto.decide("hpo_trials", lambda: ((0 if n < 80 else 8 if n <= 1500 else 5 if n <= 6000 else 3), f"n={n}", "HPO budget shrinks with data size to bound run-time")))
        epochs = int(auto.decide("nn_max_epochs", lambda: ((150 if n < 1000 else 80 if n < 5000 else 40), f"n={n}", "fewer epochs for large n; early stopping always on")))
        seq = int(auto.decide("nn_seq_len", lambda: ({"subdaily": max(4, min(12, int(g.steps_per_day // 2))), "daily": 7, "weekly": 4, "monthly": 3}[g.tier], f"granularity={g.label}", "window covers half a day (sub-daily), a week (daily) or a few periods")))
        nn_on = bool(auto.decide("enable_nn", lambda: (n >= 300, f"n={n}", "neural networks need >= 300 observations to be trainable and comparable")))
        fam = auto.decide("model_families", lambda: (["linear", "tree", "kernel", "nn"], "default", "all applicable families compete"))
        fam = tuple(fam)
        gap = max(1, min(int(round(max(g.steps_per_day, 1))), n // 20))
        folds = make_folds(n, k, scheme, gap)
        if not folds:
            raise ValueError("Not enough observations to build validation folds.")
        auto.note("validation_gap", gap, f"steps/day={g.steps_per_day:.3g}, n={n}", "purge gap of one day (>=1 step) between train and validation blocks to limit autocorrelation leakage")
        ctx = Ctx(target=target, seed=seed, n_jobs=n_jobs, hpo_trials=trials, nn_max_epochs=epochs, seq_len=seq, cv_scheme=scheme, folds=k, gap=gap,
                  steps_per_day=g.steps_per_day, tier=g.tier, enable_nn=nn_on, families=fam, raw_names=list(spec.raw_cols), temp_col=spec.temp_col, cancel=cancel)
        cands, missing = build_portfolio(ctx, n)
        register: list[dict] = [{"model": m["model"], "family": "-", "status": "not evaluated", "reason": m["reason"]} for m in missing]
        results: list[dict] = []
        from .models import Unavailable
        for i, cand in enumerate(cands):
            emit(40 + 50 * i / max(1, len(cands)), f"Model {i + 1}/{len(cands)}: {cand.name}")
            if cand.fam_key not in fam:
                register.append({"model": cand.name, "family": cand.family, "status": "not evaluated", "reason": f"family '{cand.fam_key}' excluded in settings"})
                continue
            if cand.kind == "nn" and not nn_on:
                register.append({"model": cand.name, "family": cand.family, "status": "rejected", "reason": f"data insufficient for neural networks (n={n} < 300) - explicit rejection"})
                continue
            try:
                r = evaluate_candidate(cand, X, y, folds, ctx)
                r["status"], r["mean_y"] = "ok", float(np.mean(y))
                results.append(r)
            except InterruptedError:
                raise Cancelled()
            except Unavailable as exc:
                register.append({"model": cand.name, "family": cand.family, "status": "not applicable", "reason": str(exc)})
            except Exception as exc:
                log.warning("model %s failed: %s", cand.name, exc)
                register.append({"model": cand.name, "family": cand.family, "status": "failed", "reason": f"fitting failed: {exc}"})
        if not results:
            raise ValueError("No model could be fitted for this target.")

        # ---- optional NN + tree blend (only if justified)
        nn_r = [r for r in results if r["kind"] == "nn"]
        tr_r = [r for r in results if r["kind"] == "tree"]
        if nn_r and tr_r:
            a, b = min(nn_r, key=lambda r: r["metrics"]["cvrmse"]), min(tr_r, key=lambda r: r["metrics"]["cvrmse"])
            both = ~np.isnan(a["oof"]) & ~np.isnan(b["oof"])
            if both.sum() > 10:
                bo = np.where(both, 0.5 * a["oof"] + 0.5 * b["oof"], np.nan)
                m = metrics(y[both], bo[both])
                best_single = min(a["metrics"]["cvrmse"], b["metrics"]["cvrmse"])
                if m["cvrmse"] < 0.97 * best_single:
                    bl = Blend(a["model"], b["model"], target)
                    k_ = bl.n_params()
                    rss = float(np.sum((y[both] - bo[both]) ** 2))
                    nc = int(both.sum())
                    q = np.quantile(y[both] - bo[both], [0.025, 0.975])
                    plz, pn = plausibility(bl, X, y)
                    results.append({"name": bl.name, "family": bl.family, "tier": 5, "kind": "blend", "oof": bo, "metrics": m, "train_metrics": metrics(y, bl.predict(X)),
                                    "fold_cvrmse": [m["cvrmse"]], "robustness": 0.0, "n_params": k_, "aic": nc * math.log(rss / nc) + 2 * k_, "bic": nc * math.log(rss / nc) + k_ * math.log(nc),
                                    "model": bl, "hpo": "n/a", "params": {}, "seconds": 0.0, "diagnostics": diagnostics(y[both], bo[both]), "resid_q": (float(q[0]), float(q[1])),
                                    "plausibility": plz, "plaus_notes": pn, "status": "ok", "mean_y": float(np.mean(y))})
                    auto.note("nn_tree_blend", bl.name, f"blend CV(RMSE) {m['cvrmse']:.2f} % vs best single {best_single:.2f} % (>3 % gain)", "NN + tree ensemble added only because it improves CV(RMSE) by more than 3 %")
                else:
                    auto.note("nn_tree_blend", "not created", f"blend CV(RMSE) {m['cvrmse']:.2f} % vs best single {best_single:.2f} %", "gain below 3 % - hybrid not justified")

        emit(92, "Scoring & Top-5 shortlist")
        th = thresholds(g, auto)
        res.thresholds = th
        res.scoring = score_models(results, n, th, auto)
        nn_gate(results, n, auto)
        sl = build_shortlist(results)
        res.notice = sl["notice"]
        top = sl["top"]
        res.rationale = decision_rationale(top)
        # ---- interpretability + bundles
        with_imp = bool(auto.decide("include_importance", lambda: (n <= 20000, f"n={n}", "interpretability extras on unless n>20000 (cost)")))
        stats = {c: {"min": float(Xall[c].min()), "max": float(Xall[c].max()), "mean": float(Xall[c].mean()), "std": float(Xall[c].std())} for c in spec.raw_cols if c in Xall}
        agg_map = res.temporal.get("aggregation") or {}
        for rank, r in enumerate(top, 1):
            emit(93 + 6 * rank / max(1, len(top)), f"Interpretability for {r['name']}")
            art = formulas_artifact(target, r["name"]) if formulas_artifact else ""
            r["formula"] = r["model"].formula(sel, art)
            r["artifact"] = art
            if with_imp:
                try:
                    r["importance"] = _importance(r["model"], X, y, seed, with_imp)
                except Exception as exc:
                    r["importance"] = {"note": f"importance failed: {exc}"}
            alert = resolve_alert(config, r["metrics"]["cvrmse"], auto if rank == 1 else None)
            bundles[r["name"]] = {
                "target": target, "model_name": r["name"], "model": r["model"], "spec": spec, "resid_q": r["resid_q"], "metrics": r["metrics"], "rmse": r["metrics"]["rmse"],
                "formula": r["formula"], "required_columns": spec.required_columns(), "train_stats": stats, "granularity": g.label, "freq": g.freq,
                "steps_per_day": g.steps_per_day, "aggregation": agg_map, "mean_y": float(np.mean(y)), "alert": alert, "thresholds": th, "versions": collect_versions(), "seed": seed,
                "baseline_window": res.temporal.get("baseline_window"), "units_note": "", "oof_index": [str(i) for i in X.index[:3]],
            }
        # ---- result assembly
        res.shortlist = [{"rank": r["rank"], "name": r["name"], "family": r["family"], "kind": r["kind"], "tier": r["tier"], "score": r["score"], "metrics": r["metrics"],
                          "train_metrics": r["train_metrics"], "criteria": r["criteria"], "formula": r["formula"], "reason": r["reason"], "uncertainty": {"lower": r["resid_q"][0], "upper": r["resid_q"][1], "level": 0.95,
                          "basis": "empirical 2.5/97.5 % quantiles of out-of-fold residuals"}, "diagnostics": r["diagnostics"], "robustness": r["robustness"], "n_params": r["n_params"],
                          "hpo": r["hpo"], "params": {k_: (v if isinstance(v, (int, float, str)) else str(v)) for k_, v in r["params"].items()}, "importance": r.get("importance", {}), "history": r.get("history"),
                          "plaus_notes": r["plaus_notes"], "qualified": r["qualified"], "failures": r["threshold_failures"], "artifact": r["artifact"], "fold_cvrmse": r["fold_cvrmse"]} for r in top]
        for r in sorted(results, key=lambda r: -r["score"]):
            res.leaderboard.append({"rank": r["rank"], "model": r["name"], "family": r["family"], "tier": r["tier"], "score": round(r["score"], 2), **{k_: round(v, 4) for k_, v in r["metrics"].items() if k_ != "n"},
                                    "train_r2": round(r["train_metrics"]["r2"], 4), "n_params": r["n_params"], "aic": round(r["aic"], 1), "bic": round(r["bic"], 1), "robustness": round(r["robustness"], 3),
                                    "dw": round(r["diagnostics"]["durbin_watson"], 3), "jb_p": round(r["diagnostics"]["jarque_bera_p"], 4), "lb_p": round(r["diagnostics"]["ljung_box_p"], 4), "bp_p": round(r["diagnostics"]["breusch_pagan_p"], 4),
                                    "plausibility": round(r["plausibility"], 2), "qualified": r["qualified"], "status": r["status_final"], "reason": r["reason"], "seconds": round(r["seconds"], 1),
                                    **{f"c_{k_}": round(v, 3) for k_, v in r["criteria"].items()}})
        for r in results:
            if r["status_final"] == "rejected":
                register.append({"model": r["name"], "family": r["family"], "status": "rejected", "reason": r["reason"]})
        res.register = register
        for r in top[:3]:
            d = r["diagnostics"]
            res.comparative.append({"model": r["name"], **{k_: round(v, 4) for k_, v in r["metrics"].items() if k_ != "n"}, "durbin_watson": round(d["durbin_watson"], 3), "jarque_bera_p": round(d["jarque_bera_p"], 4),
                                    "ljung_box_p": round(d["ljung_box_p"], 4), "breusch_pagan_p": round(d["breusch_pagan_p"], 4), "fold_cvrmse_std": round(float(np.std(r["fold_cvrmse"])), 3), "n_params": r["n_params"]})
        if top:
            ser = pd.DataFrame({"actual": y}, index=X.index)
            ser["baseline_oof"] = top[0]["oof"]
            ser["baseline_fit"] = top[0]["model"].predict(X)
            res.series = ser
        emit(100, "Done")
    except Cancelled:
        raise
    except Exception as exc:
        log.error("target %s failed: %s\n%s", target, exc, traceback.format_exc())
        res.status, res.error = "failed", f"{type(exc).__name__}: {exc}"
        progress(target, 100, f"FAILED: {exc}")
    res.seconds = time.time() - t0
    return res, bundles


def run_analysis(df: pd.DataFrame, time_col: str, targets: list[str], config: dict | None = None, *, dataset_sha: str | None = None,
                 dataset_info: dict | None = None, progress: Progress | None = None, cancel: Callable[[], bool] | None = None,
                 artifact_fn: Callable[[str, str], str] | None = None) -> tuple[RunResult, dict]:
    config = dict(config or {})
    progress = progress or (lambda t, p, m: None)
    cancel = cancel or (lambda: False)
    auto = AutoLog(config)
    seed = int(auto.decide("seed", lambda: (_seed_from(df, targets, dataset_sha), "SHA-256 of dataset hash, column names, row count, targets", "deterministic seed derived from the data (same data -> same seed)")))
    cpu = os.cpu_count() or 2
    par = int(auto.decide("parallel_targets", lambda: ((1 if len(targets) == 1 else min(2, max(1, cpu // 2))), f"targets={len(targets)}, cores={cpu}", "sequential for a single target, else up to cores/2 (max 2)")))
    par = max(1, min(par, len(targets), cpu))
    n_jobs = max(1, cpu // par)
    nnmod.seed_everything(seed)
    out_t: dict[str, TargetResult] = {}
    bundles: dict[str, dict] = {}

    def work(t):
        return analyze_target(df, time_col, t, targets, config, seed, auto, n_jobs, progress, cancel, artifact_fn)

    cancelled = False
    try:
        if par == 1:
            for t in targets:
                r, b = work(t)
                out_t[t], bundles[t] = r, b
        else:
            with ThreadPoolExecutor(par) as ex:
                futs = {t: ex.submit(work, t) for t in targets}
                for t, f in futs.items():
                    r, b = f.result()
                    out_t[t], bundles[t] = r, b
    except Cancelled:
        cancelled = True
    run = RunResult(seed=seed, config=config, versions=collect_versions(), auto=auto.as_dicts(), targets=out_t,
                    created=time.strftime("%Y-%m-%d %H:%M:%S"), dataset=dataset_info or {}, cancelled=cancelled)
    return run, bundles
