"""Clustering with validity indices, bootstrap ARI stability, and strict MT-5 model-input isolation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.metrics import (
    adjusted_rand_score,
    calinski_harabasz_score,
    davies_bouldin_score,
    silhouette_score,
)
from sklearn.mixture import GaussianMixture

DISCOVERY_CLUSTER_PREFIXES: tuple[str, ...] = (
    "__cluster_label__",
    "cluster_label",
    "cluster_regime",
    "mining_cluster",
    "discovered_regime",
    "regime_cluster",
)

_ATTR_DISCOVERY_COLS = "eb_discovery_cluster_columns"


class RegimeLabelLeakageError(ValueError):
    """Raised when exploratory clustering / discovered regime labels reach a model input (MT-5)."""


class DescriptiveClusterLabels(np.ndarray):
    """NumPy ndarray subclass tagging exploratory cluster labels so they can never enter a model."""

    _eb_descriptive_cluster_label: bool = True

    def __new__(cls, input_array: Any, *, method: str = "kmeans") -> "DescriptiveClusterLabels":
        obj = np.asarray(input_array, dtype=int).view(cls)
        obj.method = method
        return obj

    def __array_finalize__(self, obj: Any) -> None:
        if obj is None:
            return
        self.method = getattr(obj, "method", "kmeans")


def is_discovery_cluster_column_name(col_name: str) -> bool:
    """Return True if ``col_name`` matches a discovery clustering / exploratory regime naming token."""
    norm = str(col_name).strip().lower()
    return any(norm == p or norm.startswith(f"{p}_") or norm.startswith(p) for p in DISCOVERY_CLUSTER_PREFIXES)


def attach_descriptive_cluster_labels(
    df: pd.DataFrame,
    labels: Sequence[int] | np.ndarray,
    *,
    col_name: str = "__cluster_label__",
    method: str = "kmeans",
) -> pd.DataFrame:
    """Return a copy of ``df`` with tagged descriptive cluster labels for EDA/charts ONLY (MT-5)."""
    if not is_discovery_cluster_column_name(col_name):
        col_name = f"__cluster_label__{col_name}"
    out = df.copy()
    tagged = DescriptiveClusterLabels(labels, method=method)
    ser = pd.Series(np.asarray(tagged, dtype=int), index=out.index, name=col_name)
    ser.attrs["eb_descriptive_cluster_label"] = True
    ser.attrs["allowed_as_model_input"] = False
    out[col_name] = ser
    existing = set(out.attrs.get(_ATTR_DISCOVERY_COLS, ()))
    existing.add(col_name)
    out.attrs[_ATTR_DISCOVERY_COLS] = sorted(existing)
    return out


def strip_discovery_cluster_labels(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Strip any exploratory cluster-label columns from ``df`` and return ``(clean_df, removed_cols)``."""
    flagged = set(df.attrs.get(_ATTR_DISCOVERY_COLS, ()))
    removed: list[str] = []
    for c in df.columns:
        ser = df[c]
        if (
            c in flagged
            or is_discovery_cluster_column_name(str(c))
            or bool(ser.attrs.get("eb_descriptive_cluster_label", False))
            or ser.attrs.get("allowed_as_model_input", True) is False
        ):
            removed.append(str(c))
    if not removed:
        return df, []
    clean = df.drop(columns=removed).copy()
    clean.attrs.pop(_ATTR_DISCOVERY_COLS, None)
    return clean, removed


