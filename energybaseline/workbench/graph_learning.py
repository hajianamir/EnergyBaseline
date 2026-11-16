"""Deterministic two-layer CPU GCN; explicit static transductive node classification."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score

from .common import columns, digest, fields, integer, number, numeric
from .deep import torch_scope


def normalized_adjacency(node_count, edges):
    if not isinstance(edges, list) or not 1 <= len(edges) <= 10000:
        raise ValueError("Declare 1..10000 undirected graph edges as node-position pairs.")
    adjacency = np.eye(node_count, dtype=np.float32)
    seen = set()
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2:
            raise ValueError("Each graph edge requires two integer node positions.")
        u, v = (integer(i, "node_position", 0, node_count - 1) for i in edge)
        key = tuple(sorted((u, v)))
        if u == v or key in seen:
            raise ValueError("Declare each non-self undirected edge once; self loops are added automatically.")
        seen.add(key)
        adjacency[u, v] = adjacency[v, u] = 1
    degree = adjacency.sum(axis=1)
    return adjacency / np.sqrt(degree[:, None] * degree[None, :])


def graph_node_classification(frame, spec):
    fields(spec, ("features", "target", "train_rows", "edges"), ("epochs", "width", "learning_rate", "seed"))
    if not 10 <= len(frame) <= 1000:
        raise ValueError("Static GCN supports 10..1000 explicitly ordered nodes.")
    if not isinstance(spec["features"], list) or spec["target"] in spec["features"]:
        raise ValueError("Graph node targets cannot be predictors.")
    x = numeric(frame, spec["features"])
    if x.shape[1] > 32:
        raise ValueError("GCN supports at most 32 numeric node features.")
    split = integer(spec["train_rows"], "train_rows", 6, len(frame) - 1)
    target = columns(frame, [spec["target"]]).iloc[:, 0]
    if not target.map(lambda value: isinstance(value, str) and 1 <= len(value) <= 200).all():
        raise ValueError("Explicit string labels are required; they are never exported.")
    labels = list(dict.fromkeys(target.iloc[:split]))
    if not 2 <= len(labels) <= 10:
        raise ValueError("GCN training requires 2..10 classes.")
    lookup = {value: i for i, value in enumerate(labels)}
    if any(value not in lookup for value in target.iloc[split:]):
        raise ValueError("Holdout labels must be represented in training.")
    y = target.map(lookup).to_numpy(dtype=np.int64)
    if min(np.bincount(y[:split])) < 3:
        raise ValueError("Each class requires at least three training nodes.")
    mean, scale = x[:split].mean(axis=0), x[:split].std(axis=0)
    if (scale <= 1e-12).any():
        raise ValueError("GCN predictors must have nonzero training variance.")
    x = (x - mean) / scale
    if np.abs(x).max() > 100:
        raise ValueError("Graph standardized node feature magnitude exceeds capacity.")
    adjacency = normalized_adjacency(len(frame), spec["edges"])
    epochs = integer(spec.get("epochs", 80), "epochs", 1, 300)
    width = integer(spec.get("width", 16), "width", 2, 64)
    if len(frame) ** 2 * width * epochs > 1_000_000_000:
        raise ValueError("Dense GCN message-passing budget exceeded.")
    learning_rate = number(spec.get("learning_rate", 0.02), "learning_rate", 0.0001, 0.1)
    seed = integer(spec.get("seed", 42), "seed", 0, 2**31 - 1)
    with torch_scope(seed) as torch:
        from torch import nn
        from torch.nn import functional as F

        class GCN(nn.Module):
            def __init__(self):
                super().__init__()
                self.first = nn.Linear(x.shape[1], width, bias=False)
                self.second = nn.Linear(width, len(labels), bias=False)

            def forward(self, features, graph):
                return graph @ self.second(F.relu(graph @ self.first(features)))

        model = GCN()
        features = torch.tensor(x, dtype=torch.float32)
        graph = torch.tensor(adjacency, dtype=torch.float32)
        train_labels = torch.tensor(y[:split], dtype=torch.long)
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=5e-4)
        initial_loss = float(F.cross_entropy(model(features, graph)[:split], train_labels).detach())
        model.train()
        for _ in range(epochs):
            optimizer.zero_grad()
            logits = model(features, graph)
            loss = F.cross_entropy(logits[:split], train_labels)
            if not torch.isfinite(loss):
                raise ValueError("GCN training produced a nonfinite loss.")
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            logits = model(features, graph)
            final_loss = float(F.cross_entropy(logits[:split], train_labels))
            probability = torch.softmax(logits[split:], dim=1).numpy()
            predicted = probability.argmax(axis=1)
            weights = {name: value.tolist() for name, value in model.state_dict().items()}
    return {
        "method": "Two-layer symmetric-normalized graph convolutional network",
        "protocol": "Static transductive: all declared topology/features visible during training; holdout labels excluded from loss. Not a temporal or unseen-node evaluation.",
        "nodes": len(frame),
        "undirected_edges": len(spec["edges"]),
        "train_nodes": split,
        "test_nodes": len(frame) - split,
        "classes": len(labels),
        "epochs": epochs,
        "initial_train_cross_entropy": initial_loss,
        "final_train_cross_entropy": final_loss,
        "test_accuracy": float(np.mean(predicted == y[split:])),
        "test_macro_f1": float(
            f1_score(y[split:], predicted, labels=np.arange(len(labels)), average="macro", zero_division=0)
        ),
        "confusion_matrix_true_rows_predicted_columns": confusion_matrix(
            y[split:], predicted, labels=np.arange(len(labels))
        ).tolist(),
        "weights_sha256": digest(weights),
        "graph_sha256": digest(spec["edges"]),
        "training_labels_sha256": digest(target.iloc[:split].tolist()),
        "predictions_sha256": digest(probability.tolist()),
        "node_labels_exported": False,
        "pretrained_weights_used": False,
        "paper_benchmark_parity_claimed": False,
        "p_values_or_confidence_intervals_computed": False,
        "baseline_changed": False,
        "model_persisted": False,
    }
