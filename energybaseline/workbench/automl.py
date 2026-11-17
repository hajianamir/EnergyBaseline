"""Actual Optuna model-family/hyperparameter search with untouched temporal outer holdout."""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .common import chronological, digest, fields, integer, numeric


FAMILIES = ("ridge", "random_forest", "extra_trees", "hist_gradient_boosting")


def estimator(configuration, seed):
    family = configuration["family"]
    if family == "ridge":
        model = Ridge(alpha=configuration["alpha"])
    elif family == "hist_gradient_boosting":
        model = HistGradientBoostingRegressor(
            max_iter=60,
            max_leaf_nodes=configuration["leaves"],
            learning_rate=configuration["learning_rate"],
            early_stopping=False,
            random_state=seed,
        )
    else:
        cls = RandomForestRegressor if family == "random_forest" else ExtraTreesRegressor
        model = cls(
            n_estimators=40,
            max_depth=configuration["depth"],
            min_samples_leaf=configuration["min_leaf"],
            n_jobs=1,
            random_state=seed,
        )
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), model)


def automl_search(frame, spec):
    fields(spec, ("time", "target", "features", "train_rows", "seed"), ("trials", "folds", "gap", "families", "engine"))
    if spec.get("engine", "optuna") != "optuna":
        raise ValueError("This backend is actual Optuna, not a substitute labeled as an external AutoML engine.")
    chronological(frame, spec["time"])
    features = spec["features"]
    if not isinstance(features, list) or spec["target"] in features or spec["time"] in features:
        raise ValueError("Predictors must exclude target/time.")
    X = numeric(frame, features, missing=True)
    y = numeric(frame, [spec["target"]]).ravel()
    split = integer(spec["train_rows"], "train_rows", 40, len(frame) - 1)
    seed = integer(spec["seed"], "seed", 0, 2**32 - 1)
    trials = integer(spec.get("trials", 8), "trials", 2, 40)
    count = integer(spec.get("folds", 3), "folds", 2, 5)
    gap = integer(spec.get("gap", 1), "gap", 0, 100)
    families = spec.get("families", list(FAMILIES))
    if (
        not isinstance(families, list)
        or not families
        or any(f not in FAMILIES for f in families)
        or len(set(families)) != len(families)
    ):
        raise ValueError("Select unique explicitly supported model families.")
    if split > 10000 or X.shape[1] > 40 or trials * count * split * X.shape[1] > 10_000_000:
        raise ValueError("AutoML work bound exceeded.")
    folds = list(TimeSeriesSplit(n_splits=count, gap=gap).split(X[:split]))
    if min(len(tr) for tr, _ in folds) < 10:
        raise ValueError("Initial temporal fold is too small.")
    import optuna
    from threadpoolctl import threadpool_limits

    def objective(trial):
        family = trial.suggest_categorical("family", families)
        configuration = {"family": family}
        if family == "ridge":
            configuration["alpha"] = trial.suggest_float("alpha", 0.001, 100, log=True)
        elif family == "hist_gradient_boosting":
            configuration.update(
                leaves=trial.suggest_int("leaves", 4, 24),
                learning_rate=trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
            )
        else:
            configuration.update(depth=trial.suggest_int("depth", 2, 10), min_leaf=trial.suggest_int("min_leaf", 1, 10))
        losses = []
        for train, validation in folds:
            if np.isnan(X[train]).all(axis=0).any():
                raise ValueError("An inner training fold has an entirely missing feature.")
            fitted = estimator(configuration, seed)
            fitted.fit(X[train], y[train])
            losses.append(float(np.mean((fitted.predict(X[validation]) - y[validation]) ** 2)))
        return float(np.mean(losses))

    with threadpool_limits(limits=1):
        study = optuna.create_study(
            direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed), pruner=optuna.pruners.NopPruner()
        )
        # Enqueue one genuine evaluation per family when budget permits; not installation badges.
        for family in families[:trials]:
            study.enqueue_trial({"family": family})
        study.optimize(objective, n_trials=trials, n_jobs=1, show_progress_bar=False)
        selected = dict(study.best_params)
        model = estimator(selected, seed)
        model.fit(X[:split], y[:split])
        predictions = model.predict(X[split:])
    if not np.isfinite(predictions).all():
        raise ValueError("AutoML returned nonfinite predictions.")
    # Missing predictors are represented explicitly for a finite training-data digest.
    data = np.column_stack([X[:split], y[:split]])
    training = [[float(v) if np.isfinite(v) else None for v in row] for row in data]
    return {
        "engine": "optuna",
        "engine_version": optuna.__version__,
        "trials_completed": len(study.trials),
        "inner_fold_count": count,
        "purge_gap": gap,
        "selected": selected,
        "inner_rmse": float(np.sqrt(study.best_value)),
        "test_rmse": float(np.sqrt(np.mean((predictions - y[split:]) ** 2))),
        "test_mae": float(np.mean(np.abs(predictions - y[split:]))),
        "train_rows": split,
        "test_rows": len(y) - split,
        "training_sha256": digest(training),
        "trials": [{"number": t.number, "parameters": t.params, "inner_mse": t.value} for t in study.trials],
        "fit_scope": "Every fold fits its own imputer/scaler/estimator; outer holdout used only once for final evaluation",
        "baseline_changed": False,
        "external_automl_engines_executed": False,
    }
