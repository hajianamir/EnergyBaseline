"""Balance/overlap, pre-trends, kernel weights, DID and treatment-placebo refutation.

Observational effects require untestable identification assumptions. Diagnostics
can refute a design but cannot establish causality or authorize a baseline change.
"""

from __future__ import annotations
import numpy as np
import statsmodels.api as sm
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import rbf_kernel
from ..uq.calibration import vector, confidence


class CausalBackendUnavailable(RuntimeError):
    """Optional causal backend absent; never replaced with a fabricated effect."""


def kernel_balance(X, treatment):
    """Entropy-regularized RBF mean-embedding balancing of controls to treated."""
    X = np.asarray(X, dtype=float)
    g = np.asarray(treatment, dtype=int)
    if X.ndim != 2 or len(X) != len(g) or not np.isfinite(X).all() or set(g) != {0, 1}:
        raise ValueError("Finite covariates and binary treatment required")
    Z = StandardScaler().fit_transform(X)
    control, treated = Z[g == 0], Z[g == 1]
    K = rbf_kernel(control, control, gamma=1 / max(1, X.shape[1]))
    target = rbf_kernel(control, treated, gamma=1 / max(1, X.shape[1])).mean(axis=1)
    n = len(control)

    def objective(w):
        return float(w @ K @ w - 2 * w @ target + 0.01 * np.sum(w * np.log(np.maximum(w, 1e-15) * n)))

    fit = minimize(
        objective,
        np.full(n, 1 / n),
        method="SLSQP",
        bounds=[(1e-12, 1.0)] * n,
        constraints={"type": "eq", "fun": lambda w: w.sum() - 1},
        options={"maxiter": 300, "ftol": 1e-10},
    )
    if not fit.success:
        raise ValueError("Kernel balancing did not converge")
    weights = fit.x / fit.x.sum()
    pooled = np.sqrt((np.var(control, axis=0) + np.var(treated, axis=0)) / 2 + 1e-12)
    smd = (treated.mean(axis=0) - weights @ control) / pooled
    return {
        "control_weights": weights.tolist(),
        "post_balance_smd": smd.tolist(),
        "effective_control_n": float(1 / np.sum(weights**2)),
        "objective": float(fit.fun),
    }