def assert_no_cluster_labels_in_model_inputs(X: Any) -> None:
    """Hard MT-5 boundary guard invoked by every F1..F10 model before fitting or predicting."""
    if isinstance(X, DescriptiveClusterLabels) or getattr(X, "_eb_descriptive_cluster_label", False):
        raise RegimeLabelLeakageError(
            "MT-5 violation: DescriptiveClusterLabels array passed directly as a model input."
        )
    if isinstance(X, pd.Series):
        if (
            is_discovery_cluster_column_name(str(X.name or ""))
            or bool(X.attrs.get("eb_descriptive_cluster_label", False))
            or X.attrs.get("allowed_as_model_input", True) is False
        ):
            raise RegimeLabelLeakageError(
                f"MT-5 violation: discovery cluster/regime Series '{X.name}' cannot be used as a model input."
            )
    if isinstance(X, pd.DataFrame):
        flagged = set(X.attrs.get(_ATTR_DISCOVERY_COLS, ()))
        leaked: list[str] = []
        for col in X.columns:
            ser = X[col]
            if (
                str(col) in flagged
                or is_discovery_cluster_column_name(str(col))
                or bool(ser.attrs.get("eb_descriptive_cluster_label", False))
                or ser.attrs.get("allowed_as_model_input", True) is False
            ):
                leaked.append(str(col))
        if leaked:
            raise RegimeLabelLeakageError(
                f"MT-5 violation: discovery cluster/regime label column(s) {leaked} are forbidden in model inputs."
            )


@dataclass(frozen=True)
class ClusterValidityIndices:
    """Internal cluster validity metrics on standardized feature space."""

    n_clusters: int
    n_samples: int
    silhouette: float
    calinski_harabasz: float
    davies_bouldin: float
    cluster_sizes: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "calinski_harabasz": round(self.calinski_harabasz, 6),
            "cluster_sizes": list(self.cluster_sizes),
            "davies_bouldin": round(self.davies_bouldin, 6),
            "n_clusters": self.n_clusters,
            "n_samples": self.n_samples,
            "silhouette": round(self.silhouette, 6),
        }


@dataclass(frozen=True)
class BootstrapStabilityResult:
    """Bootstrap Adjusted Rand Index (ARI) cluster stability assessment."""

    method: str
    n_clusters: int
    n_boot: int
    sample_fraction: float
    mean_ari: float
    median_ari: float
    std_ari: float
    ci_low: float
    ci_high: float
    stability_grade: str
    ari_samples: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ci_high": round(self.ci_high, 6),
            "ci_low": round(self.ci_low, 6),
            "mean_ari": round(self.mean_ari, 6),
            "median_ari": round(self.median_ari, 6),
            "method": self.method,
            "n_boot": self.n_boot,
            "n_clusters": self.n_clusters,
            "sample_fraction": self.sample_fraction,
            "stability_grade": self.stability_grade,
            "std_ari": round(self.std_ari, 6),
        }


@dataclass(frozen=True)
class ClusterDiscoveryReport:
    """Complete clustering validity + bootstrap ARI report with MT-5 label isolation metadata."""

    method: str
    selected_k: int
    predictor_columns: tuple[str, ...]
    validity: ClusterValidityIndices
    stability: BootstrapStabilityResult
    candidate_k_summary: tuple[dict[str, Any], ...]
    labels: DescriptiveClusterLabels
    allowed_as_model_input: bool = False
    classification: str = "descriptive_regime_discovery"

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_as_model_input": False,
            "candidate_k_summary": list(self.candidate_k_summary),
            "classification": self.classification,
            "method": self.method,
            "mt5_isolation_note": (
                "Regime discovery labels are strictly descriptive and barred from all F1..F10 model inputs (MT-5)."
            ),
            "predictor_columns": list(self.predictor_columns),
            "selected_k": self.selected_k,
            "stability": self.stability.to_dict(),
            "validity": self.validity.to_dict(),
        }


