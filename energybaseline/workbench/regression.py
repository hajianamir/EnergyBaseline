"""Chronological exploratory regression with in-training selection, ALE and fixed stress scenarios."""

from __future__ import annotations

import numpy as np
from sklearn.feature_selection import RFE
from sklearn.impute import SimpleImputer
from sklearn.linear_model import HuberRegressor, RANSACRegressor, Ridge
from sklearn.model_selection import TimeSeriesSplit
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

from .common import chronological, fields, integer, number, numeric


def _estimator(method, n):
    factories = {
        "ridge": lambda: Ridge(alpha=1.0),
        "huber": lambda: HuberRegressor(max_iter=1000),
        "ransac": lambda: RANSACRegressor(random_state=42),
        "knn": lambda: KNeighborsRegressor(n_neighbors=min(5, n)),
        "decision_tree": lambda: DecisionTreeRegressor(max_depth=5, min_samples_leaf=5, random_state=42),
    }
    if method not in factories:
        raise ValueError("Unsupported exploratory regression estimator.")
    return factories[method]()


def _pipeline(estimator):
    return Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler()), ("model", estimator)])


def _sequential(X, y, count, direction, gap):
    """Every candidate/fold learns imputation and scaling inside that fold, never before CV."""
    if X.shape[1] > 20 or len(X) > 5000:
        raise ValueError("Sequential selection accepts at most 20 features and 5000 training rows.")
    folds = list(TimeSeriesSplit(n_splits=3, gap=gap).split(X))
    if min(len(train) for train, _ in folds) < 10:
        raise ValueError("Sequential selection requires larger initial temporal training folds.")
    selected = [] if direction == "forward" else list(range(X.shape[1]))
    while len(selected) != count:
        candidates = [i for i in range(X.shape[1]) if i not in selected] if direction == "forward" else selected
        scored = []
        for candidate in candidates:
            subset = (
                sorted(selected + [candidate]) if direction == "forward" else [i for i in selected if i != candidate]
            )
            losses = []
            for train, valid in folds:
                if np.isnan(X[np.ix_(train, subset)]).all(axis=0).any():
                    raise ValueError("An inner training fold has an entirely missing feature.")
                model = _pipeline(Ridge(alpha=1.0))
                model.fit(X[np.ix_(train, subset)], y[train])
                losses.append(float(np.mean((model.predict(X[np.ix_(valid, subset)]) - y[valid]) ** 2)))
            scored.append((float(np.mean(losses)), candidate, subset))
        selected = min(scored, key=lambda item: (item[0], item[1]))[2]
    return np.asarray(selected, dtype=int)


