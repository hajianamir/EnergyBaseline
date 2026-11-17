"""Explicit schemas for empirical survival, graph analysis, retrieval and rating SVD."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from sklearn.feature_extraction.text import TfidfVectorizer

from .common import binary, columns, fields, integer, number, numeric


def survival_curve(frame, spec):
    fields(spec, ("duration", "event"))
    durations = numeric(frame, [spec["duration"]]).ravel()
    events = binary(frame, spec["event"])
    if not 1 <= len(durations) <= 10_000 or (durations < 0).any():
        raise ValueError("1..10000 nonnegative right-censoring durations are required.")
    order = np.argsort(durations, kind="stable")
    ordered = durations[order]
    unique, starts, counts = np.unique(ordered, return_index=True, return_counts=True)
    deaths = np.add.reduceat(events[order], starts)
    at_risk = len(durations)
    survival, median = 1.0, None
    curve = []
    for time, deaths_at_time, count in zip(unique, deaths, counts):
        # Events precede censoring at a tied time. No delayed entry or competing events are implied.
        survival *= 1 - int(deaths_at_time) / at_risk
        curve.append(
            {
                "time": float(time),
                "at_risk": at_risk,
                "events": int(deaths_at_time),
                "censored": int(count - deaths_at_time),
                "survival": float(survival),
            }
        )
        if median is None and survival <= 0.5:
            median = float(time)
        at_risk -= int(count)
    return {
        "method": "Kaplan-Meier",
        "rows": len(durations),
        "curve": curve,
        "median_survival": median,
        "inference": "None: empirical right-censored curve only, no p-values or confidence intervals.",
        "assumptions": "Independent noninformative right censoring; no left truncation or competing-risk interpretation.",
    }


def graph_profile(frame, spec):
    fields(spec, ("source", "target"), ("weight", "damping", "tolerance", "max_iterations", "path_source_position"))
    selected = columns(frame, [spec["source"], spec["target"]])
    if (
        selected.isna().any().any()
        or not len(selected)
        or not all(isinstance(v, str) for v in selected.to_numpy().ravel())
    ):
        raise ValueError("Graph edges require nonmissing string node identifiers.")
    ids = pd.concat([selected.iloc[:, 0], selected.iloc[:, 1]], ignore_index=True)
    codes, labels = pd.factorize(ids, sort=False)
    n, edge_count = len(labels), len(frame)
    if n > 10_000 or edge_count > 200_000:
        raise ValueError("Graph resource bound exceeded.")
    weights = numeric(frame, [spec["weight"]]).ravel() if "weight" in spec else np.ones(edge_count)
    if (weights <= 0).any():
        raise ValueError("Graph weights must be strictly positive distances/transition strengths.")
    adjacency = coo_matrix((weights, (codes[:edge_count], codes[edge_count:])), shape=(n, n)).tocsr()
    degree = np.asarray(adjacency.sum(axis=1)).ravel()
    damping = number(spec.get("damping", 0.85), "damping", 0.01, 0.99)
    tolerance = number(spec.get("tolerance", 1e-10), "tolerance", 1e-14, 0.01)
    maximum = integer(spec.get("max_iterations", 1000), "max_iterations", 1, 10_000)
    ranks = np.full(n, 1 / n)
    converged = False
    for iteration in range(maximum):
        updated = (1 - damping) / n + damping * (
            adjacency.T @ (ranks / np.where(degree > 0, degree, 1)) + ranks[degree == 0].sum() / n
        )
        if np.abs(updated - ranks).sum() <= tolerance:
            ranks, converged = updated, True
            break
        ranks = updated
    n_components, component_labels = connected_components(adjacency, directed=True, connection="weak")
    result = {
        "nodes": n,
        "edges": edge_count,
        "pagerank": ranks.tolist(),
        "converged": converged,
        "iterations": iteration + 1,
        "weak_components": int(n_components),
        "component_positions": component_labels.tolist(),
        "node_labels_exported": False,
        "node_order": "First occurrence across the source column followed by the target column.",
        "weighted_out_degree": degree.tolist(),
    }
    if "path_source_position" in spec:
        source = integer(spec["path_source_position"], "path_source_position", 0, n - 1)
        distances = dijkstra(adjacency, directed=True, indices=source)
        result["shortest_distances"] = [float(v) if np.isfinite(v) else None for v in distances]
    return result


def text_retrieval(frame, spec):
    fields(spec, ("text", "query"), ("top_k",))
    documents = columns(frame, [spec["text"]]).iloc[:, 0]
    if documents.isna().any() or not all(isinstance(v, str) for v in documents) or not isinstance(spec["query"], str):
        raise ValueError("Retrieval requires explicit nonmissing text documents and a string query.")
    if sum(map(len, documents)) > 10_000_000 or len(spec["query"]) > 10_000:
        raise ValueError("Text resource limit exceeded.")
    k = integer(spec.get("top_k", 5), "top_k", 1, 100)
    vectorizer = TfidfVectorizer(max_features=50_000, ngram_range=(1, 2), sublinear_tf=True)
    matrix = vectorizer.fit_transform(documents)
    query = vectorizer.transform([spec["query"]])
    scores = (matrix @ query.T).toarray().ravel()
    positions = np.argsort(-scores, kind="stable")[:k]
    return {
        "method": "word unigram/bigram TF-IDF cosine retrieval",
        "vocabulary_size": len(vectorizer.vocabulary_),
        "query_has_known_terms": bool(query.nnz),
        "matches": [{"row_position": int(i), "similarity": float(scores[i])} for i in positions if scores[i] > 0],
        "external_models_downloaded": False,
        "generative_or_semantic_embedding_claim": False,
    }


def rating_recommendations(frame, spec):
    fields(spec, ("user", "item", "rating", "train_rows", "rank", "user_position"), ("top_k",))
    selected = columns(frame, [spec["user"], spec["item"]])
    if selected.isna().any().any() or not all(isinstance(v, str) for v in selected.to_numpy().ravel()):
        raise ValueError("Recommendation user/item IDs must be nonmissing strings.")
    if selected.duplicated().any():
        raise ValueError("Repeated user/item pairs require explicit event aggregation before rating SVD.")
    ratings = numeric(frame, [spec["rating"]]).ravel()
    split = integer(spec["train_rows"], "train_rows", 2, len(frame) - 1)
    users, user_names = pd.factorize(selected.iloc[:split, 0], sort=False)
    items, item_names = pd.factorize(selected.iloc[:split, 1], sort=False)
    shape = len(user_names), len(item_names)
    if shape[0] * shape[1] > 250_000 or min(shape) < 2:
        raise ValueError("Rating SVD requires at least two users/items and at most 250000 matrix cells.")
    rank = integer(spec["rank"], "rank", 1, min(shape) - 1)
    user = integer(spec["user_position"], "user_position", 0, shape[0] - 1)
    k = integer(spec.get("top_k", 5), "top_k", 1, 100)
    mean = float(ratings[:split].mean())
    matrix = np.zeros(shape)
    matrix[users, items] = ratings[:split] - mean
    u, s, vt = np.linalg.svd(matrix, full_matrices=False)
    predicted = mean + (u[:, :rank] * s[:rank]) @ vt[:rank]
    candidates = np.argsort(-predicted[user], kind="stable")
    seen = set(items[users == user])
    recommendations = [
        {"item_position": int(i), "estimated_rating": float(predicted[user, i])} for i in candidates if i not in seen
    ][:k]
    held_users = pd.Index(user_names).get_indexer(selected.iloc[split:, 0])
    held_items = pd.Index(item_names).get_indexer(selected.iloc[split:, 1])
    known = (held_users >= 0) & (held_items >= 0)
    errors = predicted[held_users[known], held_items[known]] - ratings[split:][known]
    return {
        "method": "truncated SVD of observed globally-centered ratings; missing residuals filled with zero",
        "train_rows": split,
        "test_rows": len(frame) - split,
        "known_test_rows": int(known.sum()),
        "cold_start_rows": int((~known).sum()),
        "rmse_known_test": float(np.sqrt(np.mean(errors**2))) if len(errors) else None,
        "recommendations": recommendations,
        "rank": rank,
        "id_labels_exported": False,
        "implicit_feedback_or_ranking_validation_claim": False,
    }


def competing_risks(frame, spec):
    fields(spec, ("duration", "event_code"))
    duration = numeric(frame, [spec["duration"]]).ravel()
    codes = numeric(frame, [spec["event_code"]]).ravel()
    if (
        not 1 <= len(duration) <= 10000
        or (duration < 0).any()
        or (codes < 0).any()
        or not np.equal(codes, np.floor(codes)).all()
    ):
        raise ValueError("Competing risks require nonnegative durations and integer event codes (0=censored).")
    causes = np.unique(codes[codes > 0]).astype(int)
    if len(causes) > 20:
        raise ValueError("Competing risks are bounded to 20 event causes.")
    order = np.argsort(duration, kind="stable")
    duration, codes = duration[order], codes[order]
    times, starts, counts = np.unique(duration, return_index=True, return_counts=True)
    risk, survival, incidence = len(duration), 1.0, np.zeros(len(causes))
    curve = []
    for time, start, count in zip(times, starts, counts):
        group = codes[start : start + count]
        deaths = np.array([int((group == cause).sum()) for cause in causes])
        incidence += survival * deaths / risk
        survival *= 1 - float(deaths.sum()) / risk
        curve.append(
            {
                "time": float(time),
                "at_risk": risk,
                "event_free_survival": survival,
                "cumulative_incidence": incidence.tolist(),
            }
        )
        risk -= int(count)
    return {
        "method": "Aalen-Johansen competing-risk cumulative incidence",
        "causes": causes.tolist(),
        "curve": curve,
        "assumptions": "Mutually exclusive first events; independent right censoring; no delayed entry.",
        "inference": "Empirical point estimates only; no tests or confidence intervals.",
    }
