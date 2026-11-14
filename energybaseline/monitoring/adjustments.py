"""Routine driver predictions and documented non-routine static-factor adjustments."""

from dataclasses import dataclass
import numpy as np
from ..uq.calibration import vector


@dataclass(frozen=True)
class NonRoutineAdjustment:
    amount: float
    reason: str
    approved_by: str
    event_id: str
    standard_uncertainty: float = 0.0

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (self.reason, self.approved_by, self.event_id)):
            raise ValueError("Static factor events require reason, approver and event ID")
        if not np.isfinite([self.amount, self.standard_uncertainty]).all() or self.standard_uncertainty < 0:
            raise ValueError("Invalid adjustment magnitude")


def apply_adjustments(routine_prediction, events=()):
    routine = vector(routine_prediction)
    events = tuple(events)
    if any(not isinstance(e, NonRoutineAdjustment) for e in events):
        raise ValueError("Only documented non-routine adjustments accepted")
    if len({e.event_id for e in events}) != len(events):
        raise ValueError("Duplicate static event ID")
    # Amount is energy per reporting row; static uncertainty is fully correlated.
    delta = sum(e.amount for e in events)
    return routine + delta, {
        "routine": "locked model evaluated at reporting drivers",
        "non_routine": [e.__dict__.copy() for e in events],
        "per_row_adjustment": delta,
        "static_factor_sd": float(np.sqrt(sum(e.standard_uncertainty**2 for e in events))),
    }
