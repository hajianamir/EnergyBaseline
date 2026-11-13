"""Disjoint Model Registers (Shortlisted Top-5 / Qualified-Not-Shortlisted / Rejected / Skipped) & SC-9 Ack.

Prevents the critical Phase 6 auto-fail trigger:
    "Rejected models commingled with Qualified-not-shortlisted models."
Every evaluated model belongs to EXACTLY ONE of four mutually exclusive registers:
1. ``shortlisted_top5``
2. ``qualified_not_shortlisted``
3. ``rejected``
4. ``skipped``

Also enforces explicit engineer acknowledgement (``SC-9``) when zero models satisfy the configured
ASHRAE Guideline 14 / IPMVP thresholds before any diagnostic fallback or threshold relaxation proceeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Mapping, Sequence


class ZeroQualifiedUnacknowledgedError(PermissionError):
    """Raised when zero models qualify under ASHRAE/IPMVP thresholds and user has not acknowledged SC-9."""


class RegisterComminglingError(AssertionError):
    """Raised if any model appears in more than one register or if qualified models enter ``rejected``."""


@dataclass(frozen=True)
class RegisterEntry:
    """Typed row in one of the four strictly separated Phase 6 model registers."""

    model_name: str
    family: str
    register_bucket: str  # "shortlisted_top5" | "qualified_not_shortlisted" | "rejected" | "skipped"
    qualified: bool
    rank: int | None
    composite_score: float | None
    cvrmse: float | None
    nmbe: float | None
    r2: float | None
    in_mcs: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite_score": round(float(self.composite_score), 4) if self.composite_score is not None else None,
            "cvrmse": round(float(self.cvrmse), 4) if self.cvrmse is not None else None,
            "family": self.family,
            "in_mcs": bool(self.in_mcs),
            "model_name": self.model_name,
            "nmbe": round(float(self.nmbe), 4) if self.nmbe is not None else None,
            "qualified": bool(self.qualified),
            "r2": round(float(self.r2), 4) if self.r2 is not None else None,
            "rank": self.rank,
            "reason": self.reason,
            "register_bucket": self.register_bucket,
        }


@dataclass(frozen=True)
class ThresholdRelaxationAcknowledgement:
    """Audited engineer acknowledgement when zero models meet baseline qualification thresholds (SC-9)."""

    engineer_id: str
    acknowledged_at_utc: str
    justification: str
    original_thresholds: dict[str, float]
    recommended_remediations: tuple[str, ...]
    audit_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "acknowledged_at_utc": self.acknowledged_at_utc,
            "audit_sha256": self.audit_sha256,
            "engineer_id": self.engineer_id,
            "justification": self.justification,
            "original_thresholds": dict(self.original_thresholds),
            "recommended_remediations": list(self.recommended_remediations),
        }


@dataclass(frozen=True)
class SeparatedModelRegisters:
    """Mutually exclusive model registers with SC-9 zero-qualified warning & acknowledgement state."""

    shortlisted_top5: tuple[RegisterEntry, ...]
    qualified_not_shortlisted: tuple[RegisterEntry, ...]
    rejected: tuple[RegisterEntry, ...]
    skipped: tuple[RegisterEntry, ...]
    zero_qualified_warning: str | None
    requires_user_acknowledgement: bool
    acknowledgement: ThresholdRelaxationAcknowledgement | None = None

    def assert_disjoint(self) -> None:
        """Verify mathematical mutual exclusivity and that NO qualified model appears in ``rejected``."""
        s_names = {e.model_name for e in self.shortlisted_top5}
        q_names = {e.model_name for e in self.qualified_not_shortlisted}
        r_names = {e.model_name for e in self.rejected}
        k_names = {e.model_name for e in self.skipped}

        all_sets = [("shortlisted_top5", s_names), ("qualified_not_shortlisted", q_names), ("rejected", r_names), ("skipped", k_names)]
        for i in range(len(all_sets)):
            for j in range(i + 1, len(all_sets)):
                overlap = all_sets[i][1] & all_sets[j][1]
                if overlap:
                    raise RegisterComminglingError(
                        f"Register commingling between {all_sets[i][0]} and {all_sets[j][0]}: {sorted(overlap)}"
                    )

        for e in self.rejected:
            if e.qualified:
                raise RegisterComminglingError(
                    f"Qualified model '{e.model_name}' illegally placed in 'rejected' register!"
                )
        for e in (*self.shortlisted_top5, *self.qualified_not_shortlisted):
            if not e.qualified:
                raise RegisterComminglingError(
                    f"Unqualified model '{e.model_name}' illegally placed in '{e.register_bucket}' register!"
                )

    def assert_ready_for_selection(self) -> None:
        """Block selection when zero models qualified and SC-9 acknowledgement has not been recorded."""
        self.assert_disjoint()
        if self.requires_user_acknowledgement and self.acknowledgement is None:
            raise ZeroQualifiedUnacknowledgedError(
                self.zero_qualified_warning
                or "SC-9: Zero models met ASHRAE/IPMVP qualification thresholds; explicit user acknowledgement required."
            )

    def with_user_acknowledgement(
        self,
        *,
        engineer_id: str,
        justification: str,
        original_thresholds: Mapping[str, float] | None = None,
    ) -> "SeparatedModelRegisters":
        """Attach an audited SC-9 user acknowledgement when zero models passed strict thresholds."""
        eng = str(engineer_id).strip()
        just = str(justification).strip()
        if not eng:
            raise ValueError("engineer_id is required for SC-9 acknowledgement.")
        if len(just) < 12:
            raise ValueError("SC-9 acknowledgement justification must be at least 12 characters.")
        ts = datetime.now(timezone.utc).isoformat()
        thr = {str(k): float(v) for k, v in (original_thresholds or {}).items()}
        rems = (
            "Inspect static factors / non-routine events (occupancy, production shifts, meter rollover).",
            "Extend baseline calibration window to cover a full 12-month seasonal cycle.",
            "Include missing physical drivers (e.g., wet-bulb humidity, schedule indicator, throughput) via VSG.",
        )
        h = hashlib.sha256(f"{eng}|{ts}|{just}|{sorted(thr.items())}".encode("utf-8")).hexdigest()
        ack = ThresholdRelaxationAcknowledgement(
            engineer_id=eng,
            acknowledged_at_utc=ts,
            justification=just,
            original_thresholds=thr,
            recommended_remediations=rems,
            audit_sha256=h,
        )
        return SeparatedModelRegisters(
            shortlisted_top5=self.shortlisted_top5,
            qualified_not_shortlisted=self.qualified_not_shortlisted,
            rejected=self.rejected,
            skipped=self.skipped,
            zero_qualified_warning=self.zero_qualified_warning,
            requires_user_acknowledgement=self.requires_user_acknowledgement,
            acknowledgement=ack,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "acknowledgement": self.acknowledgement.to_dict() if self.acknowledgement is not None else None,
            "counts": {
                "qualified_not_shortlisted": len(self.qualified_not_shortlisted),
                "rejected": len(self.rejected),
                "shortlisted_top5": len(self.shortlisted_top5),
                "skipped": len(self.skipped),
            },
            "qualified_not_shortlisted": [e.to_dict() for e in self.qualified_not_shortlisted],
            "rejected": [e.to_dict() for e in self.rejected],
            "requires_user_acknowledgement": bool(self.requires_user_acknowledgement),
            "shortlisted_top5": [e.to_dict() for e in self.shortlisted_top5],
            "skipped": [e.to_dict() for e in self.skipped],
            "zero_qualified_warning": self.zero_qualified_warning,
        }


def partition_model_registers(
    ranked_fitted: Sequence[Mapping[str, Any]],
    unfitted_or_rejected: Sequence[Mapping[str, Any]] = (),
    *,
    top_k: int = 5,
    thresholds_label: str = "CV(RMSE)<=15%, |NMBE|<=5%, R2>=0.75",
) -> SeparatedModelRegisters:
    """Partition evaluated models into strictly disjoint registers without ever commingling qualified & rejected."""
    shortlisted: list[RegisterEntry] = []
    qual_not_short: list[RegisterEntry] = []
    rejected: list[RegisterEntry] = []
    skipped: list[RegisterEntry] = []
    seen: set[str] = set()

    qual_rank = 0
    for item in ranked_fitted:
        nm = str(item["model_name"])
        if nm in seen:
            continue
        seen.add(nm)
        is_qual = bool(item.get("qualified", False))
        fam = str(item.get("family", "F1"))
        sc = item.get("composite_score")
        cv = item.get("cvrmse")
        nmbe = item.get("nmbe")
        r2 = item.get("r2")
        mcs = bool(item.get("in_mcs", False))

        if is_qual:
            qual_rank += 1
            if qual_rank <= int(top_k):
                shortlisted.append(
                    RegisterEntry(
                        model_name=nm,
                        family=fam,
                        register_bucket="shortlisted_top5",
                        qualified=True,
                        rank=qual_rank,
                        composite_score=float(sc) if sc is not None else None,
                        cvrmse=float(cv) if cv is not None else None,
                        nmbe=float(nmbe) if nmbe is not None else None,
                        r2=float(r2) if r2 is not None else None,
                        in_mcs=mcs,
                        reason=str(item.get("reason") or f"Qualified & shortlisted at rank #{qual_rank}."),
                    )
                )
            else:
                qual_not_short.append(
                    RegisterEntry(
                        model_name=nm,
                        family=fam,
                        register_bucket="qualified_not_shortlisted",
                        qualified=True,
                        rank=qual_rank,
                        composite_score=float(sc) if sc is not None else None,
                        cvrmse=float(cv) if cv is not None else None,
                        nmbe=float(nmbe) if nmbe is not None else None,
                        r2=float(r2) if r2 is not None else None,
                        in_mcs=mcs,
                        reason=str(
                            item.get("reason")
                            or f"Meets all qualification thresholds ({thresholds_label}); ranked #{qual_rank} > Top-{top_k}."
                        ),
                    )
                )
        else:
            rejected.append(
                RegisterEntry(
                    model_name=nm,
                    family=fam,
                    register_bucket="rejected",
                    qualified=False,
                    rank=None,
                    composite_score=float(sc) if sc is not None else None,
                    cvrmse=float(cv) if cv is not None else None,
                    nmbe=float(nmbe) if nmbe is not None else None,
                    r2=float(r2) if r2 is not None else None,
                    in_mcs=mcs,
                    reason=str(item.get("reason") or f"Failed qualification thresholds ({thresholds_label})."),
                )
            )

    for u in unfitted_or_rejected:
        nm = str(u.get("model_name") or u.get("model"))
        if nm in seen:
            continue
        seen.add(nm)
        st = str(u.get("status", "rejected")).lower()
        fam = str(u.get("family", "F1"))
        rsn = str(u.get("reason", "Not fitted."))
        if "skip" in st:
            skipped.append(
                RegisterEntry(
                    model_name=nm,
                    family=fam,
                    register_bucket="skipped",
                    qualified=False,
                    rank=None,
                    composite_score=None,
                    cvrmse=None,
                    nmbe=None,
                    r2=None,
                    in_mcs=False,
                    reason=rsn,
                )
            )
        else:
            rejected.append(
                RegisterEntry(
                    model_name=nm,
                    family=fam,
                    register_bucket="rejected",
                    qualified=False,
                    rank=None,
                    composite_score=None,
                    cvrmse=None,
                    nmbe=None,
                    r2=None,
                    in_mcs=False,
                    reason=rsn,
                )
            )

    zero_qual = len(shortlisted) == 0 and len(qual_not_short) == 0
    warn_msg = (
        f"SC-9 WARNING: Zero candidate models satisfied {thresholds_label}. "
        "Review non-routine adjustments, missing drivers, or baseline period before acknowledging fallback."
        if zero_qual
        else None
    )
    regs = SeparatedModelRegisters(
        shortlisted_top5=tuple(shortlisted),
        qualified_not_shortlisted=tuple(qual_not_short),
        rejected=tuple(rejected),
        skipped=tuple(skipped),
        zero_qualified_warning=warn_msg,
        requires_user_acknowledgement=zero_qual,
    )
    regs.assert_disjoint()
    return regs
