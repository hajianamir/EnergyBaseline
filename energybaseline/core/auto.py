"""AUTO-resolution engine: every decision is logged with signals + rule."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .schema import AUTO, SETTINGS_BY_KEY

log = logging.getLogger("energybaseline.auto")


def is_auto(v: Any) -> bool:
    return v is None or (isinstance(v, str) and (v.strip() == "" or v.strip().upper() == AUTO))


def coerce(key: str, v: Any) -> Any:
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


@dataclass
class Resolution:
    setting: str
    mode: str  # AUTO | USER_OVERRIDE
    value: str
    signals: str
    rule: str
    target: str = ""


class AutoLog:
    """Resolves settings (AUTO or user override) and records every decision."""

    def __init__(self, config: dict | None = None, target: str = ""):
        self.config = dict(config or {})
        self.target = target
        self.records: list[Resolution] = []

    def for_target(self, target: str) -> "AutoLog":
        child = AutoLog(self.config, target)
        child.records = self.records  # shared list (thread-safe append)
        return child

    def decide(self, key: str, auto_fn: Callable[[], tuple[Any, str, str]]) -> Any:
        """``auto_fn`` returns (value, signals, rule)."""
        user = self.config.get(key, AUTO)
        if is_auto(user):
            value, signals, rule = auto_fn()
            self.records.append(Resolution(key, "AUTO", _fmt(value), signals, rule, self.target))
            log.info("auto.resolved target=%s setting=%s value=%s signals=[%s] rule=[%s]",
                     self.target, key, _fmt(value), signals, rule)
            return value
        value = coerce(key, user)
        self.records.append(Resolution(key, "USER_OVERRIDE", _fmt(value), "value supplied by user", "user override of AUTO", self.target))
        log.info("auto.override target=%s setting=%s value=%s", self.target, key, _fmt(value))
        return value

    def note(self, key: str, value: Any, signals: str, rule: str) -> None:
        """Record a derived (non-configurable) decision made by the engine."""
        self.records.append(Resolution(key, "AUTO", _fmt(value), signals, rule, self.target))
        log.info("auto.derived target=%s setting=%s value=%s signals=[%s]", self.target, key, _fmt(value), signals)

    def as_dicts(self) -> list[dict]:
        return [asdict(r) for r in self.records]


def _fmt(v: Any) -> str:
    if v is None or (isinstance(v, str) and not v.strip()):
        return "(none)"
    if isinstance(v, dict):
        return ", ".join(f"{k}={_fmt(x)}" for k, x in v.items()) or "(none)"
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (list, tuple)):
        return ",".join(_fmt(x) for x in v) if v else "(none)"
    return str(v)
