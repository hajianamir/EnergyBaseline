"""Offline exploratory workbench, deliberately independent of locked baseline mutation.

Use run_workbench with a versioned declarative JSON request. The fixed operation
registry never evaluates expressions, imports user modules, loads pickles or downloads
models. These descriptive/decision-support operations do not execute hypothesis tests.
"""

from __future__ import annotations

from types import MappingProxyType

import json

import pandas as pd
from numpy.linalg import LinAlgError

from .analytics import categorical_divergence, group_fairness, portfolio_risk
from .business import business_validation
from .automl import automl_search
from .deep import deep_forecast
from .planning import routing, scheduling, evolutionary
from .simulation import stock_flow, agent_sir, digital_twin
from .vision import image_classification, image_segmentation, image_detection, template_ocr
from .common import digest, fields, finite_json
from .domains import competing_risks, graph_profile, rating_recommendations, survival_curve, text_retrieval
from .entities import entity_candidates
from .external import flaml_holdout, pandera_validation, pretrained_ocr
from .forecasting import forecast_holdout
from .operations import optimize, pareto, queue_simulation
from .preparation import multi_target_consensus, target_encoding_preview, transform_preview
from .regression import regression_holdout
from .prediction import classification_holdout, novelty_holdout


OPERATIONS = MappingProxyType(
    {
        "automl-search": automl_search,
        "flaml-search": flaml_holdout,
        "pandera-validation": pandera_validation,
        "deep-forecast": deep_forecast,
        "digital-twin": digital_twin,
        "portfolio-risk": portfolio_risk,
        "group-fairness": group_fairness,
        "kaplan-meier": survival_curve,
        "competing-risks": competing_risks,
        "classification-holdout": classification_holdout,
        "novelty-holdout": novelty_holdout,
        "graph-profile": graph_profile,
        "text-retrieval": text_retrieval,
        "rating-svd": rating_recommendations,
        "pareto-front": pareto,
        "fcfs-queue": queue_simulation,
        "tabular-transform": transform_preview,
        "target-encoding": target_encoding_preview,
        "multi-target-consensus": multi_target_consensus,
        "forecast-holdout": forecast_holdout,
        "regression-holdout": regression_holdout,
    }
)
SPEC_OPERATIONS = MappingProxyType({
    "pretrained-ocr": pretrained_ocr,
    "linear-optimize": optimize,
    "route-optimize": routing,
    "schedule-optimize": scheduling,
    "evolutionary-optimize": evolutionary,
    "stock-flow-simulate": stock_flow,
    "agent-simulate": agent_sir,
    "image-classification": image_classification,
    "image-segmentation": image_segmentation,
    "image-detection": image_detection,
    "template-ocr": template_ocr,
})
OPERATION_NAMES = tuple(OPERATIONS) + ("categorical-divergence", "entity-candidates", "business-validation") + tuple(SPEC_OPERATIONS)


def _validate_frame(frame):
    if (
        not isinstance(frame, pd.DataFrame)
        or not 1 <= len(frame) <= 200_000
        or not 1 <= len(frame.columns) <= 1000
        or frame.size > 5_000_000
        or frame.columns.has_duplicates
        or any(not isinstance(c, str) for c in frame.columns)
    ):
        raise ValueError("Workbench data require 1..200000 rows and 1..1000 unique named columns.")


def run_workbench(request, frame=None, *, reference=None):
    """Run a bounded job and return input/spec-bound JSON with no baseline side effects.

    Model fitting is explicitly exploratory chronological holdout evaluation, with
    no p-values, confidence intervals, causal effects, inference authorization or
    automatic promotion to the baseline registry. Fitted models are not persisted.
    """
    fields(request, ("version", "operation", "parameters"))
    if type(request["version"]) is not int or request["version"] != 1:
        raise ValueError("Unsupported workbench request version.")
    operation = request["operation"]
    if not isinstance(operation, str) or operation not in OPERATION_NAMES:
        raise ValueError("Unknown workbench operation.")
    if not isinstance(request["parameters"], dict):
        raise ValueError("Operation parameters must be an object.")
    try:
        if len(json.dumps(request, allow_nan=False)) > 1_000_000:
            raise ValueError("Workbench request exceeds one million characters.")
        spec_hash = digest(request)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Workbench request must be bounded finite JSON.") from None
    from ..core.ingest import sha256_dataframe

    if operation in SPEC_OPERATIONS:
        if frame is not None or reference is not None:
            raise ValueError("Specification-only operations do not consume an implicit dataset/reference.")
        data_hash, reference_hash = None, None
    else:
        _validate_frame(frame)
        data_hash = sha256_dataframe(frame)
        reference_hash = None
        if operation in ("categorical-divergence", "entity-candidates", "business-validation"):
            if reference is not None or operation != "business-validation":
                _validate_frame(reference)
                reference_hash = sha256_dataframe(reference)
        elif reference is not None:
            raise ValueError("Reference data are supported only for categorical-divergence, entity-candidates and business-validation.")
    try:
        if operation in SPEC_OPERATIONS:
            result = SPEC_OPERATIONS[operation](request["parameters"])
        elif operation == "business-validation":
            result = business_validation(frame, reference, request["parameters"])
        elif operation in ("categorical-divergence", "entity-candidates"):
            handler = categorical_divergence if operation == "categorical-divergence" else entity_candidates
            result = handler(frame, reference, request["parameters"])
        else:
            result = OPERATIONS[operation](frame, request["parameters"])
        finite_json(result)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, LinAlgError, RuntimeError, OSError):
        # Third-party exception text can echo cell values, category labels or caller literals.
        raise ValueError(
            "Workbench operation failed validation or numeric execution. Check the documented schema, "
            "finite values, sample sizes and applicable parameters; raw error details are not exported."
        ) from None
    if frame is not None and sha256_dataframe(frame) != data_hash:
        raise RuntimeError("Workbench input mutation detected.")
    if reference is not None and sha256_dataframe(reference) != reference_hash:
        raise RuntimeError("Workbench reference mutation detected.")
    import importlib.metadata

    return finite_json(
        {
            "version": 1,
            "operation": operation,
            "spec_sha256": spec_hash,
            "input_sha256": data_hash,
            "reference_sha256": reference_hash,
            "library_versions": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "pandas", "scipy", "scikit-learn", "statsmodels")
            },
            "scope": "Exploratory decision support; no baseline acceptance, mutation or inferential tests.",
            "result": result,
        }
    )
