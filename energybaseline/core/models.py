"""Baseline model portfolio: designs, estimators, nested time-aware validation, formulas, diagnostics."""
from __future__ import annotations

import logging
import math
import time
import warnings
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.preprocessing import StandardScaler

from . import nn as nnmod

log = logging.getLogger("energybaseline.models")
warnings.filterwarnings("ignore")
TIME_INT = ("hour", "dow", "month")
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class Unavailable(Exception):
    pass


@dataclass
class Ctx:
    target: str
    seed: int = 42
    n_jobs: int = 2
    hpo_trials: int = 4
    nn_max_epochs: int = 60
    seq_len: int = 12
    cv_scheme: str = "blocked_kfold"
    folds: int = 4
    gap: int = 1
    steps_per_day: float = 24.0
    tier: str = "subdaily"
    enable_nn: bool = True
    families: tuple = ("linear", "tree", "kernel", "nn")
    raw_names: list = field(default_factory=list)
    temp_col: str | None = None
    cancel: Callable[[], bool] = lambda: False


# ============================================================== folds
def make_folds(n: int, k: int, scheme: str, gap: int) -> list[tuple[np.ndarray, np.ndarray]]:
    idx = np.arange(n)
    out = []
    if scheme == "forward_chaining":
        b = np.linspace(int(n * 0.5), n, k + 1).astype(int)
        for i in range(k):
            tr = idx[: max(1, b[i] - gap)]
            te = idx[b[i]: b[i + 1]]
            if len(te) and len(tr) >= 5:
                out.append((tr, te))
    else:
        b = np.linspace(0, n, k + 1).astype(int)
        for i in range(k):
            te = idx[b[i]: b[i + 1]]
            mask = np.ones(n, bool)
            mask[max(0, b[i] - gap): min(n, b[i + 1] + gap)] = False
            tr = idx[mask]
            if len(te) and len(tr) >= 5:
                out.append((tr, te))
    return out


# ============================================================== metrics / diagnostics
def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    y, p = np.asarray(y, float), np.asarray(p, float)
    res = y - p
    n = len(y)
    ybar = float(np.mean(y))
    den = abs(ybar) if abs(ybar) > 1e-12 else float(np.mean(np.abs(y))) or 1.0
    sst = float(np.sum((y - ybar) ** 2))
    nz = np.abs(y) > 1e-9
    return {
        "n": n,
        "r2": float(1 - np.sum(res ** 2) / sst) if sst > 0 else float("nan"),
        "rmse": float(np.sqrt(np.mean(res ** 2))),
        "cvrmse": float(100 * np.sqrt(np.mean(res ** 2)) / den),
        "nmbe": float(100 * np.sum(res) / (n * den)),
        "mae": float(np.mean(np.abs(res))),
        "mape": float(100 * np.mean(np.abs(res[nz] / y[nz]))) if nz.any() else float("nan"),
        "smape": float(100 * np.mean(2 * np.abs(res) / np.maximum(np.abs(y) + np.abs(p), 1e-12))),
    }


