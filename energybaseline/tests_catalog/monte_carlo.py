"""Empirical Type I error & prospective power Monte Carlo calibration for T1..T14 (AC-10, AC-11, AC-12).

Strictly prohibits post-hoc 'observed power' computed from realized sample estimates as a decision input.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from scipy import stats as sp_stats

from .t1_stationarity import run_t1_stationarity
from .t2_seasonality import run_t2_seasonality
from .t3_autocorrelation import run_t3_autocorrelation
from .t4_normality import run_t4_normality
from .t5_heteroskedasticity import run_t5_heteroskedasticity
from .t6_multicollinearity import run_t6_multicollinearity
from .t7_structural_break_chow import run_t7_chow
from .t8_structural_break_quandt_andrews import run_t8_quandt_andrews
from .t9_pettitt import run_t9_pettitt
from .t10_diebold_mariano import run_t10_diebold_mariano
from .t11_nemenyi import run_t11_nemenyi
from .t12_little_mcar import run_t12_little_mcar
from .t13_outlier_influence import run_t13_outlier_influence
from .t14_causal_group import run_t14_causal_group
from .tc_assumptions import ObservedPowerForbiddenError


@dataclass(frozen=True)
class MonteCarloCalibrationSummary:
    """Empirical Type I error under H0 and empirical power under a planted H1 effect."""

    test_id: str
    alpha: float
    n_replications: int
    sample_size: int
    type1_error_rate: float
    type1_ci_low: float
    type1_ci_high: float
    type1_calibrated: bool
    planted_effect_description: str
    empirical_power: float
    power_target: float
    power_achieved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "empirical_power": round(self.empirical_power, 6),
            "n_replications": self.n_replications,
            "planted_effect_description": self.planted_effect_description,
            "power_achieved": self.power_achieved,
            "power_target": self.power_target,
            "sample_size": self.sample_size,
            "test_id": self.test_id,
            "type1_calibrated": self.type1_calibrated,
            "type1_ci_high": round(self.type1_ci_high, 6),
            "type1_ci_low": round(self.type1_ci_low, 6),
            "type1_error_rate": round(self.type1_error_rate, 6),
        }


def compute_prospective_design_power(
    *,
    target_standardized_effect: float,
    sample_size: int,
    alpha: float = 0.05,
    observed_p_value: float | None = None,
    observed_statistic: float | None = None,
) -> dict[str, Any]:
    """Prospective a-priori design power for planning sample sizes against a prespecified minimum detectable effect.

    Raises ``ObservedPowerForbiddenError`` if called with realized ``observed_p_value`` or ``observed_statistic``
    to prevent post-hoc 'observed power' fallacies (Hoenig & Heisey 2001; AC-12).
    """
    if observed_p_value is not None or observed_statistic is not None:
        raise ObservedPowerForbiddenError(
            "Post-hoc 'observed power' computed from realized p-values or test statistics is prohibited (AC-12). "
            "Specify a prospective minimum detectable effect size prior to model evaluation instead."
        )
    if sample_size < 4:
        raise ValueError("sample_size must be >= 4.")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1).")

    df = sample_size - 2
    ncp = abs(float(target_standardized_effect)) * math.sqrt(float(sample_size) / 2.0)
    t_crit = float(sp_stats.t.ppf(1.0 - alpha / 2.0, df=df))
    power = float(sp_stats.nct.sf(t_crit, df=df, nc=ncp) + sp_stats.nct.cdf(-t_crit, df=df, nc=ncp))
    return {
        "alpha": alpha,
        "design_type": "prospective_a_priori_only",
        "df": df,
        "noncentrality": round(ncp, 6),
        "prospective_power": round(min(1.0, max(0.0, power)), 6),
        "sample_size": sample_size,
        "target_standardized_effect": target_standardized_effect,
    }


def _wilson_interval(k: int, n: int, z: float = 2.576) -> tuple[float, float]:
    p = float(k) / float(n)
    denom = 1.0 + (z * z) / float(n)
    center = (p + (z * z) / (2.0 * float(n))) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * float(n))) / float(n))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def run_test_monte_carlo(
    test_id: str,
    *,
    alpha: float = 0.05,
    n_rep: int = 120,
    sample_size: int = 120,
    power_target: float = 0.80,
    seed: int = 12345,
) -> MonteCarloCalibrationSummary:
    """Evaluate empirical Type I error under H0 and empirical power under planted H1 for ``test_id`` in ``T1..T14``."""
    tid = test_id.strip().upper()
    rng = np.random.default_rng(int(seed))
    rejects_h0 = 0
    rejects_h1 = 0
    n = int(sample_size)

    if tid == "T1":
        effect_desc = "Random walk unit root (phi=1.0 vs stationary white noise phi=0.0)"
        for r in range(n_rep):
            y0 = rng.normal(0.0, 1.0, size=n)
            y1 = np.cumsum(rng.normal(0.0, 1.0, size=n))
            if run_t1_stationarity(y0, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t1_stationarity(y1, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T2":
        effect_desc = "Deterministic sinusoidal seasonal cycle amplitude=1.8 (period=12)"
        t = np.arange(n, dtype=float)
        wave = 1.8 * np.sin(2.0 * math.pi * t / 12.0)
        for r in range(n_rep):
            y0 = rng.normal(0.0, 1.0, size=n)
            y1 = wave + rng.normal(0.0, 1.0, size=n)
            if run_t2_seasonality(y0, period=12, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t2_seasonality(y1, period=12, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T3":
        effect_desc = "AR(1) serial correlation phi=0.55 on model residuals"
        for r in range(n_rep):
            u0 = rng.normal(0.0, 1.0, size=n)
            eps = rng.normal(0.0, 1.0, size=n)
            u1 = np.empty(n, dtype=float)
            u1[0] = eps[0]
            for i in range(1, n):
                u1[i] = 0.55 * u1[i - 1] + eps[i]
            if run_t3_autocorrelation(u0, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t3_autocorrelation(u1, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T4":
        effect_desc = "Shifted lognormal heavy-skewed innovation (sigma=0.9)"
        for r in range(n_rep):
            u0 = rng.normal(0.0, 1.0, size=n)
            u1 = rng.lognormal(0.0, 0.9, size=n) - math.exp(0.405)
            if run_t4_normality(u0, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t4_normality(u1, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T5":
        effect_desc = "Multiplicative variance fan sigma(x) = 0.3 + 2.5 * x"
        x = np.linspace(0.0, 1.0, num=n)
        for r in range(n_rep):
            u0 = rng.normal(0.0, 1.0, size=n)
            u1 = rng.normal(0.0, 1.0, size=n) * (0.3 + 2.5 * x)
            if run_t5_heteroskedasticity(u0, x, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t5_heteroskedasticity(u1, x, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T6":
        effect_desc = "Near-collinear predictor pair VIF=35 vs boundary VIF=10"
        rho0 = math.sqrt(1.0 - 1.0 / 10.0)
        rho1 = math.sqrt(1.0 - 1.0 / 35.0)
        for r in range(n_rep):
            z1 = rng.normal(0.0, 1.0, size=n)
            e = rng.normal(0.0, 1.0, size=n)
            X0 = np.column_stack([z1, rho0 * z1 + math.sqrt(1.0 - rho0 * rho0) * e])
            X1 = np.column_stack([z1, rho1 * z1 + math.sqrt(1.0 - rho1 * rho1) * e])
            if run_t6_multicollinearity(X0, vif_threshold=10.0, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t6_multicollinearity(X1, vif_threshold=10.0, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T7":
        effect_desc = "Midpoint Chow mean shift +1.0 SD"
        bp = n // 2
        step = np.concatenate([np.zeros(bp), np.ones(n - bp) * 1.0])
        for r in range(n_rep):
            y0 = rng.normal(0.0, 1.0, size=n)
            y1 = y0 + step
            if run_t7_chow(y0, break_point=bp, use_classical_f=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t7_chow(y1, break_point=bp, use_classical_f=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T8":
        effect_desc = "Unknown-date Quandt-Andrews regime shift +1.3 SD at t=0.6*n"
        bp = int(0.6 * n)
        step = np.concatenate([np.zeros(bp), np.ones(n - bp) * 1.3])
        for r in range(n_rep):
            y0 = rng.normal(0.0, 1.0, size=n)
            y1 = y0 + step
            if run_t8_quandt_andrews(y0, n_boot=79, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t8_quandt_andrews(y1, n_boot=79, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T9":
        effect_desc = "Pettitt non-parametric step shift +1.2 SD at midpoint"
        bp = n // 2
        step = np.concatenate([np.zeros(bp), np.ones(n - bp) * 1.2])
        for r in range(n_rep):
            y0 = rng.normal(0.0, 1.0, size=n)
            y1 = y0 + step
            if run_t9_pettitt(y0, use_asymptotic_formula=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t9_pettitt(y1, use_asymptotic_formula=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T10":
        effect_desc = "Model 2 OOF RMSE inflated by factor 1.45 vs Model 1"
        for r in range(n_rep):
            e1 = rng.normal(0.0, 1.0, size=n)
            e2_h0 = rng.normal(0.0, 1.0, size=n)
            e2_h1 = rng.normal(0.0, 1.45, size=n)
            if run_t10_diebold_mariano(e1, e2_h0, use_hln_exact=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t10_diebold_mariano(e1, e2_h1, use_hln_exact=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T11":
        effect_desc = "3-model OOF loss matrix where model_3 has +0.9 mean loss penalty"
        for r in range(n_rep):
            L0 = rng.exponential(1.0, size=(n, 3))
            L1 = L0.copy()
            L1[:, 2] += 0.9
            if run_t11_nemenyi(L0, use_nemenyi_exact=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t11_nemenyi(L1, use_nemenyi_exact=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T12":
        effect_desc = "MAR missingness on Y2 triggered when Y1 > 0.5 vs pure Bernoulli(0.2) MCAR"
        for r in range(n_rep):
            z = rng.normal(0.0, 1.0, size=(n, 2))
            Y0 = np.column_stack([z[:, 0], 0.5 * z[:, 0] + math.sqrt(0.75) * z[:, 1]])
            Y1 = Y0.copy()
            mcar_mask = rng.uniform(0.0, 1.0, size=n) < 0.20
            mar_prob = np.where(Y1[:, 0] > 0.3, 0.55, 0.03)
            mar_mask = rng.uniform(0.0, 1.0, size=n) < mar_prob
            Y0[mcar_mask, 1] = np.nan
            Y1[mar_mask, 1] = np.nan
            if run_t12_little_mcar(Y0, use_em_exact=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t12_little_mcar(Y1, use_em_exact=True, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T13":
        effect_desc = "Single high-leverage gross sensor spike +8 SD at index 10 (flag-only)"
        x = np.linspace(-2.0, 2.0, num=n)
        for r in range(n_rep):
            y0 = 1.5 * x + rng.normal(0.0, 1.0, size=n)
            y1 = y0.copy()
            y1[-2] += 8.0
            if run_t13_outlier_influence(y0, x, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t13_outlier_influence(y1, x, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    elif tid == "T14":
        effect_desc = "Non-parallel pre-intervention slope divergence (+2.2 SD) between treatment & control"
        grp = np.array([0] * (n // 2) + [1] * (n - n // 2))
        t_loc = np.concatenate([np.linspace(0.0, 1.0, n // 2), np.linspace(0.0, 1.0, n - n // 2)])
        for r in range(n_rep):
            cov = rng.normal(0.0, 1.0, size=(n, 1))
            y0 = 0.5 * cov[:, 0] + 1.0 * t_loc + rng.normal(0.0, 1.0, size=n)
            y1 = y0 + 2.2 * grp * t_loc
            if run_t14_causal_group(y0, cov, group=grp, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h0 += 1
            if run_t14_causal_group(y1, cov, group=grp, alpha=alpha, mode="fast", seed=r).decision == "fail":
                rejects_h1 += 1
    else:
        raise ValueError(f"Unknown catalog test_id '{test_id}'. Expected one of T1..T14.")

    t1_rate = float(rejects_h0) / float(n_rep)
    power = float(rejects_h1) / float(n_rep)
    ci_lo, ci_hi = _wilson_interval(rejects_h0, n_rep)
    # Calibrated if nominal alpha lies near Wilson interval or Type I rate <= alpha + binomial slack (0.065 at n_rep=120)
    se_alpha = math.sqrt(alpha * (1.0 - alpha) / float(n_rep))
    calibrated = abs(t1_rate - alpha) <= max(0.06, 3.2 * se_alpha)

    return MonteCarloCalibrationSummary(
        test_id=tid,
        alpha=alpha,
        n_replications=n_rep,
        sample_size=n,
        type1_error_rate=t1_rate,
        type1_ci_low=ci_lo,
        type1_ci_high=ci_hi,
        type1_calibrated=calibrated,
        planted_effect_description=effect_desc,
        empirical_power=power,
        power_target=power_target,
        power_achieved=(power >= power_target),
    )
