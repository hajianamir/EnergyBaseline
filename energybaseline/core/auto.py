"""AUTO-resolution engine, inspector, and deterministic SHA-256 log hash (AUTO-1..6, AC-33, G-6).

Invariants enforced:
* Every configurable setting defaults to ``AUTO`` unless explicitly overridden by the user.
* Every ``AUTO`` resolution MUST inspect empirical data/context signals and record both
  ``signals`` and ``rule``; hidden hardcoded constants without empirical signals raise
  :class:`AutoHiddenConstantError`.
* ``compute_auto_log_hash`` produces a deterministic canonical SHA-256 digest that is
  byte-identical across repeated pipeline runs on the same inputs (wall-clock timestamps
  never enter the canonical hash).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from typing import Any

from .schema import AUTO, SETTINGS_BY_KEY

log = logging.getLogger("energybaseline.auto")

_FORBIDDEN_CONSTANT_SIGNALS: frozenset[str] = frozenset(
    {
        "",
        "default",
        "constant",
        "hardcoded",
        "hidden constant",
        "static",
        "none",
        "n/a",
        "default engineering limit",
        "default numerical tolerance",
    }
)


class AutoHiddenConstantError(ValueError):
    """Raised when an AUTO decision emits no empirical data signals (violates AC-33 / Gate G3)."""


def is_auto(v: Any) -> bool:
    """Return True when setting value ``v`` requests AUTO resolution."""
    return v is None or (isinstance(v, str) and (v.strip() == "" or v.strip().upper() == AUTO))


def coerce(key: str, v: Any) -> Any:
    """Coerce a user-supplied override value according to ``SETTINGS_BY_KEY``."""
    d = SETTINGS_BY_KEY.get(key)
    kind = d.kind if d else "text"
    if isinstance(v, str):
        v = v.strip()
    if kind == "int":
        return int(float(v))
    if kind == "float":
        return float(v)
    if kind == "bool":
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
    if kind == "list":
        if isinstance(v, (list, tuple)):
            return list(v)
        return [x.strip() for x in str(v).split(",") if x.strip()]
    return v


def _validate_empirical_signals(setting: str, signals: str, rule: str) -> None:
    sig_norm = (signals or "").strip().lower()
    if sig_norm in _FORBIDDEN_CONSTANT_SIGNALS:
        raise AutoHiddenConstantError(
            f"AUTO resolution for '{setting}' attempted to emit placeholder signal {signals!r}; "
            "all AUTO decisions must inspect and report empirical data signals."
        )
    if not (rule or "").strip():
        raise AutoHiddenConstantError(f"AUTO resolution for '{setting}' must carry a non-empty rule.")


@dataclass
class Resolution:
    """Single audited setting resolution (AUTO or explicit user override)."""

    setting: str
    mode: str  # AUTO | USER_OVERRIDE
    value: str
    signals: str
    rule: str
    target: str = ""
    candidate_space: str = ""
    stage: str = ""

    def canonical_dict(self) -> dict[str, str]:
        return {
            "candidate_space": str(self.candidate_space or ""),
            "mode": str(self.mode),
            "rule": str(self.rule),
            "setting": str(self.setting),
            "signals": str(self.signals),
            "stage": str(self.stage or ""),
            "target": str(self.target or ""),
            "value": str(self.value),
        }


def _normalize_record_dict(item: Resolution | Mapping[str, Any]) -> dict[str, str]:
    if isinstance(item, Resolution):
        return item.canonical_dict()
    return {
        "candidate_space": str(item.get("candidate_space", "") or ""),
        "mode": str(item.get("mode", "")),
        "rule": str(item.get("rule", "")),
        "setting": str(item.get("setting", "")),
        "signals": str(item.get("signals", "")),
        "stage": str(item.get("stage", "") or ""),
        "target": str(item.get("target", "") or ""),
        "value": str(item.get("value", "")),
    }


def canonicalize_auto_records(
    records: Sequence[Resolution | Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Return deterministically ordered canonical AUTO resolution dictionaries.

    Global records (``target == ""``) come first in emission order, followed by per-target
    groups sorted lexicographically by ``target`` while preserving exact within-target causal
    emission order. Wall-clock timestamps are excluded so hashes remain run-invariant.
    """
    by_target: dict[str, list[dict[str, str]]] = {}
    for raw in records:
        norm = _normalize_record_dict(raw)
        tgt = norm["target"]
        by_target.setdefault(tgt, []).append(norm)

    ordered: list[dict[str, str]] = []
    if "" in by_target:
        ordered.extend(by_target.pop(""))
    for tgt in sorted(by_target.keys()):
        ordered.extend(by_target[tgt])
    return ordered


