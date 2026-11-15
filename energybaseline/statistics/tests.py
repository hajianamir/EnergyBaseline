"""Fixed statistical primitives; execute only through the input/plan-bound gated runner."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from ..workbench.common import binary
from .common import (
    preregistered_primitive,
    alternative,
    columns,
    fields,
    grouped,
    integer,
    interval,
    nonconstant,
    number,
    result,
    vector,
)


@preregistered_primitive
def normality(frame, names, parameters, level):
    method = parameters["_method"]
    p = {k: v for k, v in parameters.items() if k != "_method"}
    if len(names) != 1:
        raise ValueError("Normality requires one observed column.")
    x = vector(frame, names, 5).ravel()
    nonconstant(x)
    if method == "ks_fixed_normal":
        fields(p, ("location", "scale"))
        location = number(p["location"], "location")
        scale = number(p["scale"], "scale", 1e-12)
        fitted = stats.kstest(x, lambda values: stats.norm.cdf(values, loc=location, scale=scale), method="auto")
        note = "Fully specified external normal null; estimating these parameters from this sample invalidates ordinary KS calibration."
    else:
        fields(p)
        from statsmodels.stats.diagnostic import lilliefors

        statistic, probability = lilliefors(x, dist="norm", pvalmethod="table")
        return [
            result(
                statistic,
                probability,
                n=len(x),
                calibration="Lilliefors normality with fitted mean/variance; table p-values can be bounded.",
            )
        ]
    return [result(fitted.statistic, fitted.pvalue, n=len(x), calibration=note)]


@preregistered_primitive
def location_test(frame, names, parameters, level):
    method = parameters["_method"]
    p = {k: v for k, v in parameters.items() if k != "_method"}
    alt = alternative(p)
    if method == "one_sample_t":
        fields(p, ("null",), ("alternative",))
        if len(names) != 1:
            raise ValueError("One-sample t requires one column.")
        x = vector(frame, names).ravel()
        nonconstant(x)
        null = number(p["null"], "null")
        fit = stats.ttest_1samp(x, popmean=null, alternative=alt)
        ci = fit.confidence_interval(confidence_level=level)
        return [
            result(
                fit.statistic,
                fit.pvalue,
                float(x.mean() - null),
                n=len(x),
                interval=interval(ci.low - null, ci.high - null, level),
                effect_definition="population mean minus declared null",
            )
        ]
    if method in ("pooled_t", "welch_t"):
        fields(p, ("levels",), ("alternative",))
        y, codes, samples = grouped(frame, names)
        levels = p["levels"]
        if not isinstance(levels, list) or len(levels) != 2 or levels[0] == levels[1]:
            raise ValueError("Two distinct explicit group levels are required.")
        group = columns(frame, [names[1]]).iloc[:, 0]
        if not group.isin(levels).all() or any(not (group == value).any() for value in levels):
            raise ValueError("Declared two-group levels must match all observed assignments.")
        a, b = y[group.to_numpy() == levels[0]], y[group.to_numpy() == levels[1]]
        nonconstant(a)
        nonconstant(b)
        fit = stats.ttest_ind(a, b, equal_var=method == "pooled_t", alternative=alt)
        ci = fit.confidence_interval(confidence_level=level)
        return [
            result(
                fit.statistic,
                fit.pvalue,
                float(a.mean() - b.mean()),
                n_by_group=[len(a), len(b)],
                interval=interval(ci.low, ci.high, level),
                effect_definition="first declared group mean minus second",
            )
        ]
    fields(p, (), ("alternative",))
    if len(names) != 2:
        raise ValueError("A paired procedure requires exactly two aligned columns.")
    paired = vector(frame, names)
    a, b = paired.T
    difference = a - b
    if method == "paired_t":
        nonconstant(difference)
        fit = stats.ttest_rel(a, b, alternative=alt)
        ci = fit.confidence_interval(confidence_level=level)
        return [
            result(
                fit.statistic,
                fit.pvalue,
                float(difference.mean()),
                n=len(a),
                interval=interval(ci.low, ci.high, level),
                effect_definition="paired mean difference",
            )
        ]
    if method == "wilcoxon":
        if not np.any(difference):
            raise ValueError("All-zero paired differences do not identify a signed-rank test.")
        fit = stats.wilcoxon(difference, alternative=alt, zero_method="wilcox", method="auto")
        return [
            result(
                fit.statistic,
                fit.pvalue,
                float(np.median(difference)),
                n=len(a),
                zero_differences=int((difference == 0).sum()),
                effect_definition="paired median difference; not a Hodges-Lehmann interval",
                assumption="Independent paired differences with symmetric continuous distribution for signed-rank interpretation.",
            )
        ]
    nonzero = difference[difference != 0]
    if not len(nonzero):
        raise ValueError("The sign test needs at least one nonzero difference.")
    positive = int((nonzero > 0).sum())
    fit = stats.binomtest(positive, len(nonzero), p=0.5, alternative=alt)
    ci = fit.proportion_ci(confidence_level=level, method="exact")
    return [
        result(
            positive,
            fit.pvalue,
            positive / len(nonzero) - 0.5,
            n_nonzero=len(nonzero),
            zero_differences=int(len(a) - len(nonzero)),
            interval=interval(ci.low - 0.5, ci.high - 0.5, level),
            effect_definition="Pr(positive paired difference | nonzero) minus 0.5",
        )
    ]


@preregistered_primitive
def association(frame, names, parameters, level):
    method = parameters["_method"]
    p = {k: v for k, v in parameters.items() if k != "_method"}
    fields(p, (), ("alternative", "bootstrap_replicates", "seed"))
    if len(names) != 2:
        raise ValueError("Association requires two aligned numeric columns.")
    values = vector(frame, names, 10)
    nonconstant(values)
    a, b = values.T
    alt = alternative(p)
    if method == "pearson":
        if "bootstrap_replicates" in p or "seed" in p:
            raise ValueError("Pearson uses the prespecified Fisher transform interval, not bootstrap options.")
        fit = stats.pearsonr(a, b, alternative=alt)
        ci = fit.confidence_interval(confidence_level=level)
        return [
            result(
                fit.statistic,
                fit.pvalue,
                fit.statistic,
                n=len(a),
                interval=interval(ci.low, ci.high, level),
                calibration="Bivariate-normal independent pairs; Fisher-z interval.",
            )
        ]
    if alt != "two-sided":
        raise ValueError("Rank-association bootstrap intervals in this interface require a two-sided plan.")
    fn = stats.spearmanr if method == "spearman" else stats.kendalltau
    fit = fn(a, b, alternative=alt)
    repetitions = integer(p.get("bootstrap_replicates", 499), "bootstrap_replicates", 199, 4999)
    seed = integer(p.get("seed", 42), "seed", 0, 2**32 - 1)
    if len(a) * repetitions > 2_000_000:
        raise ValueError("Association bootstrap resource bound exceeded.")
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(repetitions):
        idx = rng.integers(0, len(a), len(a))
        draws.append(float(fn(a[idx], b[idx]).statistic))
    valid = bool(np.isfinite(draws).all())
    ci = np.quantile(draws, [(1 - level) / 2, (1 + level) / 2]) if valid else None
    return [
        result(
            fit.statistic,
            fit.pvalue,
            fit.statistic,
            n=len(a),
            interval=interval(*ci, level, kind="paired percentile bootstrap") if valid else None,
            interval_status="computed" if valid else "undefined resamples; no biased deletion of failed draws",
            bootstrap_replicates=repetitions,
            seed=seed,
            calibration="Independent pairs; rank p-values use the selected library's standard calibration, not a time-series guarantee.",
        )
    ]


@preregistered_primitive
def contingency(frame, names, parameters, level):
    method = parameters["_method"]
    p = {k: v for k, v in parameters.items() if k != "_method"}
    if method == "chi_square":
        fields(p, (), ("yates",))
        if len(names) != 2:
            raise ValueError("Contingency testing needs two categorical columns.")
        data = columns(frame, names)
        if data.isna().any().any():
            raise ValueError("Missing contingency cells require a preregistered upstream missingness policy.")
        if any(not 2 <= data[name].nunique() <= 20 for name in names):
            raise ValueError("Contingency dimensions must be bounded before dense table allocation.")
        table = pd.crosstab(data.iloc[:, 0], data.iloc[:, 1]).to_numpy()
        if min(table.shape) < 2 or max(table.shape) > 20:
            raise ValueError("Contingency dimensions must be between two and twenty.")
        correction = p.get("yates", False)
        if type(correction) is not bool:
            raise ValueError("Yates option must be boolean.")
        fit = stats.chi2_contingency(table, correction=correction)
        if fit.expected_freq.min() < 5:
            raise ValueError("Expected contingency counts below five; use an explicitly applicable exact procedure.")
        effect = np.sqrt(fit.statistic / (table.sum() * min(table.shape[0] - 1, table.shape[1] - 1)))
        return [
            result(
                fit.statistic,
                fit.pvalue,
                effect,
                n=int(table.sum()),
                degrees_of_freedom=int(fit.dof),
                effect_definition="Cramer's V",
            )
        ]
    if method == "cochran_q":
        fields(p)
        if len(names) < 3 or len(names) > 10:
            raise ValueError("Cochran Q needs three to ten matched binary conditions.")
        values = np.column_stack([binary(frame, name) for name in names])
        if len(values) < 3:
            raise ValueError("Insufficient matched subjects.")
        from statsmodels.stats.contingency_tables import cochrans_q

        fit = cochrans_q(values)
        return [result(fit.statistic, fit.pvalue, n=len(values), degrees_of_freedom=len(names) - 1)]
    if len(names) != 2:
        raise ValueError("Two binary columns are required.")
    a, b = binary(frame, names[0]), binary(frame, names[1])
    table = np.zeros((2, 2), dtype=int)
    np.add.at(table, (a, b), 1)
    if method == "fisher_exact":
        fields(p, (), ("alternative",))
        if (table.sum(axis=0) == 0).any() or (table.sum(axis=1) == 0).any():
            raise ValueError("Fisher's table must have nonempty margins.")
        fit = stats.fisher_exact(table, alternative=alternative(p))
        return [
            result(
                float(fit.statistic) if np.isfinite(fit.statistic) else None,
                fit.pvalue,
                float(fit.statistic) if np.isfinite(fit.statistic) else None,
                statistic_kind="sample odds ratio",
                odds_ratio_infinite=bool(np.isinf(fit.statistic)),
                statistic_defined=bool(np.isfinite(fit.statistic)),
                n=len(a),
            )
        ]
    fields(p)
    from statsmodels.stats.contingency_tables import mcnemar

    fit = mcnemar(table, exact=True)
    return [
        result(
            fit.statistic,
            fit.pvalue,
            float(a.mean() - b.mean()),
            n=len(a),
            discordant_pairs=int(table[0, 1] + table[1, 0]),
            effect_definition="paired binary mean difference",
            calibration="Exact McNemar",
        )
    ]
