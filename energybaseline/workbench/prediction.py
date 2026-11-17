"""Exploratory binary classification and novelty detection with chronological holdouts."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, log_loss, roc_auc_score
from sklearn.neighbors import KNeighborsClassifier, LocalOutlierFactor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

from .analytics import group_fairness
from .common import binary, chronological, fields, integer, number, numeric
from .preparation import TabularTransformer


def classification_holdout(frame, spec):
    fields(
        spec,
        ("target", "numeric", "categorical", "time", "train_rows", "estimator"),
        ("gap", "threshold", "sensitive", "minimum_group_size"),
    )
    chronological(frame, spec["time"])
    if spec["time"] in spec["numeric"] + spec["categorical"]:
        raise ValueError("Raw time must not be treated as an arbitrary classification predictor.")
    y = binary(frame, spec["target"])
    split = integer(spec["train_rows"], "train_rows", 40, len(frame) - 1)
    gap = integer(spec.get("gap", 1), "gap", 1, split - 20)
    train_end = split - gap
    if np.unique(y[:train_end]).size != 2:
        raise ValueError("Both binary classes must be represented in training.")
    transformer = TabularTransformer(
        numeric_columns=spec["numeric"],
        categorical_columns=spec["categorical"],
        target_columns=[spec["target"]],
        scaling="standard",
    )
    transformer.fit(frame.iloc[:train_end])
    train, test = transformer.transform(frame.iloc[:train_end]), transformer.transform(frame.iloc[split:])
    if train.shape[1] > train_end // 20:
        raise ValueError("Encoded feature capacity exceeds training rows // 20; reduce predictors/categories.")
    factories = {
        "logistic": lambda: LogisticRegression(max_iter=1000, random_state=42),
        "random_forest": lambda: RandomForestClassifier(
            n_estimators=100, max_depth=5, min_samples_leaf=5, random_state=42, n_jobs=1
        ),
        "knn": lambda: KNeighborsClassifier(n_neighbors=5),
    }
    if spec["estimator"] not in factories:
        raise ValueError("Unsupported binary classification estimator.")
    if len(frame) > (5000 if spec["estimator"] == "knn" else 50_000):
        raise ValueError("Classification resource bound exceeded.")
    model = factories[spec["estimator"]]()
    model.fit(train, y[:train_end])
    probability = model.predict_proba(test)[:, 1]
    threshold = number(spec.get("threshold", 0.5), "threshold", 0.000001, 0.999999)
    pred = (probability >= threshold).astype(int)
    observed = y[split:]
    result = {
        "estimator": spec["estimator"],
        "train_rows": train_end,
        "purged_rows": gap,
        "test_rows": len(test),
        "encoded_features": train.shape[1],
        "threshold": threshold,
        "confusion_matrix_true_rows_predicted_columns": confusion_matrix(observed, pred, labels=[0, 1]).tolist(),
        "accuracy": float(np.mean(pred == observed)),
        "brier_score": float(np.mean((probability - observed) ** 2)),
        "log_loss": float(log_loss(observed, probability, labels=[0, 1])),
        "roc_auc": float(roc_auc_score(observed, probability)) if len(np.unique(observed)) == 2 else None,
        "baseline_changed": False,
        "p_values_or_confidence_intervals_computed": False,
    }
    if "sensitive" in spec:
        if spec["sensitive"] == spec["target"] or spec["sensitive"] not in frame:
            raise ValueError("Fairness requires an explicit non-target sensitive column.")
        audit_frame = pd.DataFrame(
            {"group": frame[spec["sensitive"]].iloc[split:].to_numpy(), "observed": observed, "predicted": pred}
        )
        result["fairness"] = group_fairness(
            audit_frame,
            {
                "group": "group",
                "outcome": "observed",
                "prediction": "predicted",
                "minimum_group_size": spec.get("minimum_group_size", 20),
            },
        )
    elif "minimum_group_size" in spec:
        raise ValueError("Minimum group size requires a declared sensitive column.")
    return result


def novelty_holdout(frame, spec):
    fields(spec, ("features", "time", "train_rows", "method", "contamination"), ("gap", "outcome"))
    chronological(frame, spec["time"])
    if spec["time"] in spec["features"] or spec.get("outcome") in spec["features"]:
        raise ValueError("Timestamp/outcomes cannot enter novelty predictors.")
    X = numeric(frame, spec["features"], missing=True)
    split = integer(spec["train_rows"], "train_rows", 40, len(frame) - 1)
    gap = integer(spec.get("gap", 1), "gap", 1, split - 20)
    train_end = split - gap
    if X.shape[1] > train_end // 20 or np.isnan(X[:train_end]).all(axis=0).any():
        raise ValueError("Novelty training violates feature capacity or all-missing predictor constraints.")
    contamination = number(spec["contamination"], "contamination", 0.000001, 0.5)
    factories = {
        "isolation_forest": lambda: IsolationForest(
            n_estimators=100, contamination=contamination, random_state=42, n_jobs=1
        ),
        "local_outlier_factor": lambda: LocalOutlierFactor(
            n_neighbors=min(20, train_end - 1), novelty=True, contamination=contamination
        ),
        "one_class_svm": lambda: OneClassSVM(nu=contamination, gamma="scale"),
    }
    if spec["method"] not in factories:
        raise ValueError("Unknown novelty detector.")
    if spec["method"] in ("local_outlier_factor", "one_class_svm") and len(X) > 5000:
        raise ValueError("Neighbor/kernel novelty detectors are bounded to 5000 rows.")
    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", factories[spec["method"]]()),
        ]
    )
    model.fit(X[:train_end])
    test = X[split:]
    flagged = model.predict(test) == -1
    scores = -model.decision_function(test)
    positions = (np.flatnonzero(flagged) + split).tolist()
    result = {
        "method": spec["method"],
        "train_rows": train_end,
        "purged_rows": gap,
        "test_rows": len(test),
        "flagged_rows": int(flagged.sum()),
        "first_flagged_input_positions": positions[:20],
        "position_list_truncated": len(positions) > 20,
        "mean_anomaly_score": float(scores.mean()),
        "threshold_interpretation": "Model-specific training threshold, not a distribution-free p-value.",
        "baseline_changed": False,
        "automatic_refit": False,
    }
    if "outcome" in spec:
        observed = binary(frame, spec["outcome"])[split:]
        result["confusion_matrix_true_rows_predicted_columns"] = confusion_matrix(
            observed, flagged, labels=[0, 1]
        ).tolist()
        result["roc_auc"] = float(roc_auc_score(observed, scores)) if len(np.unique(observed)) == 2 else None
    return result
