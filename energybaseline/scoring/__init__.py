"""Phase 6 Multi-Criteria Scoring, Registers, Tie-Break, Override & Validity Envelope Package (SC-1..SC-9)."""

from .composite import (
    CRITERIA_13,
    DEFAULT_WEIGHTS_13,
    CandidateCompositeEvaluation,
    PairwiseModelComparisonMatrix,
    WeightSensitivityReport,
    analyze_weight_sensitivity_20pct,
    compute_13_subscores,
    compute_pairwise_comparison_matrix,
    normalize_weights_13,
    rank_candidates_13,
    score_from_subscores,
)
from .envelope import (
    DriverBounds,
    ExtrapolationReport,
    ValidityDomainEnvelope,
    fit_validity_domain_envelope,
)
from .override import (
    ManualOverrideRecord,
    OverrideJustificationError,
    apply_manual_baseline_override,
)
from .registers import (
    RegisterComminglingError,
    RegisterEntry,
    SeparatedModelRegisters,
    ThresholdRelaxationAcknowledgement,
    ZeroQualifiedUnacknowledgedError,
    partition_model_registers,
)
from .tiebreak import (
    FAMILY_PARSIMONY_TIER,
    ParsimonyTieBreakDecision,
    deterministic_candidate_sort_key,
    get_parsimony_tier,
    select_parsimonious_mcs_leader,
)

__all__ = [
    "CRITERIA_13",
    "CandidateCompositeEvaluation",
    "DEFAULT_WEIGHTS_13",
    "DriverBounds",
    "ExtrapolationReport",
    "FAMILY_PARSIMONY_TIER",
    "ManualOverrideRecord",
    "OverrideJustificationError",
    "PairwiseModelComparisonMatrix",
    "ParsimonyTieBreakDecision",
    "RegisterComminglingError",
    "RegisterEntry",
    "SeparatedModelRegisters",
    "ThresholdRelaxationAcknowledgement",
    "ValidityDomainEnvelope",
    "WeightSensitivityReport",
    "ZeroQualifiedUnacknowledgedError",
    "analyze_weight_sensitivity_20pct",
    "apply_manual_baseline_override",
    "compute_13_subscores",
    "compute_pairwise_comparison_matrix",
    "deterministic_candidate_sort_key",
    "fit_validity_domain_envelope",
    "get_parsimony_tier",
    "normalize_weights_13",
    "partition_model_registers",
    "rank_candidates_13",
    "score_from_subscores",
    "select_parsimonious_mcs_leader",
]
