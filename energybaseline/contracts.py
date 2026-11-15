"""Versioned, JSON-native public analysis contracts; executable bundles stay separate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .core.pipeline import RunResult

ANALYSIS_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class AnalysisRequest:
    """Explicit roles and domain selection for public regression analysis."""

    time_col: str
    targets: tuple[str, ...]
    domain_pack: str = "neutral"
    config: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = ANALYSIS_CONTRACT_VERSION

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != ANALYSIS_CONTRACT_VERSION:
            raise ValueError("Unsupported analysis request version.")
        if not isinstance(self.time_col, str) or not self.time_col.strip():
            raise ValueError("An explicit timestamp column is required.")
        if (
            isinstance(self.targets, str)
            or not self.targets
            or any(not isinstance(t, str) or not t.strip() for t in self.targets)
        ):
            raise ValueError("At least one explicit target column is required.")
        if len(set(self.targets)) != len(self.targets) or self.time_col in self.targets:
            raise ValueError("Targets must be unique and distinct from the timestamp column.")
        from .core.domain import PUBLIC_DOMAIN_PACKS

        if self.domain_pack not in PUBLIC_DOMAIN_PACKS:
            raise ValueError("Public requests require a versioned built-in domain pack.")
        if not isinstance(self.config, Mapping):
            raise ValueError("Analysis config must be a mapping.")
        if "domain_pack" in self.config and self.config["domain_pack"] != self.domain_pack:
            raise ValueError("Specify a consistent domain_pack in the request.")
        # Snapshot caller-owned mutable mappings/lists, including nested settings.
        import copy

        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "config", copy.deepcopy(dict(self.config)))

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, Mapping):
            raise ValueError("Analysis request must be an object.")
        allowed = {"schema_version", "time_col", "targets", "domain_pack", "config"}
        if set(payload) - allowed:
            raise ValueError("Unknown analysis request fields.")
        if not {"time_col", "targets"} <= set(payload):
            raise ValueError("Both time_col and targets are required.")
        return cls(**payload)

    def to_dict(self):
        import copy

        return {
            "schema_version": self.schema_version,
            "time_col": self.time_col,
            "targets": list(self.targets),
            "domain_pack": self.domain_pack,
            "config": copy.deepcopy(dict(self.config)),
        }


@dataclass
class AnalysisResponse:
    """Typed in-process result plus a stable, non-executable export projection."""

    run: RunResult
    bundles: dict = field(repr=False)

    def to_summary(self):
        from .core.report_adapter import json_value

        return json_value(
            {
                "format": "energybaseline.analysis.v1",
                "schema_version": ANALYSIS_CONTRACT_VERSION,
                "domain_policy": self.run.config["domain_policy"],
                "cancelled": self.run.cancelled,
                "targets": {
                    target: {
                        "status": result.status,
                        "error": result.error,
                        "shortlist": result.shortlist,
                        "scoring": result.scoring,
                        "validation_contract": result.validation_contract,
                        "purge_gap_spec": result.purge_gap_spec,
                        "thresholds": result.thresholds,
                    }
                    for target, result in self.run.targets.items()
                },
            }
        )
