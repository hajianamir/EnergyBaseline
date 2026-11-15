"""Train-only general tabular transforms and strictly past-only target encoding."""

from __future__ import annotations

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder, PowerTransformer, RobustScaler, StandardScaler

from .common import columns, digest, fields, integer, numeric


SCALERS = {
    "none": None,
    "standard": StandardScaler,
    "robust": RobustScaler,
    "minmax": MinMaxScaler,
    "power": PowerTransformer,
}


class TabularTransformer:
    """Fit on caller-provided training predictors only, never targets or validation rows.

    This is an exploratory transformer, not a replacement for baseline fold authorization.
    No fitted cell values, category labels, or imputation statistics are serialized by the runner.
    """

    def __init__(
        self, *, numeric_columns, categorical_columns, target_columns, imputation="median", scaling="standard"
    ):
        if imputation not in ("mean", "median", "most_frequent") or scaling not in SCALERS:
            raise ValueError("Unsupported imputation or scaling method.")
        self.numeric_columns = list(numeric_columns)
        self.categorical_columns = list(categorical_columns)
        self.target_columns = list(target_columns)
        names = self.numeric_columns + self.categorical_columns
        if not names or len(set(names)) != len(names) or set(names) & set(self.target_columns):
            raise ValueError("Predictors must be disjoint/unique and must never include declared targets.")
        self.imputation, self.scaling = imputation, scaling
        self.transformer_ = None

    def _validate(self, frame, *, fitting=False):
        selected = columns(frame, self.numeric_columns + self.categorical_columns)
        if self.numeric_columns:
            numeric(frame, self.numeric_columns, missing=True)
        for c in self.categorical_columns:
            if not all(isinstance(v, str) for v in frame[c].dropna()):
                raise ValueError("Categorical columns must contain strings or missing values.")
        if fitting and (selected.isna().all().any() or not len(selected)):
            raise ValueError("Entirely missing training columns cannot be learned; specify an upstream domain policy.")
        # sklearn object missing representation is explicitly normalized on a copy.
        result = selected.copy()
        for c in self.categorical_columns:
            result[c] = result[c].astype(object).where(result[c].notna(), np.nan)
        return result

    def fit(self, frame):
        self.transformer_ = None
        selected = self._validate(frame, fitting=True)
        width_bound = len(self.numeric_columns) + sum(frame[c].nunique() for c in self.categorical_columns)
        if width_bound > 2000 or width_bound * len(frame) > 2_000_000:
            raise ValueError("Dense transformed feature resource bound exceeded.")
        parts = []
        if self.numeric_columns:
            steps = [("impute", SimpleImputer(strategy=self.imputation))]
            if SCALERS[self.scaling] is not None:
                steps.append(("scale", SCALERS[self.scaling]()))
            parts.append(("numeric", Pipeline(steps), self.numeric_columns))
        if self.categorical_columns:
            parts.append(
                (
                    "categorical",
                    Pipeline(
                        [
                            ("impute", SimpleImputer(strategy="most_frequent")),
                            ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                        ]
                    ),
                    self.categorical_columns,
                )
            )
        transformer = ColumnTransformer(parts, remainder="drop")
        transformer.fit(selected)
        from ..core.ingest import sha256_dataframe

        self.training_sha256_ = sha256_dataframe(selected)
        self.transformer_ = transformer
        return self

    def transform(self, frame):
        if self.transformer_ is None:
            raise ValueError("Transformer must be successfully fitted on training rows first.")
        selected = self._validate(frame)
        width = len(self.transformer_.get_feature_names_out())
        if width * len(selected) > 2_000_000:
            raise ValueError("Transformed output resource bound exceeded.")
        result = self.transformer_.transform(selected)
        if not np.isfinite(result).all():
            raise ValueError("Transform produced nonfinite features.")
        return result


class PastOnlyTargetEncoder:
    """Expanding training encoding excludes the current and all future targets.

    Row order is supplied by the caller. Simultaneous timestamps are not supported;
    the job runner requires a unique strictly increasing time column. A fixed external
    initial prior is mandatory: a full-training target mean would leak future labels.
    """

    def __init__(self, *, prior, smoothing=10):
        from .common import number

        self.prior = number(prior, "prior")
        self.smoothing = number(smoothing, "smoothing", 0.000001)
        self.statistics_ = None

    def fit_transform(self, categories, target):
        self.statistics_ = None
        if len(categories) != len(target) or not len(target) or not np.isfinite(target).all():
            raise ValueError("Finite nonempty aligned training labels are required; targets are never imputed.")
        if not all(isinstance(v, str) for v in categories):
            raise ValueError("Target encoding requires nonmissing string categories.")
        statistics, result = {}, []
        for category, y in zip(categories, target):
            total, count = statistics.get(category, (0.0, 0))
            result.append((total + self.smoothing * self.prior) / (count + self.smoothing))
            statistics[category] = total + float(y), count + 1
        self.statistics_ = statistics
        return np.asarray(result)

    def transform(self, categories):
        if self.statistics_ is None:
            raise ValueError("Target encoder must be fitted first.")
        if not all(isinstance(v, str) for v in categories):
            raise ValueError("Target encoding requires nonmissing string categories.")
        result = []
        for c in categories:
            total, count = self.statistics_.get(c, (0.0, 0))
            result.append((total + self.smoothing * self.prior) / (count + self.smoothing))
        return np.asarray(result)