def diagnostics(y: np.ndarray, p: np.ndarray) -> dict:
    from statsmodels.stats.diagnostic import acorr_ljungbox, het_breuschpagan
    from statsmodels.stats.stattools import durbin_watson
    res = np.asarray(y, float) - np.asarray(p, float)
    d: dict = {}
    try:
        jb = stats.jarque_bera(res)
        d["jarque_bera_p"] = float(jb.pvalue)
        d["jarque_bera_stat"] = float(jb.statistic)
    except Exception:
        d["jarque_bera_p"] = float("nan")
    try:
        d["durbin_watson"] = float(durbin_watson(res))
    except Exception:
        d["durbin_watson"] = float("nan")
    try:
        lb = acorr_ljungbox(res, lags=[min(10, max(1, len(res) // 5))], return_df=True)
        d["ljung_box_p"] = float(lb["lb_pvalue"].iloc[0])
    except Exception:
        d["ljung_box_p"] = float("nan")
    try:
        exog = np.column_stack([np.ones(len(p)), np.asarray(p, float)])
        d["breusch_pagan_p"] = float(het_breuschpagan(res, exog)[1])
    except Exception:
        d["breusch_pagan_p"] = float("nan")
    passed = [
        d["jarque_bera_p"] > 0.05, 1.5 <= d["durbin_watson"] <= 2.5, d["ljung_box_p"] > 0.05, d["breusch_pagan_p"] > 0.05,
    ]
    d["tests_passed"] = int(sum(bool(x) for x in passed if x == x))
    d["residual_mean"] = float(res.mean())
    d["residual_std"] = float(res.std())
    return d


# ============================================================== designs
class Design:
    cols: list[str]

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> "Design":
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError


def _base_cols(X: pd.DataFrame, ix=False, timeint=False) -> list[str]:
    return [c for c in X.columns if (ix or not c.startswith("ix:")) and (timeint or c not in TIME_INT)]


class ColsDesign(Design):
    def __init__(self, ix=False, timeint=False, only: list[str] | None = None, dd_only=False):
        self.ix, self.timeint, self.only, self.dd_only = ix, timeint, only, dd_only

    def fit(self, X, y):
        cols = _base_cols(X, self.ix, self.timeint)
        if self.only is not None:
            cols = [c for c in cols if c in self.only]
        if self.dd_only:
            cols = [c for c in X.columns if c.startswith(("HDD", "CDD"))]
            if not cols:
                raise Unavailable("no degree-day features (needs an outdoor-temperature column)")
        self.cols = cols
        return self

    def transform(self, X):
        return X[self.cols].fillna(0.0)


def _top_numeric(X: pd.DataFrame, y: np.ndarray, k: int, exclude=()) -> list[str]:
    cands = [c for c in _base_cols(X) if c not in exclude and X[c].nunique() > 6]
    sc = {c: abs(np.corrcoef(X[c].fillna(0), y)[0, 1]) if X[c].std() > 0 else 0 for c in cands}
    return sorted(cands, key=lambda c: -np.nan_to_num(sc[c]))[:k]


class PolyDesign(Design):
    def __init__(self, k=3, degree=2):
        self.k, self.degree = k, degree

    def fit(self, X, y):
        self.top = _top_numeric(X, y, self.k)
        if not self.top:
            raise Unavailable("no continuous driver for polynomial terms")
        self.rest = [c for c in _base_cols(X) if c not in self.top]
        self.mu = {c: float(X[c].mean()) for c in self.top}
        return self

    def transform(self, X):
        out = pd.DataFrame(index=X.index)
        for c in self.top:
            out[c] = X[c].fillna(self.mu[c])
        for i, a in enumerate(self.top):
            for b in self.top[i:]:
                out[f"{a}^2" if a == b else f"{a}*{b}"] = out[a] * out[b]
        for c in self.rest:
            out[c] = X[c].fillna(0.0)
        return out


class PiecewiseDesign(Design):
    """Change-point model on the best driver: y = b0 + b1*max(0,c1-x) + b2*max(0,x-c2) [+ other linear terms]."""

    def __init__(self, raw_names, prefer=None):
        self.raw_names, self.prefer = raw_names, prefer

    def fit(self, X, y):
        cand = [c for c in X.columns if c in self.raw_names and X[c].nunique() > 6]
        if not cand:
            raise Unavailable("no continuous raw driver for a change-point model")
        self.x = self.prefer if self.prefer in cand else max(cand, key=lambda c: abs(np.nan_to_num(np.corrcoef(X[c].fillna(X[c].median()), y)[0, 1])))
        x = X[self.x].fillna(X[self.x].median()).values
        qs = np.unique(np.quantile(x, np.linspace(0.1, 0.9, 9)))
        n = len(y)
        best = (np.inf, None, None)
        opts = [(c1, None) for c1 in qs] + [(None, c2) for c2 in qs] + [(a, b) for a in qs for b in qs if b > a]
        rest = [c for c in _base_cols(X) if c != self.x]
        R = X[rest].fillna(0.0).values if rest else np.zeros((n, 0))
        for c1, c2 in opts:
            cols = [np.ones(n)]
            if c1 is not None:
                cols.append(np.maximum(0, c1 - x))
            if c2 is not None:
                cols.append(np.maximum(0, x - c2))
            A = np.column_stack(cols + ([R] if R.shape[1] else []))
            beta, *_ = np.linalg.lstsq(A, y, rcond=None)
            rss = float(np.sum((y - A @ beta) ** 2))
            bic = n * math.log(max(rss, 1e-12) / n) + A.shape[1] * math.log(n)
            if bic < best[0]:
                best = (bic, c1, c2)
        _, self.c1, self.c2 = best
        self.rest = rest
        return self

    def transform(self, X):
        x = X[self.x].fillna(X[self.x].median() if X[self.x].notna().any() else 0).values
        out = pd.DataFrame(index=X.index)
        if self.c1 is not None:
            out[f"max(0, {self.c1:.4g} - {self.x})"] = np.maximum(0, self.c1 - x)
        if self.c2 is not None:
            out[f"max(0, {self.x} - {self.c2:.4g})"] = np.maximum(0, x - self.c2)
        for c in self.rest:
            out[c] = X[c].fillna(0.0)
        return out


class TOWTDesign(Design):
    """Time-of-week-and-temperature: time-of-week indicators + piecewise-linear temperature + other drivers."""

    def __init__(self, ctx: Ctx):
        self.tier, self.temp_col = ctx.tier, ctx.temp_col

    def fit(self, X, y):
        if self.tier not in ("subdaily", "daily"):
            raise Unavailable("TOWT requires sub-daily or daily data")
        if "dow" not in X.columns or (self.tier == "subdaily" and "hour" not in X.columns):
            raise Unavailable("TOWT requires calendar features (enable calendar features)")
        sub = self.tier == "subdaily"
        b = (X["dow"].astype(int) * 24 + np.floor(X["hour"]).astype(int)) if sub else X["dow"].astype(int)
        self.buckets = sorted(set(b.tolist()))[1:]  # first bucket is the reference level
        tc = self.temp_col if self.temp_col in X.columns else None
        self.tc = tc
        self.knots = [float(v) for v in np.unique(np.quantile(X[tc].dropna(), [0.2, 0.4, 0.6, 0.8]))] if tc else []
        self.rest = [c for c in _base_cols(X) if c != tc and not c.startswith(("sin_", "cos_", "is_weekend", "is_holiday"))]
        return self

    def _label(self, v: int) -> str:
        return f"TOW[{DOW[v // 24]} {v % 24:02d}h]" if self.tier == "subdaily" else f"DOW[{DOW[v]}]"

    def transform(self, X):
        sub = self.tier == "subdaily"
        b = (X["dow"].astype(int) * 24 + np.floor(X["hour"]).astype(int)) if sub else X["dow"].astype(int)
        out = pd.DataFrame(index=X.index)
        for v in self.buckets:
            out[self._label(v)] = (b == v).astype(float)
        if self.tc:
            T = X[self.tc].fillna(X[self.tc].median())
            out[self.tc] = T
            for k in self.knots:
                out[f"max(0, {self.tc} - {k:.4g})"] = np.maximum(0, T - k)
        for c in self.rest:
            out[c] = X[c].fillna(0.0)
        return out


class SplineDesign(Design):
    def __init__(self, k=4):
        self.k = k

    def fit(self, X, y):
        from sklearn.preprocessing import SplineTransformer
        self.top = _top_numeric(X, y, self.k)
        if not self.top:
            raise Unavailable("no continuous driver for spline terms")
        self.rest = [c for c in _base_cols(X) if c not in self.top]
        self.med = {c: float(X[c].median()) for c in self.top}
        self.sp = SplineTransformer(n_knots=5, degree=3, knots="quantile", include_bias=False, extrapolation="constant").fit(X[self.top].fillna(pd.Series(self.med)))
        self.names = [f"B[{n.split('_sp_')[0]}#{n.split('_sp_')[1]}]" if "_sp_" in n else n for n in self.sp.get_feature_names_out(self.top)]
        return self

    def transform(self, X):
        B = self.sp.transform(X[self.top].fillna(pd.Series(self.med)))
        out = pd.DataFrame(B, index=X.index, columns=self.names)
        for c in self.rest:
            out[c] = X[c].fillna(0.0)
        return out


class AllDesign(ColsDesign):
    """Everything incl. interaction + integer time columns (tree / NN / kernel models)."""

    def __init__(self):
        super().__init__(ix=True, timeint=True)


# ============================================================== helper estimators
class MeanEst:
    def fit(self, A, y):
        self.m_ = float(np.mean(y))
        return self

    def predict(self, A):
        return np.full(len(A), self.m_)


class YScaled:
    def __init__(self, est):
        self.est = est

    def fit(self, A, y):
        self.mu, self.sd = float(np.mean(y)), float(np.std(y) or 1.0)
        self.est.fit(A, (y - self.mu) / self.sd)
        return self

    def predict(self, A):
        return self.est.predict(A) * self.sd + self.mu

    def __getattr__(self, k):
        if k in ("est", "mu", "sd"):
            raise AttributeError(k)
        return getattr(self.est, k)


# ============================================================== HPO
class _RandTrial:
    def __init__(self, rng):
        self.rng = rng

    def suggest_int(self, n, lo, hi, log=False):
        return int(round(math.exp(self.rng.uniform(math.log(lo), math.log(hi))))) if log else int(self.rng.integers(lo, hi + 1))

    def suggest_float(self, n, lo, hi, log=False):
        return float(math.exp(self.rng.uniform(math.log(lo), math.log(hi)))) if log else float(self.rng.uniform(lo, hi))

    def suggest_categorical(self, n, ch):
        return ch[int(self.rng.integers(0, len(ch)))]


def hpo(build, space, A, y, ctx: Ctx, max_rows=2500) -> tuple[dict, str]:
    n = len(y)
    if ctx.hpo_trials <= 0 or n < 80:
        return {}, "HPO skipped (n<80 or trials=0): conservative defaults"
    stride = max(1, math.ceil(n / max_rows))
    idx = np.arange(0, n, stride)
    As, ys = A[idx], y[idx]
    gap = max(1, ctx.gap // stride)
    folds = make_folds(len(ys), 3, ctx.cv_scheme, gap)
    if not folds:
        return {}, "HPO skipped (too few rows for inner folds)"

    def obj(trial):
        p = space(trial)
        err = []
        for tr, te in folds:
            m = build(p).fit(As[tr], ys[tr])
            err.append(np.sqrt(np.mean((m.predict(As[te]) - ys[te]) ** 2)))
        return float(np.mean(err))

    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=ctx.seed))
        study.optimize(obj, n_trials=ctx.hpo_trials, show_progress_bar=False)
        return study.best_params, f"Optuna TPE, {ctx.hpo_trials} trials, inner {len(folds)}-fold on {len(ys)} rows"
    except ImportError:
        rng = np.random.default_rng(ctx.seed)
        best, bp = np.inf, {}
        for _ in range(ctx.hpo_trials):
            t = _RandTrial(rng)
            p = space(t)
            v = obj(type("T", (), {"suggest_int": t.suggest_int, "suggest_float": t.suggest_float, "suggest_categorical": t.suggest_categorical})())
            if v < best:
                best, bp = v, p
        return bp, f"random search (optuna not installed), {ctx.hpo_trials} trials"


# ============================================================== candidates
class Candidate:
    def __init__(self, name, family, tier, kind, design, est, space=None, scale=True, cap=None, desc="", fam_key="linear"):
        self.name, self.family, self.tier, self.kind = name, family, tier, kind
        self.design, self.est, self.space, self.scale, self.cap, self.desc, self.fam_key = design, est, space, scale, cap, desc, fam_key

    def fit(self, X: pd.DataFrame, y: np.ndarray, ctx: Ctx, tune: bool = True) -> "Fitted":
        des = self.design(ctx).fit(X, y)
        D = des.transform(X)
        A = np.nan_to_num(D.values.astype(float))
        names = list(D.columns)
        Af, yf = A, y
        if self.cap and len(A) > self.cap:
            sel = np.linspace(0, len(A) - 1, self.cap).astype(int)
            Af, yf = A[sel], y[sel]
        scaler = StandardScaler().fit(Af) if (self.scale and A.shape[1] > 0) else None
        As = scaler.transform(Af) if scaler else Af
        params, hpo_note = {}, "no hyper-parameter search (parameter-free or internally cross-validated)"
        if tune and self.space is not None:
            params, hpo_note = hpo(lambda p: self.est(p, ctx), self.space, As, yf, ctx)
        est = self.est(params, ctx).fit(As, yf)
        f = Fitted(self, des, scaler, est, names, params, hpo_note, ctx)
        f.cand = None  # candidates hold lambdas (not picklable)
        return f


class Fitted:
    def __init__(self, cand, design, scaler, est, names, params, hpo_note, ctx):
        self.cand, self.design, self.scaler, self.est = cand, design, scaler, est
        self.names, self.params, self.hpo_note = names, params, hpo_note
        self.name, self.family, self.kind, self.tier = cand.name, cand.family, cand.kind, cand.tier
        self.ctx_target = ctx.target
        self.X_columns: list[str] = []
        self.steps_per_day = ctx.steps_per_day
        self._surrogate_info: dict | None = None

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        A = np.nan_to_num(self.design.transform(X).values.astype(float))
        if self.scaler is not None:
            A = self.scaler.transform(A)
        return np.asarray(self.est.predict(A), float)

    # ------------------------------------------------------------ complexity
    def n_params(self) -> int:
        e = self.est
        inner = getattr(e, "est", e)
        try:
            if hasattr(inner, "coef_") and self.kind in ("linear",):
                return int(np.sum(np.abs(np.ravel(inner.coef_)) > 1e-12) + 1)
            if isinstance(inner, MeanEst):
                return 1
            if hasattr(inner, "estimators_"):
                return int(sum(t.tree_.node_count for t in np.ravel(inner.estimators_)))
            if hasattr(inner, "booster_"):
                return int(inner.booster_.num_trees() * inner.num_leaves)
            if hasattr(inner, "get_booster"):
                return int(inner.n_estimators * 2 ** inner.max_depth)
            if hasattr(inner, "tree_count_"):
                return int(inner.tree_count_ * 2 ** inner.get_param("depth"))
            if hasattr(inner, "support_"):
                return int(len(inner.support_) + 1)
            if hasattr(inner, "X_train_"):
                return int(len(inner.X_train_) + 3)
            if hasattr(inner, "n_params"):
                return int(inner.n_params)
        except Exception:
            pass
        return int(len(self.names) + 1)

    # ------------------------------------------------------------ formula
    def linear_terms(self) -> tuple[float, list[tuple[str, float]]]:
        inner = getattr(self.est, "est", self.est)
        if isinstance(inner, MeanEst):
            return float(inner.m_), []
        coef, b = np.ravel(inner.coef_).astype(float), float(np.ravel(inner.intercept_)[0])
        if self.scaler is not None:
            s, m = self.scaler.scale_, self.scaler.mean_
            b = b - float(np.sum(coef * m / s))
            coef = coef / s
        return b, list(zip(self.names, coef.tolist()))

    def formula(self, features: list[str], artifact: str = "") -> dict:
        t = self.ctx_target
        if self.kind == "linear":
            b, terms = self.linear_terms()
            nz = [(n, c) for n, c in terms if abs(c) > 1e-12]
            shown = sorted(nz, key=lambda x: -abs(x[1]))[:30]
            body = f"{b:.6g}" + "".join(f" {'+' if c >= 0 else '-'} {abs(c):.6g}·[{n}]" for n, c in shown)
            more = f"  … plus {len(nz) - len(shown)} further terms (see Excel export / artifact)" if len(nz) > len(shown) else ""
            extra = ""
            if self.name.startswith("Piecewise"):
                extra = f"\nChange points: c1={getattr(self.design, 'c1', None)}, c2={getattr(self.design, 'c2', None)} on driver '{getattr(self.design, 'x', '')}'."
            if self.name.startswith("Mean"):
                body, extra = f"{b:.6g}", "\nConstant baseline: the mean of the baseline-period target."
            if self.name.startswith("GAM"):
                extra = "\n[B[x#k]] are cubic B-spline basis functions (5 quantile knots, constant extrapolation) of the named driver."
            if self.name.startswith("Quantile"):
                extra = "\nFitted by minimising the pinball loss at q=0.5 (median regression)."
            return {"text": f"ŷ({t}) = {body}{more}{extra}", "terms": terms, "intercept": b, "kind": "equation"}
        e = getattr(self.est, "est", self.est)
        feats = ", ".join(self.names[:15]) + (" …" if len(self.names) > 15 else "")
        surr_block = ""
        surr_meta = self._surrogate_info
        if surr_meta:
            surr_block = (
                f"\n[{surr_meta['surrogate_banner']} — fidelity R²={surr_meta['fidelity_r2']:.3f} | "
                f"NOT the model's actual prediction formula]\n{surr_meta['approximation_expression']}"
            )
        if hasattr(e, "describe"):
            return {
                "text": f"Architecture: {self.name} with features [{feats}]\n{e.describe(self.names)}\nArtifact: {artifact}{surr_block}",
                "terms": [],
                "kind": "network",
                "is_parametric": False,
                "surrogate": surr_meta,
            }
        n_par = self.n_params()
        if hasattr(e, "estimators_") and "Gradient" not in type(e).__name__:
            txt = f"Architecture: bagged tree ensemble B={len(e.estimators_)} trees ({n_par} nodes total), hyperparameters={self.params}"
        elif hasattr(e, "estimators_"):
            txt = f"Architecture: stagewise boosted trees F0=mean(y), lr={e.learning_rate}, M={e.n_estimators}, max_depth={e.max_depth}, hyperparameters={self.params}"
        elif hasattr(e, "booster_") or hasattr(e, "get_booster") or hasattr(e, "tree_count_"):
            txt = f"Architecture: gradient-boosted decision trees ({n_par} leaves), hyperparameters={self.params}"
        elif hasattr(e, "support_"):
            txt = f"Architecture: RBF kernel SVR (gamma={e._gamma:.4g}, C={e.C}, epsilon={e.epsilon}, {len(e.support_)} support vectors, z standardised)"
        elif hasattr(e, "kernel_"):
            txt = f"Architecture: Gaussian Process ({e.kernel_}, {len(e.X_train_)} training points)"
        else:
            txt = f"Architecture: {type(e).__name__}, hyperparameters={self.params}"
        return {
            "text": f"{txt}\nFeatures ({len(self.names)}): {feats}\nExported model artifact: {artifact}{surr_block}",
            "terms": [],
            "kind": "ensemble/kernel",
            "is_parametric": False,
            "surrogate": surr_meta,
        }


class Blend:
    """Weighted NNLS combination of fitted models whose weights were estimated strictly on inner-fold OOF."""

    def __init__(self, a: Fitted, b: Fitted, target: str, weights: tuple[float, float] = (0.5, 0.5)):
        wa, wb = float(weights[0]), float(weights[1])
        s = wa + wb
        self.wa, self.wb = (wa / s, wb / s) if s > 1e-12 else (0.5, 0.5)
        self.a, self.b = a, b
        self.name, self.family, self.kind, self.tier = f"NNLS_Ensemble({a.name} + {b.name})", "Hybrid ensemble", "blend", 5
        self.names, self.params, self.hpo_note, self.ctx_target = (
            a.names + b.names,
            {"w_a": round(self.wa, 6), "w_b": round(self.wb, 6)},
            "NNLS simplex weights fitted strictly on inner walk-forward OOF",
            target,
        )

    def predict(self, X):
        return self.wa * self.a.predict(X) + self.wb * self.b.predict(X)

    def n_params(self):
        return self.a.n_params() + self.b.n_params()

    def formula(self, features, artifact=""):
        return {
            "text": (
                f"Architecture: Inner-OOF NNLS Ensemble w=({self.wa:.4f}, {self.wb:.4f}) on "
                f"[{self.a.name}, {self.b.name}]\n--- component 1 ({self.wa:.4f}) ---\n"
                f"{self.a.formula(features, artifact)['text']}\n--- component 2 ({self.wb:.4f}) ---\n"
                f"{self.b.formula(features, artifact)['text']}"
            ),
            "terms": [],
            "kind": "blend",
            "is_parametric": False,
            "surrogate": None,
        }


# ============================================================== portfolio
def _have(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def build_portfolio(ctx: Ctx, n: int) -> tuple[list[Candidate], list[dict]]:
    """Return (available candidates, unavailable register entries)."""
    from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
    from sklearn.linear_model import BayesianRidge, ElasticNetCV, LassoCV, LinearRegression, QuantileRegressor, RidgeCV
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.svm import SVR

    seed, nj = ctx.seed, ctx.n_jobs
    L = "Linear / statistical"
    cands: list[Candidate] = []
    missing: list[dict] = []
    kf = TimeSeriesSplit(n_splits=3)
    alphas = np.logspace(-3, 3, 13)

    cands += [
        Candidate("Mean baseline", L, 1, "linear", lambda c: ColsDesign(only=[]), lambda p, c: MeanEst(), scale=False, desc="constant mean (null model)"),
        Candidate("Degree-day regression", L, 1, "linear", lambda c: ColsDesign(dd_only=True), lambda p, c: LinearRegression(), desc="y = b0 + b1·HDD + b2·CDD"),
        Candidate("Multiple linear regression (MLR)", L, 1, "linear", lambda c: ColsDesign(), lambda p, c: LinearRegression()),
        Candidate("Ridge regression", L, 1, "linear", lambda c: ColsDesign(ix=True), lambda p, c: RidgeCV(alphas=alphas)),
        Candidate("Lasso regression", L, 1, "linear", lambda c: ColsDesign(ix=True), lambda p, c: LassoCV(cv=kf, random_state=seed, max_iter=20000)),
        Candidate("Elastic Net", L, 1, "linear", lambda c: ColsDesign(ix=True), lambda p, c: ElasticNetCV(cv=kf, l1_ratio=[0.2, 0.5, 0.8], random_state=seed, max_iter=20000)),
        Candidate("Polynomial regression (deg 2)", L, 2, "linear", lambda c: PolyDesign(3), lambda p, c: RidgeCV(alphas=alphas)),
        Candidate("Piecewise / change-point regression", L, 2, "linear", lambda c: PiecewiseDesign(c.raw_names, c.temp_col), lambda p, c: LinearRegression()),
        Candidate("TOWT (time-of-week & temperature)", L, 2, "linear", lambda c: TOWTDesign(c), lambda p, c: RidgeCV(alphas=np.logspace(-4, 1, 6))),
        Candidate("GAM (B-spline additive)", L, 2, "linear", lambda c: SplineDesign(4), lambda p, c: RidgeCV(alphas=alphas)),
        Candidate("Bayesian ridge", L, 1, "linear", lambda c: ColsDesign(), lambda p, c: BayesianRidge()),
        Candidate("Quantile regression (median)", L, 2, "linear", lambda c: ColsDesign(), lambda p, c: QuantileRegressor(quantile=0.5, alpha=0.0, solver="highs")),
    ]
    T = "Tree-based"
    d = lambda c: AllDesign()  # noqa: E731
    cands += [
        Candidate("Random forest", T, 4, "tree", d, lambda p, c: RandomForestRegressor(**{**dict(n_estimators=120, max_depth=14, min_samples_leaf=3, max_features=0.8, n_jobs=nj, random_state=seed), **p}),
                  space=lambda t: dict(max_depth=t.suggest_int("max_depth", 4, 20), min_samples_leaf=t.suggest_int("min_samples_leaf", 1, 10), max_features=t.suggest_float("max_features", 0.4, 1.0)), scale=False, fam_key="tree"),
        Candidate("Extra trees", T, 4, "tree", d, lambda p, c: ExtraTreesRegressor(**{**dict(n_estimators=120, max_depth=14, min_samples_leaf=3, max_features=0.9, n_jobs=nj, random_state=seed), **p}),
                  space=lambda t: dict(max_depth=t.suggest_int("max_depth", 4, 20), min_samples_leaf=t.suggest_int("min_samples_leaf", 1, 10), max_features=t.suggest_float("max_features", 0.4, 1.0)), scale=False, fam_key="tree"),
        Candidate("Gradient boosting", T, 4, "tree", d, lambda p, c: GradientBoostingRegressor(**{**dict(n_estimators=150, learning_rate=0.08, max_depth=3, subsample=0.8, random_state=seed), **p}),
                  space=lambda t: dict(n_estimators=t.suggest_int("n_estimators", 80, 300), learning_rate=t.suggest_float("learning_rate", 0.02, 0.2, log=True), max_depth=t.suggest_int("max_depth", 2, 5), subsample=t.suggest_float("subsample", 0.6, 1.0)), scale=False, fam_key="tree"),
    ]
    if _have("xgboost"):
        from xgboost import XGBRegressor
        cands.append(Candidate("XGBoost", T, 4, "tree", d, lambda p, c: XGBRegressor(**{**dict(n_estimators=200, learning_rate=0.06, max_depth=4, subsample=0.8, colsample_bytree=0.8, n_jobs=nj, random_state=seed, verbosity=0), **p}),
                               space=lambda t: dict(n_estimators=t.suggest_int("n_estimators", 100, 400), learning_rate=t.suggest_float("learning_rate", 0.02, 0.2, log=True), max_depth=t.suggest_int("max_depth", 3, 8), subsample=t.suggest_float("subsample", 0.6, 1.0)), scale=False, fam_key="tree"))
    else:
        missing.append({"model": "XGBoost", "reason": "package 'xgboost' not installed in this environment"})
    if _have("lightgbm"):
        from lightgbm import LGBMRegressor
        cands.append(Candidate("LightGBM", T, 4, "tree", d, lambda p, c: LGBMRegressor(**{**dict(n_estimators=200, learning_rate=0.06, num_leaves=24, min_child_samples=10, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, n_jobs=nj, random_state=seed, verbose=-1), **p}),
                               space=lambda t: dict(n_estimators=t.suggest_int("n_estimators", 100, 400), learning_rate=t.suggest_float("learning_rate", 0.02, 0.2, log=True), num_leaves=t.suggest_int("num_leaves", 8, 64), min_child_samples=t.suggest_int("min_child_samples", 5, 50)), scale=False, fam_key="tree"))
    else:
        missing.append({"model": "LightGBM", "reason": "package 'lightgbm' not installed in this environment"})
    if _have("catboost"):
        from catboost import CatBoostRegressor
        cands.append(Candidate("CatBoost", T, 4, "tree", d, lambda p, c: CatBoostRegressor(**{**dict(iterations=250, depth=6, learning_rate=0.06, random_seed=seed, verbose=0, allow_writing_files=False, thread_count=nj), **p}),
                               space=lambda t: dict(iterations=t.suggest_int("iterations", 100, 400), depth=t.suggest_int("depth", 4, 8), learning_rate=t.suggest_float("learning_rate", 0.02, 0.2, log=True)), scale=False, fam_key="tree"))
    else:
        missing.append({"model": "CatBoost", "reason": "package 'catboost' not installed in this environment"})

    K = "Kernel / probabilistic"
    cands += [
        Candidate("Support vector regression (RBF)", K, 4, "kernel", d, lambda p, c: YScaled(SVR(**{**dict(C=10.0, epsilon=0.05, gamma="scale"), **p})),
                  space=lambda t: dict(C=t.suggest_float("C", 0.1, 100, log=True), epsilon=t.suggest_float("epsilon", 0.01, 0.3), gamma=t.suggest_float("gamma", 1e-3, 1.0, log=True)), cap=2500, fam_key="kernel"),
        Candidate("Gaussian process regression", K, 4, "kernel", lambda c: ColsDesign(timeint=True),
                  lambda p, c: GaussianProcessRegressor(kernel=ConstantKernel(1.0) * RBF(length_scale=2.0) + WhiteKernel(0.1), normalize_y=True, random_state=seed, n_restarts_optimizer=0), cap=500, fam_key="kernel"),
    ]
    N = "Neural network"
    ep = ctx.nn_max_epochs
    cands.append(Candidate("MLP (feed-forward NN)", N, 5, "nn", d, lambda p, c: nnmod.SkMLP(hidden=(48, 24), max_epochs=ep, seed=seed), fam_key="nn"))
    if nnmod.torch_available():
        h = 24 if n < 2000 else 32
        for arch in nnmod.TORCH_ARCHS:
            cands.append(Candidate(f"{nnmod.ARCH_LABEL[arch]} (PyTorch)", N, 5, "nn", d,
                                   (lambda a: (lambda p, c: nnmod.TorchSeqRegressor(a, seq_len=c.seq_len, hidden=h, max_epochs=ep, seed=seed)))(arch), fam_key="nn"))
    else:
        for arch in nnmod.TORCH_ARCHS:
            missing.append({"model": f"{nnmod.ARCH_LABEL[arch]} (PyTorch)", "reason": "PyTorch is not installed in this environment (pip install torch)"})
    for m in ("TFT", "PatchTST / Informer"):
        missing.append({"model": m, "reason": "optional architecture not implemented in v3.0 (marked optional in the specification)"})
    if not _have("prophet"):
        pass
    return cands, missing


# ============================================================== evaluation
def plausibility(f, X: pd.DataFrame, y: np.ndarray) -> tuple[float, list[str]]:
    score, notes = 1.0, []
    try:
        p = f.predict(X)
        if y.min() >= 0:
            neg = float((p < 0).mean())
            if neg > 0:
                score -= min(0.5, neg * 5)
                notes.append(f"{neg:.1%} negative predictions for a non-negative quantity")
        if np.nanmax(p) > 3 * max(np.nanmax(y), 1e-9) and np.nanmax(y) > 0:
            score -= 0.25
            notes.append("predictions far outside the observed range")
        scale = max(abs(float(np.mean(y))), 1e-9)
        for c in [c for c in X.columns if c.startswith(("HDD", "CDD"))]:
            X2 = X.copy()
            X2[c] = X2[c] + max(float(X[c].std()), 1e-6)
            delta = float(np.mean(f.predict(X2) - p))
            if delta < -0.01 * scale:
                score -= 0.25
                notes.append(f"consumption decreases when {c} increases (physically implausible)")
    except Exception as exc:  # pragma: no cover
        notes.append(f"plausibility check failed: {exc}")
    return max(0.0, score), notes


def evaluate_candidate(cand: Candidate, X: pd.DataFrame, y: np.ndarray, folds, ctx: Ctx) -> dict:
    from ..mining.clustering import assert_no_cluster_labels_in_model_inputs

    assert_no_cluster_labels_in_model_inputs(X)
    t0 = time.time()
    n = len(y)
    oof = np.full(n, np.nan)
    fold_cv = []
    for tr, te in folds:
        if ctx.cancel():
            raise InterruptedError("cancelled")
        # Fit fold-local medians exclusively on training rows (VAL-4 leakage safety)
        X_tr_raw = X.iloc[tr]
        fold_med = X_tr_raw.median(numeric_only=True).fillna(0.0)
        X_tr_clean = X_tr_raw.fillna(fold_med).fillna(0.0)
        X_te_clean = X.iloc[te].fillna(fold_med).fillna(0.0)
        m = cand.fit(X_tr_clean, y[tr], ctx, tune=True)
        oof[te] = m.predict(X_te_clean)
        fold_cv.append(metrics(y[te], oof[te])["cvrmse"])
    cov = ~np.isnan(oof)
    if cov.sum() < 5:
        raise RuntimeError("no out-of-fold predictions produced")
    full_med = X.median(numeric_only=True).fillna(0.0)
    X_full_clean = X.fillna(full_med).fillna(0.0)
    final = cand.fit(X_full_clean, y, ctx, tune=True)
    ptrain = final.predict(X_full_clean)
    if final.kind != "linear":
        try:
            from ..models.top5 import fit_labelled_diagnostic_surrogate

            final._surrogate_info = fit_labelled_diagnostic_surrogate(X_full_clean, ptrain, seed=ctx.seed).to_dict()
        except Exception:
            final._surrogate_info = None
    m = metrics(y[cov], oof[cov])
    tm = metrics(y, ptrain)
    k = final.n_params()
    rss = float(np.sum((y[cov] - oof[cov]) ** 2))
    nc = int(cov.sum())
    ll = nc * math.log(max(rss, 1e-12) / nc)
    res = {
        "name": cand.name, "family": cand.family, "tier": cand.tier, "kind": cand.kind, "oof": oof, "metrics": m,
        "train_metrics": tm, "fold_cvrmse": fold_cv, "robustness": float(np.std(fold_cv) / max(np.mean(fold_cv), 1e-9)) if len(fold_cv) > 1 else 0.0,
        "n_params": k, "aic": ll + 2 * k, "bic": ll + k * math.log(nc), "model": final, "hpo": final.hpo_note,
        "params": final.params, "seconds": time.time() - t0, "diagnostics": diagnostics(y[cov], oof[cov]),
    }
    q = np.quantile((y[cov] - oof[cov]), [0.025, 0.975])
    res["resid_q"] = (float(q[0]), float(q[1]))
    res["plausibility"], res["plaus_notes"] = plausibility(final, X, y)
    e = getattr(final.est, "est", final.est)
    if hasattr(e, "history_"):
        res["history"] = e.history_
    return res
