"""Balanced repeated-measures and fixed formula-free factorial/ANCOVA/MANOVA designs."""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats

from .common import preregistered_primitive, columns, fields, grouped, integer, interval, result, vector


def _factor(series):
    if series.isna().any():
        raise ValueError("Design factors cannot be missing.")
    codes, labels = pd.factorize(series, sort=False)
    if not 2 <= len(labels) <= 10:
        raise ValueError("A factor must have two to ten observed levels.")
    return codes


@preregistered_primitive
def repeated_anova(frame, names, parameters, level):
    fields(parameters)
    if len(names) != 3:
        raise ValueError("Repeated ANOVA needs response, subject and within-condition columns.")
    y = vector(frame, [names[0]], 6).ravel()
    subject = columns(frame, [names[1]]).iloc[:, 0]
    condition = columns(frame, [names[2]]).iloc[:, 0]
    if subject.isna().any() or condition.isna().any():
        raise ValueError("Subjects and within conditions cannot be missing.")
    s, subjects = pd.factorize(subject, sort=False)
    if len(subjects) > 100 or len(y) > 500:
        raise ValueError("Repeated ANOVA is bounded to 100 subjects and 500 complete observations.")
    c = _factor(condition)
    if len(subjects) < 3 or (pd.crosstab(s, c).to_numpy() != 1).any():
        raise ValueError("Repeated ANOVA requires complete balanced subjects with exactly one row per condition.")
    from statsmodels.stats.anova import AnovaRM

    data = pd.DataFrame({"response": y, "subject": s, "condition": c})
    fit = AnovaRM(data, "response", "subject", within=["condition"]).fit().anova_table.loc["condition"]
    return [
        result(
            fit["F Value"],
            fit["Pr > F"],
            subjects=len(subjects),
            conditions=len(np.unique(c)),
            numerator_df=float(fit["Num DF"]),
            denominator_df=float(fit["Den DF"]),
            assumption="Independent subjects; normal within-subject errors and sphericity. No automatic Greenhouse-Geisser correction.",
        )
    ]


@preregistered_primitive
def factorial(frame, names, parameters, level):
    fields(parameters)
    if len(names) != 3:
        raise ValueError("Two-way factorial ANOVA needs response and two factors.")
    y = vector(frame, [names[0]], 20).ravel()
    data = pd.DataFrame(
        {
            "response": y,
            "factor0": _factor(columns(frame, [names[1]]).iloc[:, 0]),
            "factor1": _factor(columns(frame, [names[2]]).iloc[:, 0]),
        }
    )
    if (pd.crosstab(data.factor0, data.factor1).to_numpy() < 2).any():
        raise ValueError("Every factorial cell needs at least two observations.")
    return _anova(
        data,
        "response ~ C(factor0) * C(factor1)",
        level,
        "Type II sums of squares; interpret main effects cautiously with interaction.",
    )


@preregistered_primitive
def ancova(frame, names, parameters, level):
    fields(parameters)
    if not 3 <= len(names) <= 8:
        raise ValueError("ANCOVA needs response, group and one to six real covariates.")
    values = vector(frame, [names[0]] + names[2:], 20)
    data = pd.DataFrame({"response": values[:, 0], "factor0": _factor(columns(frame, [names[1]]).iloc[:, 0])})
    for i in range(values.shape[1] - 1):
        data[f"covariate{i}"] = values[:, i + 1]
    formula = "response ~ C(factor0) + " + " + ".join(f"covariate{i}" for i in range(values.shape[1] - 1))
    return _anova(
        data, formula, level, "Common within-group covariate slopes, homogeneous errors and independent observations."
    )


