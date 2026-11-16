"""Offline train-only NLP models; never return documents, tokens or class labels."""

from __future__ import annotations

from contextlib import contextmanager
import warnings

import numpy as np
from sklearn.decomposition import NMF
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, f1_score, log_loss

from .common import chronological, columns, digest, fields, integer


@contextmanager
def converged_fit():
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            yield
    except ConvergenceWarning:
        raise ValueError("Text model failed to converge within its fixed resource budget.") from None


def text_split(frame, spec):
    chronological(frame, spec["time"])
    if spec["text"] == spec["time"]:
        raise ValueError("Text and chronological timestamp must be distinct columns.")
    text = columns(frame, [spec["text"]]).iloc[:, 0]
    if (
        len(frame) > 5000
        or not text.map(lambda x: isinstance(x, str) and 1 <= len(x.strip()) <= 10000).all()
        or sum(map(len, text)) > 2_000_000
    ):
        raise ValueError("Text requires 1..10000 characters per document, at most 5000 rows/two million characters.")
    split = integer(spec["train_rows"], "train_rows", 30, len(frame) - 1)
    gap = integer(spec.get("gap", 1), "gap", 1, split - 20)
    end = split - gap
    vocabulary_limit = integer(spec.get("max_features", 2000), "max_features", 10, 10000)
    vectorizer = TfidfVectorizer(max_features=vocabulary_limit, dtype=np.float64, strip_accents="unicode")
    train = vectorizer.fit_transform(text.iloc[:end])
    test = vectorizer.transform(text.iloc[split:])
    if min(train.shape) < 2 or train.nnz + test.nnz > 500000:
        raise ValueError("Text vocabulary or sparse resource capacity is invalid.")
    from ..core.ingest import sha256_dataframe

    audit = {
        "train_rows": end,
        "purged_rows": gap,
        "test_rows": len(frame) - split,
        "vocabulary_size": train.shape[1],
        "vocabulary_sha256": digest(sorted(vectorizer.vocabulary_)),
        "training_sha256": sha256_dataframe(frame.iloc[:end]),
        "test_documents_without_known_tokens": int(np.sum(test.getnnz(axis=1) == 0)),
        "fit_scope": "Vocabulary, IDF and model fitted on the training prefix only; gap and holdout excluded.",
        "plaintext_or_tokens_exported": False,
        "pretrained_weights_used": False,
        "baseline_changed": False,
        "model_persisted": False,
        "p_values_or_confidence_intervals_computed": False,
    }
    return train, test, end, split, audit


def text_classification(frame, spec):
    fields(spec, ("text", "target", "time", "train_rows"), ("gap", "max_features"))
    if len({spec["text"], spec["target"], spec["time"]}) != 3:
        raise ValueError("Text, target and time columns must be distinct.")
    target = columns(frame, [spec["target"]]).iloc[:, 0]
    if not target.map(lambda x: isinstance(x, str) and 1 <= len(x) <= 200).all():
        raise ValueError("Explicit nonmissing string class labels are required.")
    train, test, end, split, audit = text_split(frame, spec)
    labels = list(dict.fromkeys(target.iloc[:end]))
    if not 2 <= len(labels) <= 20:
        raise ValueError("Training requires 2..20 represented classes.")
    lookup = {label: i for i, label in enumerate(labels)}
    if any(label not in lookup for label in target.iloc[split:]):
        raise ValueError("Holdout classes must be represented in the training prefix.")
    y_train = target.iloc[:end].map(lookup).to_numpy(dtype=int)
    if min(np.bincount(y_train)) < 5:
        raise ValueError("Each class needs at least five training documents.")
    y_test = target.iloc[split:].map(lookup).to_numpy(dtype=int)
    with converged_fit():
        model = LogisticRegression(max_iter=500, random_state=42).fit(train, y_train)
    probability = model.predict_proba(test)
    predicted = np.argmax(probability, axis=1)
    audit.update(
        method="TF-IDF with multinomial logistic regression",
        class_count=len(labels),
        class_order="First occurrence in training; positions only, no class labels exported.",
        confusion_matrix_true_rows_predicted_columns=confusion_matrix(
            y_test, predicted, labels=np.arange(len(labels))
        ).tolist(),
        accuracy=float(np.mean(predicted == y_test)),
        macro_f1=float(f1_score(y_test, predicted, labels=np.arange(len(labels)), average="macro", zero_division=0)),
        log_loss=float(log_loss(y_test, probability, labels=np.arange(len(labels)))),
        model_sha256=digest({"coefficients": model.coef_.tolist(), "intercept": model.intercept_.tolist()}),
    )
    return audit


def text_topics(frame, spec):
    fields(spec, ("text", "time", "train_rows", "topics"), ("gap", "max_features"))
    train, test, end, split, audit = text_split(frame, spec)
    count = integer(spec["topics"], "topics", 2, min(20, min(train.shape) - 1))
    if count * (len(frame) + train.shape[1]) > 200000:
        raise ValueError("Topic factorization capacity exceeded.")
    with converged_fit():
        model = NMF(n_components=count, init="nndsvda", random_state=42, max_iter=1000, tol=1e-4)
        train_weights = model.fit_transform(train)
        test_weights = model.transform(test)
    # ||X-WH||^2 without materializing the sparse document-by-vocabulary matrix.
    residual = float(
        test.multiply(test).sum()
        - 2 * np.sum((test @ model.components_.T) * test_weights)
        + np.sum((test_weights @ (model.components_ @ model.components_.T)) * test_weights)
    )
    total = test_weights.sum(axis=1, keepdims=True)
    normalized = np.divide(test_weights, total, out=np.zeros_like(test_weights), where=total > 0)
    audit.update(
        method="TF-IDF nonnegative matrix factorization",
        topic_count=count,
        iterations=int(model.n_iter_),
        training_reconstruction_error=float(model.reconstruction_err_),
        test_reconstruction_frobenius=float(np.sqrt(max(0.0, residual))),
        mean_test_topic_weights=normalized.mean(axis=0).tolist(),
        test_documents_without_topic_weight=int(np.sum(total.ravel() == 0)),
        training_topic_mass=train_weights.sum(axis=0).tolist(),
        components_sha256=digest(model.components_.tolist()),
        topic_semantics="Unsupervised corpus-specific components, not human labels or pretrained semantic topics.",
    )
    return audit
