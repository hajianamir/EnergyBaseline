"""Reference-trained fuzzy entity candidates; never automatically merge records."""

from __future__ import annotations

import re
import unicodedata

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .common import columns, fields, integer, number


def _strings(frame, column):
    values = columns(frame, [column]).iloc[:, 0]
    if values.isna().any() or not all(isinstance(v, str) for v in values):
        raise ValueError("Entity matching requires nonmissing strings.")
    if sum(map(len, values)) > 2_000_000:
        raise ValueError("Entity matching text resource limit exceeded.")
    result = [re.sub(r"\s+", " ", unicodedata.normalize("NFKC", v).casefold()).strip() for v in values]
    if any(not v for v in result):
        raise ValueError("Empty normalized entity strings are not matchable.")
    return result


def entity_candidates(frame, reference, spec):
    fields(spec, ("column", "reference_column", "threshold"), ("top_k",))
    incoming = _strings(frame, spec["column"])
    existing = _strings(reference, spec["reference_column"])
    threshold = number(spec["threshold"], "threshold", 0.01, 1)
    k = integer(spec.get("top_k", 3), "top_k", 1, 10)
    if len(incoming) > 2000 or len(existing) > 20_000:
        raise ValueError("Entity matching accepts at most 2000 incoming and 20000 reference rows.")
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), max_features=50_000)
    matrix = vectorizer.fit_transform(existing)
    transformed = vectorizer.transform(incoming)
    results = []
    # Batching avoids constructing a potentially large all-pairs dense similarity matrix.
    for start in range(0, len(incoming), 32):
        scores = (transformed[start : start + 32] @ matrix.T).toarray()
        for offset, row in enumerate(scores):
            candidates = np.argsort(-row, kind="stable")[:k]
            results.append(
                {
                    "row_position": start + offset,
                    "candidates": [
                        {"reference_position": int(i), "cosine_similarity": float(row[i])}
                        for i in candidates
                        if row[i] >= threshold - 1e-12
                    ],
                }
            )
    return {
        "normalization": "NFKC, Unicode casefold, trim/collapse whitespace",
        "method": "reference-fitted character unigram/trigram TF-IDF candidate retrieval",
        "vocabulary_size": len(vectorizer.vocabulary_),
        "matches": results,
        "automatic_merges": 0,
        "probabilistic_match_confidence_claim": False,
        "warning": "Similarity is not identity; review candidates and domain-specific false merges.",
    }