def transform_preview(frame, spec):
    fields(spec, ("numeric", "categorical", "targets", "train_rows"), ("imputation", "scaling"))
    split = integer(spec["train_rows"], "train_rows", 2, len(frame) - 1)
    transformer = TabularTransformer(
        numeric_columns=spec["numeric"],
        categorical_columns=spec["categorical"],
        target_columns=spec["targets"],
        imputation=spec.get("imputation", "median"),
        scaling=spec.get("scaling", "standard"),
    )
    transformer.fit(frame.iloc[:split])
    train = transformer.transform(frame.iloc[:split])
    test = transformer.transform(frame.iloc[split:])
    return {
        "train_rows": split,
        "test_rows": len(test),
        "features": train.shape[1],
        "training_sha256": transformer.training_sha256_,
        "fit_scope": "training predictors only",
        "transformed_train_sha256": digest(train.tolist()),
        "transformed_test_sha256": digest(test.tolist()),
        "raw_values_exported": False,
        "targets_imputed": False,
    }


def target_encoding_preview(frame, spec):
    from .common import chronological, number

    fields(spec, ("category", "target", "time", "train_rows", "prior"), ("smoothing",))
    chronological(frame, spec["time"])
    if spec["category"] in (spec["target"], spec["time"]):
        raise ValueError("Target/time cannot be the category to encode.")
    category = columns(frame, [spec["category"]]).iloc[:, 0].tolist()
    split = integer(spec["train_rows"], "train_rows", 2, len(frame) - 1)
    target = numeric(frame.iloc[:split], [spec["target"]]).ravel()
    encoder = PastOnlyTargetEncoder(prior=number(spec["prior"], "prior"), smoothing=spec.get("smoothing", 10))
    train = encoder.fit_transform(category[:split], target)
    heldout = encoder.transform(category[split:])
    return {
        "train_rows": split,
        "test_rows": len(frame) - split,
        "training_encoding_sha256": digest(train.tolist()),
        "heldout_encoding_sha256": digest(heldout.tolist()),
        "own_or_future_training_labels_used": False,
        "heldout_labels_used_for_encoding": False,
        "prior_contract": "Caller-supplied external constant, never full-training target mean.",
    }


def multi_target_consensus(frame, spec):
    from .common import chronological

    fields(spec, ("features", "targets", "time", "train_rows", "feature_count"))
    chronological(frame, spec["time"])
    if set(spec["features"]) & set(spec["targets"]) or spec["time"] in spec["features"]:
        raise ValueError("Targets/time must not enter the consensus predictors.")
    split = integer(spec["train_rows"], "train_rows", 40, len(frame) - 1)
    X = numeric(frame.iloc[:split], spec["features"])
    Y = numeric(frame.iloc[:split], spec["targets"])
    if Y.shape[1] < 2:
        raise ValueError("Multi-target consensus requires at least two targets.")
    count = integer(spec["feature_count"], "feature_count", 1, min(X.shape[1], split // 20))
    if (X.std(axis=0) == 0).any() or (Y.std(axis=0) == 0).any():
        raise ValueError("Constant training predictors/targets have undefined association ranks.")
    from scipy.stats import rankdata

    correlations = np.corrcoef(X.T, Y.T)[: X.shape[1], X.shape[1] :]
    ranks = np.column_stack([rankdata(-abs(correlations[:, j]), method="average") for j in range(Y.shape[1])])
    mean_rank = ranks.mean(axis=1)
    return {
        "method": "Equal-target Borda consensus of absolute training Pearson association ranks",
        "mean_ranks": mean_rank.tolist(),
        "per_target_ranks": ranks.tolist(),
        "selected_feature_positions": np.argsort(mean_rank, kind="stable")[:count].tolist(),
        "train_rows": split,
        "test_rows": len(frame) - split,
        "inference": "Descriptive ranking only, no association p-values or causal interpretation.",
    }
