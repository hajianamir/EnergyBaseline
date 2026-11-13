"""Phase 6 Leakage-Safe Time-Series Validation Package (VAL-1..VAL-6, Purge Gap, k_max)."""

from .ashrae14_metrics import ASHRAE14Metrics, ashrae14_limits_for_granularity, compute_ashrae14_oos_metrics
from .leakage_canary import DataLeakageDetectedError, LeakageCanaryResult, run_future_perturbation_canary
from .nested_hpo import NestedHPOResult, run_nested_walkforward_hpo
from .preprocessing import FoldPreprocessingSummary, InFoldPreprocessor, compute_k_max
from .purge import PurgeGapSpec, compute_purge_gap, compute_purge_gap_spec
from .splitting import (
    ChronologicalSplit,
    TimeSeriesShuffleForbiddenError,
    assert_strictly_chronological_folds,
    chronological_train_val_holdout_split,
)
from .walkforward import WalkForwardFold, WalkForwardMode, folds_as_index_tuples, generate_walkforward_folds

__all__ = [
    "ASHRAE14Metrics",
    "ChronologicalSplit",
    "DataLeakageDetectedError",
    "FoldPreprocessingSummary",
    "InFoldPreprocessor",
    "LeakageCanaryResult",
    "NestedHPOResult",
    "PurgeGapSpec",
    "TimeSeriesShuffleForbiddenError",
    "WalkForwardFold",
    "WalkForwardMode",
    "ashrae14_limits_for_granularity",
    "assert_strictly_chronological_folds",
    "chronological_train_val_holdout_split",
    "compute_ashrae14_oos_metrics",
    "compute_k_max",
    "compute_purge_gap",
    "compute_purge_gap_spec",
    "folds_as_index_tuples",
    "generate_walkforward_folds",
    "run_future_perturbation_canary",
    "run_nested_walkforward_hpo",
]