def run_causal_mv(outcome, covariates, treatment, post, time, *, tcp, level=0.95, seed=42, refutations=99):
    """Repeated-observation DID with HAC; caller supplies aligned common time.

    Outcome contrasts are reported only if balance, pre-trends and overlap pass.
    Kernel weights are diagnostics rather than an undisclosed change of estimand.
    """
    tcp.assert_accepted()
    level = confidence(level)
    y = vector(outcome, minimum=40)
    X = np.asarray(covariates, dtype=float)
    g = np.asarray(treatment)
    p = np.asarray(post)
    t = vector(time)
    if (
        X.ndim != 2
        or len(X) != len(y)
        or not np.isfinite(X).all()
        or any(v.shape != y.shape for v in (g, p, t))
        or set(g) != {0, 1}
        or set(p) != {0, 1}
    ):
        raise ValueError("Aligned binary treatment/post and finite covariates required")
    if refutations < 19:
        raise ValueError("At least 19 placebo draws required")
    pre = p == 0
    if any(np.sum((g == a) & (p == b)) < 10 for a in (0, 1) for b in (0, 1)):
        raise ValueError("Each treatment-period cell needs ten observations")
    Z = StandardScaler().fit_transform(X[pre])
    gp = g[pre]
    prop = LogisticRegression(C=1.0, random_state=seed).fit(Z, gp).predict_proba(Z)[:, 1]
    overlap = float(np.mean((prop > 0.1) & (prop < 0.9)))
    pooled = np.sqrt((np.var(Z[gp == 1], axis=0) + np.var(Z[gp == 0], axis=0)) / 2 + 1e-12)
    smd = (Z[gp == 1].mean(axis=0) - Z[gp == 0].mean(axis=0)) / pooled
    tp = t[pre]
    tp = (tp - tp.min()) / max(float(np.ptp(tp)), 1.0)
    trend = sm.OLS(y[pre], np.column_stack([np.ones(pre.sum()), gp, tp, gp * tp, X[pre]])).fit(
        cov_type="HAC", cov_kwds={"maxlags": 3}
    )
    trends_p = float(trend.pvalues[3])
    kb = kernel_balance(X[pre], gp)
    design = np.column_stack([np.ones(len(y)), g, p, g * p, t, X])
    if np.linalg.matrix_rank(design) != design.shape[1]:
        raise ValueError("Causal design is rank deficient")
    order = np.argsort(t, kind="stable")
    fit = sm.OLS(y[order], design[order]).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    effect = float(fit.params[3])
    ci = fit.conf_int(alpha=1 - level)[3].tolist()
    # Permute the treatment assignment, preserve period and covariates. This is a
    # placebo diagnostic, not randomization inference for an observational study.
    rng = np.random.default_rng(seed)
    placebos = []
    for _ in range(refutations):
        placebo_g = rng.permutation(g)
        d = np.column_stack([np.ones(len(y)), placebo_g, p, placebo_g * p, t, X])
        placebos.append(float(np.linalg.lstsq(d, y, rcond=None)[0][3]))
    placebo_p = float((1 + np.sum(np.abs(placebos) >= abs(effect))) / (refutations + 1))
    eligible = bool(overlap >= 0.9 and np.max(abs(smd)) <= 0.25 and trends_p >= tcp.alpha)
    return {
        "label": "possible savings / non-routine event",
        "eligible": eligible,
        "effect": effect if eligible else None,
        "effect_interval": ci if eligible else None,
        "diagnostic_effect": effect,
        "pretrend_p": trends_p,
        "overlap_fraction": overlap,
        "pre_balance_smd": smd.tolist(),
        "kernel_balance": kb,
        "refutation": {
            "method": "permuted treatment placebo",
            "draws": refutations,
            "placebo_p": placebo_p,
            "placebo_mean": float(np.mean(placebos)),
        },
        "assumptions": [
            "parallel untreated trends",
            "no unmeasured time-varying confounding",
            "no interference",
            "stable group composition",
        ],
        "model_mutated": False,
    }


def optional_backend_effect(data, *, treatment, outcome, common_causes, tcp, backend="dowhy", seed=42):
    """Real optional DoWhy/EconML adapters with refutation; absent dependencies skip."""
    tcp.assert_accepted()
    if backend not in ("dowhy", "econml"):
        raise ValueError("Unknown causal backend")
    try:
        if backend == "dowhy":
            from dowhy import CausalModel
        else:
            from econml.dml import LinearDML
    except ImportError as exc:
        raise CausalBackendUnavailable(f"Optional {backend} backend is not installed") from exc
    if backend == "dowhy":
        model = CausalModel(data=data, treatment=treatment, outcome=outcome, common_causes=common_causes)
        identified = model.identify_effect(proceed_when_unidentifiable=False)
        effect = model.estimate_effect(identified, method_name="backdoor.linear_regression")
        refutation = model.refute_estimate(identified, effect, method_name="random_common_cause", random_seed=seed)
        return {
            "backend": backend,
            "effect": float(effect.value),
            "refutation": str(refutation),
            "label": "possible savings / non-routine event",
        }
    from sklearn.ensemble import RandomForestRegressor

    X = data[common_causes].to_numpy()
    y = data[outcome].to_numpy()
    t = data[treatment].to_numpy()
    est = LinearDML(
        model_y=RandomForestRegressor(n_estimators=50, random_state=seed),
        model_t=RandomForestRegressor(n_estimators=50, random_state=seed),
        random_state=seed,
    )
    est.fit(y, t, X=X)
    effect = float(np.mean(est.effect(X)))
    placebo = LinearDML(random_state=seed)
    placebo.fit(y, np.random.default_rng(seed).permutation(t), X=X)
    return {
        "backend": backend,
        "effect": effect,
        "refutation_placebo_effect": float(np.mean(placebo.effect(X))),
        "label": "possible savings / non-routine event",
    }
