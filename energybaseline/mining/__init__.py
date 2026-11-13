"""Phase 5 mining & exploratory performance package: clustering validity/ARI, LTTB/M4 downsampling, descriptive EDA."""

from __future__ import annotations

from .clustering import (
    DISCOVERY_CLUSTER_PREFIXES,
    BootstrapStabilityResult,
    ClusterDiscoveryReport,
    ClusterValidityIndices,
    DescriptiveClusterLabels,
    RegimeLabelLeakageError,
    assert_no_cluster_labels_in_model_inputs,
    attach_descriptive_cluster_labels,
    compute_bootstrap_ari,
    compute_cluster_validity_indices,
    discover_operating_regimes,
    is_discovery_cluster_column_name,
    strip_discovery_cluster_labels,
)
from .downsample import DownsampleResult, lttb_downsample, m4_downsample
from .eda_attach import (
    DESCRIPTIVE_LABEL,
    DescriptiveOnlyViolationError,
    EDAAttachment,
    assert_descriptive_only,
    build_optional_eda_attachment,
)

__all__ = [
    "BootstrapStabilityResult",
    "ClusterDiscoveryReport",
    "ClusterValidityIndices",
    "DESCRIPTIVE_LABEL",
    "DISCOVERY_CLUSTER_PREFIXES",
    "DescriptiveClusterLabels",
    "DescriptiveOnlyViolationError",
    "DownsampleResult",
    "EDAAttachment",
    "RegimeLabelLeakageError",
    "assert_descriptive_only",
    "assert_no_cluster_labels_in_model_inputs",
    "attach_descriptive_cluster_labels",
    "build_optional_eda_attachment",
    "compute_bootstrap_ari",
    "compute_cluster_validity_indices",
    "discover_operating_regimes",
    "is_discovery_cluster_column_name",
    "lttb_downsample",
    "m4_downsample",
    "strip_discovery_cluster_labels",
]
