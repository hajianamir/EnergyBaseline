"""Independent numerical reproduction against manually constructed design sums of squares.

Run from the repository root with Python 3.12 and the unchanged requirements lock.
This is supplementary raw evidence, not a replacement for pytest or two-OS CI.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def execute(frame, method, names, targets, metadata, *, paired=False, **parameters):
    from energybaseline.core.schema import ColumnRole, ColumnSpec, suggest_dataset_schema
    from energybaseline.core.tcp import TestConfigPanel
    from energybaseline.core.vsg import VariableSelectionGate
    from energybaseline.statistics import run_statistical_plan

    plan = {
        "version": 1,
        "time": "time",
        "targets": targets,
        "metadata": metadata,
        "sampling": "independent_subjects" if paired else "independent",
        "assumption_justification": "Independent synthetic Gaussian units/subjects for numerical oracle reproduction.",
        "correction": "tcp",
        "tests": [{"id": "H1", "method": method, "columns": names, "parameters": parameters}],
    }
    schema = suggest_dataset_schema(frame, time_col="time", targets=targets)
    for name in metadata:
        schema = schema.with_column(ColumnSpec(name=name, role=ColumnRole.GROUP))
    vsg = VariableSelectionGate(frame, targets=targets, time_col="time", schema=schema)
    vsg.accept()
    tcp = TestConfigPanel(vsg, config={"tcp_alpha": 0.05, "tcp_correction": "holm"})
    tcp.register_extension_plan(plan)
    tcp.lock_preregistration()
    return run_statistical_plan(plan, frame, vsg=vsg, tcp=tcp)["frequentist"]


def compare(name, actual, expected):
    np.testing.assert_allclose(actual, expected, rtol=1e-8, atol=1e-10)
    return {
        "oracle": name,
        "actual": np.asarray(actual).tolist(),
        "expected": np.asarray(expected).tolist(),
        "passed": True,
    }


def main():
    receipts = []
    rng = np.random.default_rng(144)
    cell_size = 40
    # Balanced 2x2 factorial: direct cell-means decomposition, no patsy/statsmodels oracle.
    y = rng.normal(size=(2, 2, cell_size)) + np.array([[0, 0.4], [0.7, 1.6]])[:, :, None]
    flat = y.ravel()
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=len(flat), freq="h"),
            "y": flat,
            "a": np.repeat(["A", "B"], 2 * cell_size),
            "b": np.tile(np.repeat(["C", "D"], cell_size), 2),
        }
    )
    rows = execute(frame, "factorial_anova", ["y", "a", "b"], ["y"], ["a", "b"])
    mean = y.mean()
    cell = y.mean(axis=2)
    first, second = y.mean(axis=(1, 2)), y.mean(axis=(0, 2))
    ss = np.array(
        [
            2 * cell_size * np.sum((first - mean) ** 2),
            2 * cell_size * np.sum((second - mean) ** 2),
            cell_size * np.sum((cell - first[:, None] - second[None, :] + mean) ** 2),
        ]
    )
    residual = np.sum((y - cell[:, :, None]) ** 2)
    statistic = ss / (residual / (len(flat) - 4))
    receipts.append(compare("Balanced factorial F from cell means", [row["statistic"] for row in rows], statistic))
    receipts.append(
        compare(
            "Balanced factorial F survival probabilities",
            [row["p_input"] for row in rows],
            stats.f.sf(statistic, 1, len(flat) - 4),
        )
    )
    receipts.append(
        compare("Balanced factorial partial eta squared", [row["effect"] for row in rows], ss / (ss + residual))
    )

    # Repeated ANOVA: direct subject/condition/error decomposition.
    n, k = 30, 3
    matrix = rng.normal(size=(n, 1)) + np.arange(k) * 0.3 + rng.normal(size=(n, k))
    data = pd.DataFrame(
        {
            "time": pd.date_range("2025", periods=n * k, freq="h"),
            "y": matrix.ravel(),
            "s": np.repeat(np.arange(n), k),
            "c": np.tile(np.arange(k), n),
        }
    )
    row = execute(data, "repeated_anova", ["y", "s", "c"], ["y"], ["s", "c"], paired=True)[0]
    ss_conditions = n * np.sum((matrix.mean(axis=0) - matrix.mean()) ** 2)
    errors = matrix - matrix.mean(axis=0)[None, :] - matrix.mean(axis=1)[:, None] + matrix.mean()
    f_value = (ss_conditions / (k - 1)) / (np.sum(errors**2) / ((n - 1) * (k - 1)))
    receipts.append(
        compare(
            "Repeated-measures F from balanced subject residuals",
            [row["statistic"], row["p_input"]],
            [f_value, stats.f.sf(f_value, k - 1, (n - 1) * (k - 1))],
        )
    )

    # ANCOVA: reduced-vs-full linear algebra partial F.
    frame["x"] = rng.normal(size=len(frame))
    frame["y"] += frame.x * 0.8
    row = next(
        row for row in execute(frame, "ancova", ["y", "a", "x"], ["y", "x"], ["a"]) if row["term_code"] == "C(factor0)"
    )
    reduced = np.column_stack([np.ones(len(frame)), frame.x])
    full = np.column_stack([reduced, frame.a.eq("B").astype(int)])

    def rss(design):
        return float(np.sum((frame.y - design @ np.linalg.lstsq(design, frame.y, rcond=None)[0]) ** 2))

    f_value = (rss(reduced) - rss(full)) / (rss(full) / (len(frame) - 3))
    receipts.append(
        compare(
            "ANCOVA group partial F from reduced/full QR-equivalent least squares",
            [row["statistic"], row["p_input"]],
            [f_value, stats.f.sf(f_value, 1, len(frame) - 3)],
        )
    )

    # Two-group MANOVA is Hotelling's T2; independently derive Wilks and its exact F transform.
    frame["y2"] = 0.5 * frame.y + rng.normal(size=len(frame))
    row = execute(frame, "manova", ["y", "y2", "a"], ["y", "y2"], ["a"], dependent_count=2)[0]
    groups = [frame.loc[frame.a == name, ["y", "y2"]].to_numpy() for name in ["A", "B"]]
    a, b = groups
    error_ss = (a - a.mean(axis=0)).T @ (a - a.mean(axis=0)) + (b - b.mean(axis=0)).T @ (b - b.mean(axis=0))
    delta = a.mean(axis=0) - b.mean(axis=0)
    n = len(a) + len(b)
    hypothesis_ss = len(a) * len(b) / n * np.outer(delta, delta)
    wilks = np.linalg.det(error_ss) / np.linalg.det(error_ss + hypothesis_ss)
    t_squared = len(a) * len(b) / n * (delta @ np.linalg.solve(error_ss / (n - 2), delta))
    f_value = (n - 3) / (2 * (n - 2)) * t_squared
    receipts.append(
        compare(
            "Two-group MANOVA via Hotelling T2 and determinant ratio",
            [row["statistic"], row["p_input"], row["effect"]],
            [f_value, stats.f.sf(f_value, 2, n - 3), wilks],
        )
    )

    # Dunn ties: independent rank variance from the centered rank vector itself.
    observed = np.array([1, 1, 2, 2, 3, 2, 3, 3, 4, 4, 4, 4, 5, 5, 6])
    data = pd.DataFrame(
        {"time": pd.date_range("2025", periods=15, freq="h"), "y": observed, "g": np.repeat(["A", "B", "C"], 5)}
    )
    rows = execute(data, "dunn", ["y", "g"], ["y"], ["g"])
    ranks = stats.rankdata(observed).reshape(3, 5)
    rank_variance = ranks.ravel().var(ddof=1)
    expected = [
        (ranks[a].mean() - ranks[b].mean()) / np.sqrt(rank_variance * (1 / 5 + 1 / 5))
        for a, b in [(0, 1), (0, 2), (1, 2)]
    ]
    receipts.append(
        compare("Dunn tied-rank variance via pooled sample rank variance", [row["statistic"] for row in rows], expected)
    )
    output = {
        "passed": all(r["passed"] for r in receipts),
        "comparisons": receipts,
        "scope": "Supplementary genuine design oracles, not full-suite or two-OS substitutes",
    }
    (Path(__file__).parent / "numerical-oracles.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
