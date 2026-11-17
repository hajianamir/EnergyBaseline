"""Input-bound, explicitly preregistered statistical extensions; no implicit gate acceptance."""

from __future__ import annotations

from types import MappingProxyType
import importlib.metadata
import json
import re

import numpy as np

from ..core.authorization import assert_bound_inference
from ..core.ingest import sha256_dataframe
from ..core.schema import ColumnRole
from ..core.tcp import adjust_p_values
from ..core.vsg import ComplianceGateError
from ..workbench import _validate_frame
from ..workbench.common import chronological, digest, fields, finite_json
from . import bayes
from .designs import ancova, factorial, manova, posthoc, repeated_anova
from .power import prospective
from .tests import association, contingency, location_test, normality


FREQUENTIST = MappingProxyType(
    {
        "ks_fixed_normal": normality,
        "lilliefors": normality,
        "one_sample_t": location_test,
        "pooled_t": location_test,
        "welch_t": location_test,
        "paired_t": location_test,
        "wilcoxon": location_test,
        "sign_test": location_test,
        "pearson": association,
        "spearman": association,
        "kendall": association,
        "chi_square": contingency,
        "fisher_exact": contingency,
        "mcnemar": contingency,
        "cochran_q": contingency,
        "repeated_anova": repeated_anova,
        "factorial_anova": factorial,
        "ancova": ancova,
        "manova": manova,
        "tukey_hsd": posthoc,
        "dunn": posthoc,
    }
)
BAYESIAN = MappingProxyType(
    {
        "beta_binomial": bayes.conjugate,
        "normal_mean": bayes.conjugate,
        "normal_model_average": bayes.model_average,
        "linear_metropolis": bayes.metropolis,
        "linear_variational": bayes.variational,
    }
)
METHODS = tuple(FREQUENTIST) + tuple(BAYESIAN) + ("prospective_power",)
TAGGED = (
    "ks_fixed_normal",
    "lilliefors",
    "one_sample_t",
    "pooled_t",
    "welch_t",
    "paired_t",
    "wilcoxon",
    "sign_test",
    "pearson",
    "spearman",
    "kendall",
    "chi_square",
    "fisher_exact",
    "mcnemar",
    "cochran_q",
    "tukey_hsd",
    "dunn",
    "beta_binomial",
    "normal_mean",
)


