"""Audited Manual Engineer Baseline Override (SC-7).

Allows an engineer to override the composite-score rank-1 recommendation in favor of another
candidate baseline ONLY with a documented engineering justification recorded in the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Mapping, Sequence


class OverrideJustificationError(ValueError):
    """Raised when a manual baseline override lacks required engineer identity or justification (SC-7)."""


@dataclass(frozen=True)
class ManualOverrideRecord:
    """Immutable audit record for an engineer-initiated baseline selection override (SC-7)."""

    default_recommended_model: str
    overridden_to_model: str
    default_composite_score: float
    overridden_composite_score: float
    composite_score_delta: float
    overridden_model_qualified: bool
    engineer_id: str
    justification: str
    overridden_at_utc: str
    audit_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_sha256": self.audit_sha256,
            "composite_score_delta": round(float(self.composite_score_delta), 4),
            "default_composite_score": round(float(self.default_composite_score), 4),
            "default_recommended_model": self.default_recommended_model,
            "engineer_id": self.engineer_id,
            "justification": self.justification,
            "overridden_at_utc": self.overridden_at_utc,
            "overridden_composite_score": round(float(self.overridden_composite_score), 4),
            "overridden_model_qualified": bool(self.overridden_model_qualified),
            "overridden_to_model": self.overridden_to_model,
        }


def apply_manual_baseline_override(
    candidates: Sequence[Mapping[str, Any]],
    *,
    requested_model_name: str,
    engineer_id: str,
    justification: str,
    allow_unqualified_with_ack: bool = False,
) -> ManualOverrideRecord:
    """Validate and record a manual baseline override with cryptographic audit hash (SC-7)."""
    if not candidates:
        raise ValueError("Cannot apply manual override on an empty candidate list.")

    eng = str(engineer_id or "").strip()
    just = str(justification or "").strip()
    target_name = str(requested_model_name or "").strip()
    if not eng:
        raise OverrideJustificationError("SC-7 violation: engineer_id must be non-empty for manual baseline override.")
    if len(just) < 15:
        raise OverrideJustificationError(
            "SC-7 violation: manual baseline override requires a documented engineering justification (>= 15 chars)."
        )

    by_name = {str(c["model_name"]): c for c in candidates}
    if target_name not in by_name:
        raise KeyError(f"Requested override model '{target_name}' not found among evaluated candidates.")

    default_cand = candidates[0]
    chosen_cand = by_name[target_name]
    is_qual = bool(chosen_cand.get("qualified", True))
    if not is_qual and not allow_unqualified_with_ack:
        raise OverrideJustificationError(
            f"SC-7/SC-9 violation: cannot override to unqualified model '{target_name}' without explicit SC-9 acknowledgement."
        )

    def_name = str(default_cand["model_name"])
    def_score = float(default_cand.get("composite_score") or 0.0)
    ch_score = float(chosen_cand.get("composite_score") or 0.0)
    ts = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(
        f"{def_name}->{target_name}|{def_score:.6f}->{ch_score:.6f}|{eng}|{ts}|{just}".encode("utf-8")
    ).hexdigest()

    return ManualOverrideRecord(
        default_recommended_model=def_name,
        overridden_to_model=target_name,
        default_composite_score=def_score,
        overridden_composite_score=ch_score,
        composite_score_delta=ch_score - def_score,
        overridden_model_qualified=is_qual,
        engineer_id=eng,
        justification=just,
        overridden_at_utc=ts,
        audit_sha256=digest,
    )