def compute_auto_log_hash(records: Sequence[Resolution | Mapping[str, Any]]) -> str:
    """Compute deterministic lowercase SHA-256 over canonical AUTO-log records."""
    canonical = canonicalize_auto_records(records)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AutoLogInspector:
    """Read-only structured inspector and validator over AUTO resolution records (AUTO-6, AC-33)."""

    def __init__(self, records: Sequence[Resolution | Mapping[str, Any]]) -> None:
        self._records: list[dict[str, str]] = canonicalize_auto_records(records)
        self._hash: str = compute_auto_log_hash(self._records)

    @property
    def auto_log_hash(self) -> str:
        return self._hash

    @property
    def records(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._records]

    def filter(
        self,
        *,
        target: str | None = None,
        mode: str | None = None,
        stage: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        q = (query or "").strip().lower()
        for rec in self._records:
            if target is not None and target != "(all)" and rec["target"] != target:
                continue
            if mode is not None and rec["mode"] != mode:
                continue
            if stage is not None and rec["stage"] != stage:
                continue
            if q and not any(q in rec[field].lower() for field in ("setting", "value", "signals", "rule", "target")):
                continue
            out.append(dict(rec))
        return out

    def validate_no_hidden_constants(self) -> list[str]:
        violations: list[str] = []
        for idx, rec in enumerate(self._records):
            if rec["mode"] == "AUTO":
                sig = rec["signals"].strip().lower()
                if sig in _FORBIDDEN_CONSTANT_SIGNALS:
                    violations.append(f"entry[{idx}] setting={rec['setting']!r} target={rec['target']!r}: {rec['signals']!r}")
                if not rec["rule"].strip():
                    violations.append(f"entry[{idx}] setting={rec['setting']!r}: empty rule")
        return violations

    def summary(self) -> dict[str, Any]:
        targets = sorted({r["target"] for r in self._records if r["target"]})
        stages = sorted({r["stage"] for r in self._records if r["stage"]})
        by_target: dict[str, list[dict[str, str]]] = {}
        for r in self._records:
            by_target.setdefault(r["target"] or "(global)", []).append(dict(r))
        violations = self.validate_no_hidden_constants()
        auto_cnt = sum(1 for r in self._records if r["mode"] == "AUTO")
        ovr_cnt = sum(1 for r in self._records if r["mode"] == "USER_OVERRIDE")
        return {
            "auto_log_hash": self._hash,
            "total_entries": len(self._records),
            "auto_entries": auto_cnt,
            "override_entries": ovr_cnt,
            "targets": targets,
            "stages": stages,
            "all_auto_have_empirical_signals": len(violations) == 0,
            "signal_violations": violations,
            "by_target": by_target,
            "records": [dict(r) for r in self._records],
        }


def inspect_auto_log(records: Sequence[Resolution | Mapping[str, Any]]) -> dict[str, Any]:
    """Convenience wrapper returning full structured inspector dictionary + ``auto_log_hash``."""
    return AutoLogInspector(records).summary()


class AutoLog:
    """Resolves settings (AUTO or user override) and records every decision deterministically."""

    def __init__(self, config: dict | None = None, target: str = "", stage: str = ""):
        self.config = dict(config or {})
        self.target = target
        self.stage = stage
        self.records: list[Resolution] = []

    def for_target(self, target: str, *, isolated: bool = False) -> "AutoLog":
        child = AutoLog(self.config, target, stage=self.stage)
        if not isolated:
            child.records = self.records
        return child

    def merge_from(self, other: "AutoLog") -> None:
        self.records.extend(other.records)

    def decide(
        self,
        key: str,
        auto_fn: Callable[[], tuple[Any, str, str]],
        *,
        candidate_space: str = "",
        stage: str = "",
    ) -> Any:
        """Resolve ``key`` via ``auto_fn() -> (value, signals, rule)`` or user override."""
        eff_stage = stage or self.stage
        user = self.config.get(key, AUTO)
        if is_auto(user):
            value, signals, rule = auto_fn()
            _validate_empirical_signals(key, signals, rule)
            self.records.append(
                Resolution(
                    setting=key,
                    mode="AUTO",
                    value=_fmt(value),
                    signals=signals,
                    rule=rule,
                    target=self.target,
                    candidate_space=candidate_space,
                    stage=eff_stage,
                )
            )
            log.info(
                "auto.resolved target=%s setting=%s value=%s signals=[%s] rule=[%s]",
                self.target,
                key,
                _fmt(value),
                signals,
                rule,
            )
            return value
        value = coerce(key, user)
        self.records.append(
            Resolution(
                setting=key,
                mode="USER_OVERRIDE",
                value=_fmt(value),
                signals="value supplied by user",
                rule="user override of AUTO",
                target=self.target,
                candidate_space=candidate_space,
                stage=eff_stage,
            )
        )
        log.info("auto.override target=%s setting=%s value=%s", self.target, key, _fmt(value))
        return value

    def note(
        self,
        key: str,
        value: Any,
        signals: str,
        rule: str,
        *,
        candidate_space: str = "",
        stage: str = "",
    ) -> None:
        """Record a derived (non-configurable) decision made from empirical signals."""
        _validate_empirical_signals(key, signals, rule)
        self.records.append(
            Resolution(
                setting=key,
                mode="AUTO",
                value=_fmt(value),
                signals=signals,
                rule=rule,
                target=self.target,
                candidate_space=candidate_space,
                stage=stage or self.stage,
            )
        )
        log.info(
            "auto.derived target=%s setting=%s value=%s signals=[%s]",
            self.target,
            key,
            _fmt(value),
            signals,
        )

    def record_override(
        self,
        key: str,
        value: Any,
        *,
        reason: str = "user override of AUTO",
        signals: str = "value supplied by user",
        candidate_space: str = "",
        stage: str = "",
    ) -> None:
        """Record an explicit interactive user override in the AUTO audit log."""
        if not (reason or "").strip():
            raise ValueError(f"User override for '{key}' requires a non-empty audit reason.")
        self.records.append(
            Resolution(
                setting=key,
                mode="USER_OVERRIDE",
                value=_fmt(value),
                signals=signals,
                rule=reason.strip(),
                target=self.target,
                candidate_space=candidate_space,
                stage=stage or self.stage,
            )
        )
        log.info("auto.user_override target=%s setting=%s value=%s reason=%s", self.target, key, _fmt(value), reason)

    def compute_hash(self) -> str:
        return compute_auto_log_hash(self.records)

    def inspect(self) -> dict[str, Any]:
        return AutoLogInspector(self.records).summary()

    def as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.records]


def _fmt(v: Any) -> str:
    if v is None or (isinstance(v, str) and not v.strip()):
        return "(none)"
    if isinstance(v, dict):
        return ", ".join(f"{k}={_fmt(x)}" for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))) or "(none)"
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (list, tuple)):
        return ",".join(_fmt(x) for x in v) if v else "(none)"
    return str(v)