def _ale(model, X, feature, bins):
    values = X[:, feature]
    if not np.isfinite(values).all():
        raise ValueError("ALE requires nonmissing values for its selected feature.")
    edges = np.unique(np.quantile(values, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        raise ValueError("ALE needs at least three distinct feature quantile edges.")
    membership = np.clip(np.searchsorted(edges, values, side="right") - 1, 0, len(edges) - 2)
    effects, counts = [], []
    for i in range(len(edges) - 1):
        batch = X[membership == i].copy()
        counts.append(len(batch))
        if not len(batch):
            effects.append(0.0)
            continue
        low, high = batch.copy(), batch.copy()
        low[:, feature], high[:, feature] = edges[i], edges[i + 1]
        effects.append(float(np.mean(model.predict(high) - model.predict(low))))
    # Midpoint accumulated first-order effects, centered under training bin frequencies.
    accumulated = np.cumsum(effects) - np.asarray(effects) / 2
    accumulated -= np.average(accumulated, weights=counts)
    return {
        "edges": edges.tolist(),
        "centered_effects": accumulated.tolist(),
        "counts": counts,
        "fit_scope": "Training predictors only",
        "causal_interpretation": False,
        "method": "First-order quantile-binned ALE, midpoint accumulation and empirical centering",
    }


def regression_holdout(frame, spec):
    fields(
        spec,
        ("target", "features", "time", "train_rows", "estimator"),
        ("selection", "feature_count", "gap", "ale_feature", "ale_bins", "stress"),
    )
    chronological(frame, spec["time"])
    features = spec["features"]
    if spec["target"] in features or spec["time"] in features:
        raise ValueError("Target and timestamp cannot be regression predictors.")
    X = numeric(frame, features, missing=True)
    y = numeric(frame, [spec["target"]]).ravel()
    split = integer(spec["train_rows"], "train_rows", 40, len(frame) - 1)
    gap = integer(spec.get("gap", 1), "gap", 1, min(1000, split - 20))
    fit_end = split - gap
    train, target = X[:fit_end], y[:fit_end]
    if np.isnan(train).all(axis=0).any():
        raise ValueError("All-missing training predictors cannot be imputed.")
    capacity = min(len(features), fit_end // 20)
    count = integer(spec.get("feature_count", capacity), "feature_count", 1, capacity)
    selection = spec.get("selection", "none")
    if selection == "none":
        if len(features) > capacity or count != len(features):
            raise ValueError("Feature capacity exceeded: use explicit training-only selection.")
        positions = np.arange(len(features))
    elif selection == "rfe":
        imputer = SimpleImputer(strategy="median")
        scaler = StandardScaler()
        complete = scaler.fit_transform(imputer.fit_transform(train))
        selector = RFE(Ridge(alpha=1.0), n_features_to_select=count, step=1).fit(complete, target)
        positions = np.flatnonzero(selector.support_)
    elif selection in ("sequential_forward", "sequential_backward"):
        positions = _sequential(train, target, count, selection.removeprefix("sequential_"), gap)
    else:
        raise ValueError("Unsupported selection: use none, rfe, sequential_forward or sequential_backward.")
    train, test = train[:, positions], X[split:, positions]
    model = _pipeline(_estimator(spec["estimator"], len(train)))
    model.fit(train, target)
    prediction = model.predict(test)
    if not np.isfinite(prediction).all():
        raise ValueError("Regression produced nonfinite predictions.")
    error = prediction - y[split:]
    output = {
        "estimator": spec["estimator"],
        "train_rows": fit_end,
        "purged_rows": gap,
        "test_rows": len(test),
        "selected_feature_positions": positions.tolist(),
        "selection": selection,
        "capacity": capacity,
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.mean(np.abs(error))),
        "baseline_changed": False,
        "inference": "No p-values, intervals or significance/causal claims.",
        "model_scope": "Exploratory holdout fit, not an accepted/locked baseline candidate.",
    }
    if "ale_feature" in spec:
        if spec["ale_feature"] not in features or features.index(spec["ale_feature"]) not in positions:
            raise ValueError("ALE feature must be a selected predictor.")
        pos = int(np.flatnonzero(positions == features.index(spec["ale_feature"]))[0])
        bins = integer(spec.get("ale_bins", 10), "ale_bins", 2, 50)
        output["ale"] = _ale(model, train, pos, bins)
    elif "ale_bins" in spec:
        raise ValueError("ALE bins require an explicit ALE feature.")
    if "stress" in spec:
        scenarios = spec["stress"]
        if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 20:
            raise ValueError("Stress requires 1..20 fixed additive predictor-shock maps.")
        results = []
        for shock in scenarios:
            if not isinstance(shock, dict) or not shock or set(shock) - {features[i] for i in positions}:
                raise ValueError("Stress shocks may address selected predictors only, never targets.")
            changed = test.copy()
            for column, delta in shock.items():
                pos = int(np.flatnonzero(positions == features.index(column))[0])
                changed[:, pos] += number(delta, "shock")
            stressed = model.predict(changed)
            results.append(
                {
                    "scenario_position": len(results),
                    "mean_prediction_shift": float(np.mean(stressed - prediction)),
                    "rmse": float(np.sqrt(np.mean((stressed - y[split:]) ** 2))),
                }
            )
        output["stress"] = results
        output["adversarial_certification"] = False
    return output
