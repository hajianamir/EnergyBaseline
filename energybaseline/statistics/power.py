"""Prospective power/sample-size designs; never derive effect size from observed outcomes."""

from __future__ import annotations

import math

import numpy as np
from statsmodels.stats.power import FTestAnovaPower, NormalIndPower, TTestIndPower, TTestPower

from .common import alternative, fields, integer, number


def prospective(parameters, alpha):
    fields(parameters, ("design", "effect_size", "mode"), ("alternative", "ratio", "groups", "power", "n_per_group"))
    design = parameters["design"]
    if design not in ("one_sample_t", "independent_t", "independent_z", "balanced_anova"):
        raise ValueError("Unsupported prospective design.")
    effect = number(parameters["effect_size"], "effect_size", 0.000001, 10)
    alt = alternative(parameters)
    if design == "balanced_anova" and alt != "two-sided":
        raise ValueError("Balanced ANOVA uses its nondirectional omnibus design.")
    signed = -effect if alt == "less" else effect
    backend_alt = {"two-sided": "two-sided", "greater": "larger", "less": "smaller"}[alt]
    ratio = number(parameters.get("ratio", 1), "ratio", 0.01, 100)
    if design not in ("independent_t", "independent_z") and "ratio" in parameters:
        raise ValueError("An allocation ratio applies only to independent two-group designs.")
    groups = integer(parameters.get("groups", 2), "groups", 2, 20)
    if design != "balanced_anova" and "groups" in parameters:
        raise ValueError("Group count applies only to balanced ANOVA.")
    mode = parameters["mode"]
    if mode == "solve_n":
        if "power" not in parameters or "n_per_group" in parameters:
            raise ValueError("Sample-size solving requires prospective desired power, not observed n.")
        desired = number(parameters["power"], "power", 0.5, 0.999)
    elif mode == "evaluate":
        if "n_per_group" not in parameters or "power" in parameters:
            raise ValueError("Prospective evaluation requires n_per_group and an externally specified effect.")
        desired = None
    else:
        raise ValueError("Only prospective solve_n or evaluate modes are supported; observed power is forbidden.")
    solver = {
        "one_sample_t": TTestPower,
        "independent_t": TTestIndPower,
        "independent_z": NormalIndPower,
        "balanced_anova": FTestAnovaPower,
    }[design]()
    if mode == "solve_n":
        if design == "balanced_anova":
            estimate = solver.solve_power(effect_size=effect, nobs=None, alpha=alpha, power=desired, k_groups=groups)
            n = math.ceil(float(estimate) / groups)
        else:
            kwargs = dict(effect_size=signed, alpha=alpha, power=desired, alternative=backend_alt)
            if design != "one_sample_t":
                kwargs["ratio"] = ratio
            n = math.ceil(
                float(
                    solver.solve_power(nobs=None, **kwargs)
                    if design == "one_sample_t"
                    else solver.solve_power(nobs1=None, **kwargs)
                )
            )
        integer(n, "solved n_per_group", 2, 1_000_000)
    else:
        n = integer(parameters["n_per_group"], "n_per_group", 2, 1_000_000)
    if design == "balanced_anova":
        sizes = [n] * groups
        attained = solver.power(effect, n * groups, alpha, k_groups=groups)
    elif design == "one_sample_t":
        sizes = [n]
        attained = solver.power(signed, n, alpha, alternative=backend_alt)
    else:
        n2 = max(2, math.ceil(n * ratio))
        integer(n2, "second-group sample size", 2, 1_000_000)
        sizes = [n, n2]
        attained = solver.power(signed, n, alpha, ratio=n2 / n, alternative=backend_alt)
    if not np.isfinite(attained) or not 0 <= attained <= 1:
        raise ValueError("Prospective design solver failed.")
    if desired is not None and attained + 1e-10 < desired:
        raise ValueError("Rounded integer design failed its prospective power target.")
    return {
        "design": design,
        "mode": mode,
        "group_sample_sizes": sizes,
        "attained_power": float(attained),
        "desired_power": desired,
        "alpha": alpha,
        "effect_source": "externally specified prospective standardized effect",
        "observed_power": False,
        "assumptions": "Design-model independence, distribution and effect-size assumptions are caller responsibilities.",
    }