def validate_plan(plan):
    """Structural validation only; safe before acceptance, never computes a statistic."""
    fields(
        plan, ("version", "time", "targets", "metadata", "sampling", "assumption_justification", "correction", "tests")
    )
    try:
        encoded = json.dumps(plan, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Statistical plans must contain bounded finite JSON only.") from None
    if len(encoded) > 200_000 or type(plan["version"]) is not int or plan["version"] != 1:
        raise ValueError("Unsupported statistical plan version or resource size.")
    if not isinstance(plan["time"], str) or not plan["time"]:
        raise ValueError("An explicit timestamp column is required.")
    for key in ("targets", "metadata"):
        values = plan[key]
        if (
            not isinstance(values, list)
            or any(not isinstance(v, str) or not v for v in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError("Targets/metadata must be unique explicit column names.")
    if (
        not plan["targets"]
        or set(plan["targets"]) & set(plan["metadata"])
        or plan["time"] in plan["metadata"] + plan["targets"]
    ):
        raise ValueError("Time, targets and metadata must be disjoint, with at least one target.")
    if plan["sampling"] not in ("independent", "independent_subjects"):
        raise ValueError("These methods require an explicit independent-unit or independent-subject design.")
    if (
        not isinstance(plan["assumption_justification"], str)
        or not 10 <= len(plan["assumption_justification"].strip()) <= 2000
    ):
        raise ValueError("Document why the declared sampling/likelihood assumptions are appropriate.")
    if plan["correction"] not in ("tcp", "fdr_tsbh", "fdr_tsbky"):
        raise ValueError("Select TCP correction or an explicitly alpha-bound two-stage FDR method.")
    if not isinstance(plan["tests"], list) or not 1 <= len(plan["tests"]) <= 50:
        raise ValueError("A plan must declare one to fifty procedures before lock.")
    identifiers = set()
    for test in plan["tests"]:
        fields(test, ("id", "method", "columns", "parameters"))
        if (
            not isinstance(test["id"], str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", test["id"])
            or test["id"] in identifiers
        ):
            raise ValueError("Procedure identifiers must be unique short codes.")
        identifiers.add(test["id"])
        if not isinstance(test["method"], str) or test["method"] not in METHODS:
            raise ValueError("Unknown preregistered statistical method.")
        cols = test["columns"]
        if (
            not isinstance(cols, list)
            or any(not isinstance(c, str) or not c for c in cols)
            or len(set(cols)) != len(cols)
        ):
            raise ValueError("Procedure columns must be unique explicit names.")
        if not isinstance(test["parameters"], dict) or any(str(k).startswith("_") for k in test["parameters"]):
            raise ValueError("Procedure parameters must be an object without reserved fields.")
        if test["method"] == "prospective_power":
            if cols:
                raise ValueError("Prospective power must not derive an effect from observed data columns.")
        elif not cols or not set(cols) & set(plan["targets"]):
            raise ValueError("Every observed-data procedure must include a declared authorized target.")
        if test["method"] in ("repeated_anova", "mcnemar", "cochran_q", "paired_t", "wilcoxon", "sign_test"):
            if plan["sampling"] != "independent_subjects":
                raise ValueError("Paired/repeated methods require independent-subject sampling, not independent rows.")
        if test["method"] in (
            "ks_fixed_normal",
            "lilliefors",
            "one_sample_t",
            "pooled_t",
            "welch_t",
            "chi_square",
            "fisher_exact",
            "factorial_anova",
            "ancova",
            "manova",
            "tukey_hsd",
            "dunn",
            *BAYESIAN,
        ):
            if plan["sampling"] != "independent":
                raise ValueError("Unpaired procedures require independent observational units.")
    return digest(plan)


def assert_plan_authorized(plan, frame, vsg, tcp):
    fingerprint = validate_plan(plan)
    if vsg is None or tcp is None:
        raise ComplianceGateError(
            "Statistical execution requires accepted input-bound VSG and a locked plan-bound TCP."
        )
    assert_bound_inference(frame, plan["time"], plan["targets"], vsg, tcp)
    if fingerprint not in tcp.extension_plan_hashes:
        raise ComplianceGateError("This exact statistical plan was not explicitly registered before TCP lock.")
    permitted = set(plan["targets"])
    for target in plan["targets"]:
        permitted.update(vsg.included_variables(target))
    schema = vsg.schema
    groups = {spec.name for spec in schema.columns if spec.role == ColumnRole.GROUP} if schema is not None else set()
    if not set(plan["metadata"]).issubset(groups):
        raise ComplianceGateError(
            "Statistical metadata must be explicitly declared GROUP roles in the accepted VSG schema."
        )
    permitted.update(plan["metadata"])
    if any(not set(test["columns"]).issubset(permitted) for test in plan["tests"]):
        raise ComplianceGateError("A statistical procedure addresses a variable excluded by the accepted gate.")
    return fingerprint


def correct_family(probabilities, method, alpha):
    if method in ("fdr_tsbh", "fdr_tsbky"):
        from statsmodels.stats.multitest import multipletests

        # Two-stage adjusted values are alpha dependent and may legitimately be below raw p.
        return multipletests(probabilities, alpha=alpha, method=method, maxiter=1)[1].tolist()
    return [row["p_adj"] for row in adjust_p_values(probabilities, method)]


def run_statistical_plan(plan, frame, *, vsg=None, tcp=None):
    """All authorization checks precede every observed-data primitive. No implicit acceptance/lock."""
    fingerprint = assert_plan_authorized(plan, frame, vsg, tcp)
    _validate_frame(frame)
    chronological(frame, plan["time"])
    if len(frame) > 20_000:
        raise ValueError("Statistical plans are bounded to 20000 observations.")
    input_hash = sha256_dataframe(frame)
    rows, bayesian, designs = [], [], []
    try:
        for test in plan["tests"]:
            method, parameters = test["method"], dict(test["parameters"])
            if method in TAGGED:
                parameters["_method"] = method
            if method in FREQUENTIST:
                outputs = FREQUENTIST[method](
                    frame, test["columns"], parameters, tcp.confidence_level, plan=plan, vsg=vsg, tcp=tcp
                )
                for output in outputs:
                    rows.append({"id": test["id"], "method": method, **output})
            elif method in BAYESIAN:
                bayesian.append(
                    {
                        "id": test["id"],
                        "method": method,
                        **BAYESIAN[method](
                            frame, test["columns"], parameters, tcp.confidence_level, plan=plan, vsg=vsg, tcp=tcp
                        ),
                    }
                )
            else:
                designs.append({"id": test["id"], "method": method, **prospective(parameters, tcp.alpha)})
        if len(rows) > 1000:
            raise ValueError("Expanded hypothesis family exceeds 1000 comparisons.")
        method = tcp.correction if plan["correction"] == "tcp" else plan["correction"]
        if rows:
            corrected = correct_family([r["p_input"] for r in rows], method, tcp.alpha)
            for row, adjusted in zip(rows, corrected):
                row.update(
                    p_adjusted=float(adjusted),
                    reject=bool(adjusted <= tcp.alpha),
                    correction=method,
                    correction_alpha=tcp.alpha,
                    family_size=len(rows),
                )
        finite_json({"tests": rows, "bayesian": bayesian, "prospective": designs})
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OverflowError,
        np.linalg.LinAlgError,
        ZeroDivisionError,
        RuntimeError,
        FloatingPointError,
    ):
        raise ValueError(
            "Statistical plan failed method/design/numerical validation. No partial results or raw error values are exported."
        ) from None
    # Gates and input must still match after the complete family, before releasing any evidence.
    assert_plan_authorized(plan, frame, vsg, tcp)
    if sha256_dataframe(frame) != input_hash:
        raise RuntimeError("Statistical input mutation detected.")
    payload = {
        "version": 1,
        "operation": "statistical-plan",
        "spec_sha256": fingerprint,
        "input_sha256": input_hash,
        "reference_sha256": None,
        "vsg_sha256": vsg.vsg_sha256,
        "preregistration_sha256": tcp.preregistration_sha256,
        "alpha": tcp.alpha,
        "confidence_level": tcp.confidence_level,
        "family_definition": "All expanded frequentist comparisons in this plan",
        "assumptions": "Explicit caller-declared sampling/likelihood conditions; not established automatically. No autocorrelation-robust or distribution-free guarantee.",
        "sampling": plan["sampling"],
        "frequentist": rows,
        "bayesian": bayesian,
        "prospective": designs,
        "baseline_changed": False,
        "library_versions": {
            name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scipy", "statsmodels")
        },
    }
    payload["evidence_sha256"] = digest(payload)
    return finite_json(payload)