def compute_cluster_validity_indices(
    Z: np.ndarray,
    labels: Sequence[int] | np.ndarray,
    *,
    seed: int = 42,
) -> ClusterValidityIndices:
    """Compute Silhouette, Calinski-Harabasz, and Davies-Bouldin validity indices."""
    arr = np.asarray(Z, dtype=float)
    lab = np.asarray(labels, dtype=int).reshape(-1)
    if arr.ndim != 2 or arr.shape[0] != lab.size:
        raise ValueError("Z must be 2-D and match len(labels).")
    uniq, counts = np.unique(lab[lab >= 0], return_counts=True)
    k = int(uniq.size)
    if k < 2 or arr.shape[0] <= k:
        return ClusterValidityIndices(
            n_clusters=max(1, k),
            n_samples=int(arr.shape[0]),
            silhouette=0.0,
            calinski_harabasz=0.0,
            davies_bouldin=float("inf"),
            cluster_sizes=tuple(int(c) for c in counts),
        )
    sil_n = min(int(arr.shape[0]), 2500)
    sil = float(silhouette_score(arr, lab, sample_size=sil_n, random_state=seed))
    ch = float(calinski_harabasz_score(arr, lab))
    db = float(davies_bouldin_score(arr, lab))
    return ClusterValidityIndices(
        n_clusters=k,
        n_samples=int(arr.shape[0]),
        silhouette=sil,
        calinski_harabasz=ch,
        davies_bouldin=db,
        cluster_sizes=tuple(int(c) for c in counts),
    )


