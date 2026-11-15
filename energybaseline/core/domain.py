"""Versioned built-in domain policies; statistical metrics are not standards.

The old core entry point retains an explicitly recorded legacy policy for callers
that omit the new setting. Product defaults and the public SDK select neutral.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any
from types import MappingProxyType

from .auto import is_auto


@dataclass(frozen=True)
class DomainPolicy:
    key: str
    version: int = 1

    @property
    def energy(self) -> bool:
        return self.key in ("energy", "legacy_energy")

    @property
    def report_title(self) -> str:
        return "Energy Baseline Analytics Report" if self.energy else "Baseline Analytics Report"

    def to_dict(self) -> dict:
        return {"key": self.key, "version": self.version, "energy_features": self.energy}


# Built-in data policies only: importing arbitrary entry points is not a domain
# extension contract. Water/manufacturing deliberately impose no universal limits.
BUILTIN_DOMAIN_PACKS = MappingProxyType(
    {key: DomainPolicy(key) for key in ("neutral", "energy", "water", "manufacturing", "legacy_energy")}
)
PUBLIC_DOMAIN_PACKS = tuple(key for key in BUILTIN_DOMAIN_PACKS if key != "legacy_energy")


def resolve_domain(config: Mapping[str, Any], *, legacy_if_missing: bool = True) -> DomainPolicy:
    value = config.get("domain_pack", "legacy_energy" if legacy_if_missing else "neutral")
    key = "neutral" if is_auto(value) else str(value).strip().lower()
    if key not in BUILTIN_DOMAIN_PACKS:
        raise ValueError(
            "Unknown domain pack. Select a versioned built-in pack; executable plugin discovery is disabled."
        )
    return BUILTIN_DOMAIN_PACKS[key]
