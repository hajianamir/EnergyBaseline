"""Immutable provenance records and stage hash-chain ledger (VSG-6, PIPE-2).

Architecture & separation invariants:
* Every configuration, variable-selection, test-panel, and stage artifact carries a
  :class:`ProvenanceRecord` with ``source``, ``phase`` strictly in
  ``{"pre_test", "post_test"}``, ``dataset_sha256``, and deterministic ``record_sha256``.
* Core provenance models domain state exclusively via ``phase in {"pre_test", "post_test"}``
  and ``requires_limitations_disclosure``. Presentation-specific Limitations labels belong
  strictly to the desktop UI layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any


class ProvenanceValidationError(ValueError):
    """Raised when a provenance record violates schema or phase invariants."""


class ProvenanceSource(str, Enum):
    """Origin of a variable, test-panel, or stage decision."""

    AUTO_RULE = "auto_rule"
    SCHEMA_SUGGESTION = "schema_suggestion"
    USER_PRE_TEST = "user_pre_test"
    USER_MODIFICATION = "user_modification"
    PRESET = "preset"
    CROSS_TARGET_COPY = "cross_target_copy"
    PIPELINE_STAGE = "pipeline_stage"


class ProvenancePhase(str, Enum):
    """Temporal governance phase relative to inferential test execution."""

    PRE_TEST = "pre_test"
    POST_TEST = "post_test"


VALID_PROVENANCE_SOURCES: frozenset[str] = frozenset(s.value for s in ProvenanceSource)
VALID_PROVENANCE_PHASES: frozenset[str] = frozenset(p.value for p in ProvenancePhase)


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")


def _sha256_hex(payload: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def normalize_phase(phase: str | ProvenancePhase) -> str:
    val = phase.value if isinstance(phase, ProvenancePhase) else str(phase or "").strip()
    if val not in VALID_PROVENANCE_PHASES:
        raise ProvenanceValidationError(
            f"Invalid provenance phase {phase!r}; expected one of {sorted(VALID_PROVENANCE_PHASES)}."
        )
    return val


def normalize_source(source: str | ProvenanceSource) -> str:
    val = source.value if isinstance(source, ProvenanceSource) else str(source or "").strip()
    if val not in VALID_PROVENANCE_SOURCES:
        raise ProvenanceValidationError(
            f"Invalid provenance source {source!r}; expected one of {sorted(VALID_PROVENANCE_SOURCES)}."
        )
    return val


@dataclass(frozen=True)
class ProvenanceRecord:
    """Single immutable provenance event anchored to ``dataset_sha256`` and ``previous_hash``."""

    seq: int
    entity_type: str
    entity_id: str
    target: str
    source: str
    phase: str
    dataset_sha256: str
    previous_value: Any = None
    new_value: Any = None
    reason: str = ""
    previous_record_sha256: str = ""
    record_sha256: str = ""

    def __post_init__(self) -> None:
        src = normalize_source(self.source)
        phs = normalize_phase(self.phase)
        if not (self.entity_type or "").strip():
            raise ProvenanceValidationError("entity_type must not be empty.")
        if not (self.entity_id or "").strip():
            raise ProvenanceValidationError("entity_id must not be empty.")
        object.__setattr__(self, "source", src)
        object.__setattr__(self, "phase", phs)
        computed = _sha256_hex(self._canonical_body())
        if self.record_sha256 and self.record_sha256 != computed:
            raise ProvenanceValidationError(
                f"ProvenanceRecord SHA-256 mismatch for {self.entity_type}:{self.entity_id} "
                f"(expected {computed}, got {self.record_sha256})."
            )
        object.__setattr__(self, "record_sha256", computed)

    def _canonical_body(self) -> dict[str, Any]:
        return {
            "dataset_sha256": str(self.dataset_sha256 or ""),
            "entity_id": str(self.entity_id),
            "entity_type": str(self.entity_type),
            "new_value": self.new_value,
            "phase": str(self.phase),
            "previous_record_sha256": str(self.previous_record_sha256 or ""),
            "previous_value": self.previous_value,
            "reason": str(self.reason or ""),
            "seq": int(self.seq),
            "source": str(self.source),
            "target": str(self.target or ""),
        }

    @property
    def requires_limitations_disclosure(self) -> bool:
        """True when a modification occurred after inferential testing began."""
        return self.phase == ProvenancePhase.POST_TEST.value

    def to_dict(self) -> dict[str, Any]:
        out = self._canonical_body()
        out["record_sha256"] = self.record_sha256
        out["requires_limitations_disclosure"] = self.requires_limitations_disclosure
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProvenanceRecord":
        return cls(
            seq=int(data["seq"]),
            entity_type=str(data["entity_type"]),
            entity_id=str(data["entity_id"]),
            target=str(data.get("target", "") or ""),
            source=str(data["source"]),
            phase=str(data["phase"]),
            dataset_sha256=str(data.get("dataset_sha256", "") or ""),
            previous_value=data.get("previous_value"),
            new_value=data.get("new_value"),
            reason=str(data.get("reason", "") or ""),
            previous_record_sha256=str(data.get("previous_record_sha256", "") or ""),
            record_sha256=str(data.get("record_sha256", "") or ""),
        )


@dataclass(frozen=True)
class StageArtifactReceipt:
    """Immutable stage contract receipt linking dataset SHA-256 across pipeline stages (PIPE-1, PIPE-2)."""

    stage_name: str
    target: str
    dataset_sha256: str
    upstream_stage_sha256: str
    stage_payload_sha256: str
    phase: str = ProvenancePhase.PRE_TEST.value
    stage_sha256: str = ""

    def __post_init__(self) -> None:
        phs = normalize_phase(self.phase)
        if not (self.stage_name or "").strip():
            raise ProvenanceValidationError("stage_name must not be empty.")
        if not (self.dataset_sha256 or "").strip():
            raise ProvenanceValidationError("dataset_sha256 must not be empty for StageArtifactReceipt.")
        object.__setattr__(self, "phase", phs)
        computed = _sha256_hex(
            {
                "dataset_sha256": self.dataset_sha256,
                "phase": self.phase,
                "stage_name": self.stage_name,
                "stage_payload_sha256": self.stage_payload_sha256,
                "target": self.target,
                "upstream_stage_sha256": self.upstream_stage_sha256,
            }
        )
        if self.stage_sha256 and self.stage_sha256 != computed:
            raise ProvenanceValidationError(
                f"StageArtifactReceipt SHA-256 mismatch for stage '{self.stage_name}'."
            )
        object.__setattr__(self, "stage_sha256", computed)

    def to_dict(self) -> dict[str, str]:
        return {
            "dataset_sha256": self.dataset_sha256,
            "phase": self.phase,
            "stage_name": self.stage_name,
            "stage_payload_sha256": self.stage_payload_sha256,
            "stage_sha256": self.stage_sha256,
            "target": self.target,
            "upstream_stage_sha256": self.upstream_stage_sha256,
        }


class ProvenanceLedger:
    """Append-only hash-chained ledger of provenance records and stage receipts."""

    def __init__(self, dataset_sha256: str = "", records: Sequence[ProvenanceRecord] | None = None) -> None:
        self.dataset_sha256: str = str(dataset_sha256 or "")
        self._records: list[ProvenanceRecord] = []
        self._stage_receipts: list[StageArtifactReceipt] = []
        if records:
            for r in records:
                self._append_verified(r)

    def _append_verified(self, rec: ProvenanceRecord) -> None:
        expected_prev = self._records[-1].record_sha256 if self._records else ""
        if rec.previous_record_sha256 != expected_prev:
            raise ProvenanceValidationError(
                f"Broken provenance hash chain at seq={rec.seq}: expected previous_record_sha256={expected_prev!r}, "
                f"got {rec.previous_record_sha256!r}."
            )
        if rec.seq != len(self._records) + 1:
            raise ProvenanceValidationError(
                f"Non-sequential provenance seq={rec.seq}; expected {len(self._records) + 1}."
            )
        self._records.append(rec)

    def append(
        self,
        *,
        entity_type: str,
        entity_id: str,
        source: str | ProvenanceSource,
        phase: str | ProvenancePhase = ProvenancePhase.PRE_TEST,
        target: str = "",
        previous_value: Any = None,
        new_value: Any = None,
        reason: str = "",
        dataset_sha256: str | None = None,
    ) -> ProvenanceRecord:
        prev_hash = self._records[-1].record_sha256 if self._records else ""
        ds_sha = self.dataset_sha256 if dataset_sha256 is None else str(dataset_sha256)
        rec = ProvenanceRecord(
            seq=len(self._records) + 1,
            entity_type=entity_type,
            entity_id=entity_id,
            target=target,
            source=normalize_source(source),
            phase=normalize_phase(phase),
            dataset_sha256=ds_sha,
            previous_value=previous_value,
            new_value=new_value,
            reason=reason,
            previous_record_sha256=prev_hash,
        )
        self._records.append(rec)
        return rec

    def record_stage(
        self,
        *,
        stage_name: str,
        payload: Any,
        target: str = "",
        upstream_stage_sha256: str = "",
        phase: str | ProvenancePhase = ProvenancePhase.PRE_TEST,
    ) -> StageArtifactReceipt:
        ds_sha = self.dataset_sha256 or "unspecified_dataset_sha256"
        payload_sha = _sha256_hex(payload)
        receipt = StageArtifactReceipt(
            stage_name=stage_name,
            target=target,
            dataset_sha256=ds_sha,
            upstream_stage_sha256=upstream_stage_sha256,
            stage_payload_sha256=payload_sha,
            phase=normalize_phase(phase),
        )
        self._stage_receipts.append(receipt)
        self.append(
            entity_type="pipeline_stage",
            entity_id=stage_name,
            source=ProvenanceSource.PIPELINE_STAGE,
            phase=phase,
            target=target,
            previous_value=upstream_stage_sha256 or None,
            new_value=receipt.stage_sha256,
            reason=f"stage receipt for {stage_name}",
            dataset_sha256=ds_sha,
        )
        return receipt

    @property
    def records(self) -> tuple[ProvenanceRecord, ...]:
        return tuple(self._records)

    @property
    def stage_receipts(self) -> tuple[StageArtifactReceipt, ...]:
        return tuple(self._stage_receipts)

    @property
    def ledger_sha256(self) -> str:
        return _sha256_hex(
            {
                "dataset_sha256": self.dataset_sha256,
                "records": [r.to_dict() for r in self._records],
                "stage_receipts": [s.to_dict() for s in self._stage_receipts],
            }
        )

    def post_test_records(self, target: str | None = None) -> list[ProvenanceRecord]:
        return [
            r
            for r in self._records
            if r.phase == ProvenancePhase.POST_TEST.value and (target is None or r.target in ("", target))
        ]

    def has_post_test_modifications(self, target: str | None = None) -> bool:
        return len(self.post_test_records(target)) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_sha256": self.dataset_sha256,
            "has_post_test_modifications": self.has_post_test_modifications(),
            "ledger_sha256": self.ledger_sha256,
            "records": [r.to_dict() for r in self._records],
            "stage_receipts": [s.to_dict() for s in self._stage_receipts],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProvenanceLedger":
        ds_sha = str(data.get("dataset_sha256", "") or "")
        recs = [ProvenanceRecord.from_dict(r) for r in data.get("records", [])]
        ledger = cls(dataset_sha256=ds_sha, records=recs)
        for sr in data.get("stage_receipts", []):
            ledger._stage_receipts.append(
                StageArtifactReceipt(
                    stage_name=str(sr["stage_name"]),
                    target=str(sr.get("target", "") or ""),
                    dataset_sha256=str(sr["dataset_sha256"]),
                    upstream_stage_sha256=str(sr.get("upstream_stage_sha256", "") or ""),
                    stage_payload_sha256=str(sr["stage_payload_sha256"]),
                    phase=str(sr.get("phase", ProvenancePhase.PRE_TEST.value)),
                    stage_sha256=str(sr.get("stage_sha256", "") or ""),
                )
            )
        expected_sha = str(data.get("ledger_sha256", "") or "")
        if expected_sha and ledger.ledger_sha256 != expected_sha:
            raise ProvenanceValidationError(
                f"ProvenanceLedger SHA-256 mismatch (expected {expected_sha}, got {ledger.ledger_sha256})."
            )
        return ledger