def _fit_predict_clusterer(Z: np.ndarray, k: int, method: str, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Fit clustering algorithm and return ``(labels, centroids)``."""
    m = method.strip().lower()
    if m == "kmeans":
        km = KMeans(n_clusters=k, n_init=8, random_state=seed)
        lab = km.fit_predict(Z)
        return lab, np.asarray(km.cluster_centers_, dtype=float)
    if m == "gmm":
        gm = GaussianMixture(n_components=k, covariance_type="full", n_init=3, random_state=seed)
        lab = gm.fit_predict(Z)
        return lab, np.asarray(gm.means_, dtype=float)
    if m in ("ward", "hierarchical"):
        ag = AgglomerativeClustering(n_clusters=k, linkage="ward")
        lab = ag.fit_predict(Z)
        centers = np.vstack([Z[lab == j].mean(axis=0) for j in range(k)])
        return lab, centers
    raise ValueError(f"Unsupported clustering method '{method}'; expected kmeans, gmm, or ward.")


def compute_bootstrap_ari(
    Z: np.ndarray,
    k: int,
    *,
    method: str = "kmeans",
    n_boot: int = 30,
    sample_fraction: float = 0.85,
    seed: int = 42,
) -> BootstrapStabilityResult:
    """Evaluate cluster replication stability via subsampled/bootstrapped Adjusted Rand Index (Hennig 2007)."""
    arr = np.asarray(Z, dtype=float)
    n = int(arr.shape[0])
    if k < 2 or n < max(12, 2 * k + 2):
        raise ValueError(f"Need k >= 2 and sufficient rows (got n={n}, k={k}).")
    frac = float(np.clip(sample_fraction, 0.5, 0.95))
    sub_n = max(2 * k + 1, int(round(frac * n)))
    rng = np.random.default_rng(seed)

    ref_labels, _ = _fit_predict_clusterer(arr, k, method, seed)
    boot_full_labels: list[np.ndarray] = []
    aris_vs_ref: list[float] = []

    for b in range(int(max(4, n_boot))):
        b_seed = int(rng.integers(1, 2**31 - 1))
        sub_idx = np.sort(rng.choice(n, size=sub_n, replace=False))
        Z_sub = arr[sub_idx]
        sub_lab, centers = _fit_predict_clusterer(Z_sub, k, method, b_seed)
        # Compare clustering directly on co-sampled observations + nearest-center out-of-bag projection
        ari_sub = float(adjusted_rand_score(ref_labels[sub_idx], sub_lab))
        dists = np.sum((arr[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        proj_lab = np.argmin(dists, axis=1)
        boot_full_labels.append(proj_lab)
        aris_vs_ref.append(0.5 * (ari_sub + float(adjusted_rand_score(ref_labels, proj_lab))))

    # Combine pairwise bootstrap ARIs with reference ARIs so symmetric isotropic noise yields ~0 ARI
    pairwise_aris: list[float] = []
    max_pairs = min(len(boot_full_labels) - 1, 25)
    for i in range(max_pairs):
        pairwise_aris.append(float(adjusted_rand_score(boot_full_labels[i], boot_full_labels[i + 1])))

    all_aris = np.asarray(pairwise_aris if pairwise_aris else aris_vs_ref, dtype=float)
    mean_ari = float(np.mean(all_aris))
    med_ari = float(np.median(all_aris))
    std_ari = float(np.std(all_aris, ddof=1)) if all_aris.size > 1 else 0.0
    ci_low, ci_high = (float(np.quantile(all_aris, 0.025)), float(np.quantile(all_aris, 0.975)))
    grade = "stable" if mean_ari >= 0.75 else ("moderate" if mean_ari >= 0.45 else "unstable")
    return BootstrapStabilityResult(
        method=method.strip().lower(),
        n_clusters=int(k),
        n_boot=int(max(4, n_boot)),
        sample_fraction=frac,
        mean_ari=mean_ari,
        median_ari=med_ari,
        std_ari=std_ari,
        ci_low=ci_low,
        ci_high=ci_high,
        stability_grade=grade,
        ari_samples=tuple(float(v) for v in all_aris),
    )


def discover_operating_regimes(
    df: pd.DataFrame,
    predictor_cols: Sequence[str],
    *,
    method: str = "kmeans",
    k_range: tuple[int, int] = (2, 5),
    n_boot: int = 25,
    seed: int = 42,
) -> ClusterDiscoveryReport:
    """Discover operating regimes strictly from exogenous predictors (never target), with validity + bootstrap ARI."""
    clean_df, _ = strip_discovery_cluster_labels(df)
    cols = [str(c) for c in predictor_cols if str(c) in clean_df.columns]
    if not cols:
        raise ValueError("No valid predictor columns supplied for operating-regime discovery.")
    work = clean_df[cols].dropna()
    if len(work) < 20:
        raise ValueError(f"Need at least 20 complete rows for clustering (got {len(work)}).")

    mu = work.mean(axis=0).to_numpy(dtype=float)
    sd = np.where(work.std(axis=0, ddof=0).to_numpy(dtype=float) > 1e-12, work.std(axis=0, ddof=0).to_numpy(dtype=float), 1.0)
    Z = (work.to_numpy(dtype=float) - mu) / sd

    k_min = max(2, int(k_range[0]))
    k_max = min(max(k_min, int(k_range[1])), max(2, len(work) // 8))
    candidates: list[dict[str, Any]] = []
    best_score = -float("inf")
    best_bundle: tuple[int, np.ndarray, ClusterValidityIndices, BootstrapStabilityResult] | None = None

    for k in range(k_min, k_max + 1):
        lab, _ = _fit_predict_clusterer(Z, k, method, seed)
        val = compute_cluster_validity_indices(Z, lab, seed=seed)
        stab = compute_bootstrap_ari(Z, k, method=method, n_boot=n_boot, seed=seed + k)
        # Joint selection criterion balancing cluster separation (silhouette) and replication stability (mean ARI)
        composite = 0.55 * stab.mean_ari + 0.45 * val.silhouette
        candidates.append(
            {
                "calinski_harabasz": round(val.calinski_harabasz, 4),
                "composite_selection_score": round(composite, 6),
                "davies_bouldin": round(val.davies_bouldin, 4),
                "k": k,
                "mean_bootstrap_ari": round(stab.mean_ari, 6),
                "silhouette": round(val.silhouette, 6),
                "stability_grade": stab.stability_grade,
            }
        )
        if composite > best_score:
            best_score = composite
            best_bundle = (k, lab, val, stab)

    assert best_bundle is not None
    sel_k, sel_lab, sel_val, sel_stab = best_bundle
    tagged_labels = DescriptiveClusterLabels(sel_lab, method=method)
    return ClusterDiscoveryReport(
        method=method.strip().lower(),
        selected_k=sel_k,
        predictor_columns=tuple(cols),
        validity=sel_val,
        stability=sel_stab,
        candidate_k_summary=tuple(candidates),
        labels=tagged_labels,
    )