def _anova(data, formula, level, assumption):
    # Only internal aliases and fixed operators enter patsy/statsmodels; no caller expression is evaluated.
    from statsmodels.formula.api import ols
    from statsmodels.stats.anova import anova_lm

    fitted = ols(formula, data=data).fit()
    X = fitted.model.exog
    if np.linalg.matrix_rank(X) != X.shape[1] or len(data) < 5 * X.shape[1] or fitted.df_resid < 5:
        raise ValueError("Rank deficient or undersized design; no singular-fit inference.")
    table = anova_lm(fitted, typ=2)
    error_ss = float(table.loc["Residual", "sum_sq"])
    if error_ss <= 0:
        raise ValueError("Zero residual variation does not identify an ANOVA error variance.")
    rows = []
    for position, (term, row) in enumerate(table.drop(index="Residual").iterrows()):
        rows.append(
            result(
                row["F"],
                row["PR(>F)"],
                row["sum_sq"] / (row["sum_sq"] + error_ss),
                suffix=f"term{position}",
                term_code=term,
                numerator_df=float(row["df"]),
                denominator_df=float(fitted.df_resid),
                effect_definition="partial eta squared",
                n=len(data),
                assumption=assumption,
            )
        )
    return rows


@preregistered_primitive
def manova(frame, names, parameters, level):
    fields(parameters, ("dependent_count",))
    count = integer(parameters["dependent_count"], "dependent_count", 2, 5)
    if len(names) != count + 1:
        raise ValueError("MANOVA column ordering must be dependent variables followed by one group factor.")
    Y = vector(frame, names[:count], max(20, count * 10))
    group = _factor(columns(frame, [names[-1]]).iloc[:, 0])
    data = pd.DataFrame({**{f"outcome{i}": Y[:, i] for i in range(count)}, "factor0": group})
    from statsmodels.multivariate.manova import MANOVA

    fit = MANOVA.from_formula(" + ".join(f"outcome{i}" for i in range(count)) + " ~ C(factor0)", data=data)
    X = fit.exog
    residual = Y - X @ np.linalg.lstsq(X, Y, rcond=None)[0]
    if np.linalg.matrix_rank(X) != X.shape[1] or np.linalg.matrix_rank(residual) != count or len(Y) < 5 * X.shape[1]:
        raise ValueError("Rank deficient or undersized MANOVA covariance/design.")
    summary = fit.mv_test().results["C(factor0)"]["stat"].loc["Wilks' lambda"]
    return [
        result(
            summary["F Value"],
            summary["Pr > F"],
            summary["Value"],
            n=len(Y),
            effect_definition="Wilks lambda",
            numerator_df=float(summary["Num DF"]),
            denominator_df=float(summary["Den DF"]),
            assumption="Independent multivariate-normal residuals and equal within-group covariance matrices.",
        )
    ]


@preregistered_primitive
def posthoc(frame, names, parameters, level):
    method = parameters["_method"]
    fields({k: v for k, v in parameters.items() if k != "_method"})
    y, codes, samples = grouped(frame, names, minimum=5)
    groups = range(len(samples))
    if method == "tukey_hsd":
        from statsmodels.stats.multicomp import pairwise_tukeyhsd

        fitted = pairwise_tukeyhsd(y, codes, alpha=1 - level)
        rows = []
        for idx, (a, b) in enumerate(combinations(groups, 2)):
            low, high = fitted.confint[idx]
            rows.append(
                result(
                    fitted.meandiffs[idx],
                    fitted.pvalues[idx],
                    fitted.meandiffs[idx],
                    suffix=f"pair{a}_{b}",
                    p_kind="Tukey within-family adjusted; outer plan adjustment is additionally conservative",
                    interval=interval(low, high, level, kind="simultaneous Tukey family"),
                    effect_definition="second group mean minus first; groups ordered by first observed occurrence",
                )
            )
        return rows
    ranks = stats.rankdata(y, method="average")
    means = [float(ranks[codes == i].mean()) for i in groups]
    _, counts = np.unique(y, return_counts=True)
    n = len(y)
    variance = n * (n + 1) / 12 - np.sum(counts**3 - counts) / (12 * (n - 1))
    if variance <= 0:
        raise ValueError("Tied constant ranks do not identify Dunn comparisons.")
    rows = []
    for a, b in combinations(groups, 2):
        difference = means[a] - means[b]
        z = difference / np.sqrt(variance * (1 / len(samples[a]) + 1 / len(samples[b])))
        rows.append(
            result(
                z,
                2 * stats.norm.sf(abs(z)),
                difference,
                suffix=f"pair{a}_{b}",
                effect_definition="first minus second mean pooled rank",
                tie_correction=True,
                calibration="Dunn normal approximation; outer plan correction covers all declared pairs.",
            )
        )
    return rows
