"""Compile-time UI command allowlist resolved at startup (P1.5, SCR-7).

Security & architecture guarantees:
* ``STATIC_UI_COMMAND_SPECS`` is an immutable ``MappingProxyType`` defined at import/compile time.
* Handlers are bound explicitly by dictionary key and validated once at ``MainWindow`` startup.
* Zero ``eval``, ``exec``, or ``getattr`` dynamic string dispatch anywhere in command routing.
* Any unknown command identifier raises ``UnknownCommandError`` immediately.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


class UnknownCommandError(KeyError):
    """Raised when attempting to register or dispatch a command ID outside the compile-time allowlist."""


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    category: str
    description: str


STATIC_UI_COMMAND_SPECS: MappingProxyType[str, CommandSpec] = MappingProxyType(
    {
        "nav.setup": CommandSpec("nav.setup", "navigation", "Activate 1 · Data & Temporal Setup subwindow"),
        "nav.analysis": CommandSpec("nav.analysis", "navigation", "Activate 2 · Analysis subwindow"),
        "nav.results": CommandSpec("nav.results", "navigation", "Activate 3 · Results subwindow"),
        "nav.selection": CommandSpec("nav.selection", "navigation", "Activate 4 · Baseline Selection subwindow"),
        "nav.monitor": CommandSpec("nav.monitor", "navigation", "Activate 5 · Prediction & Monitoring subwindow"),
        "nav.status": CommandSpec("nav.status", "navigation", "Activate Status subwindow"),
        "file.open_dataset": CommandSpec("file.open_dataset", "file", "Open dataset via guarded file picker"),
        "file.manual_entry": CommandSpec("file.manual_entry", "file", "Open manual dataset entry dialog"),
        "file.load_run": CommandSpec("file.load_run", "file", "Load archived analysis run"),
        "file.exit": CommandSpec("file.exit", "file", "Close main window and trigger clean shutdown"),
        "analysis.run": CommandSpec("analysis.run", "analysis", "Dispatch analysis run to worker process pool"),
        "analysis.cancel": CommandSpec("analysis.cancel", "analysis", "Cancel active worker process-pool job"),
        "advanced.settings": CommandSpec("advanced.settings", "advanced", "Open Advanced settings dialog"),
        "advanced.show_auto_panel": CommandSpec("advanced.show_auto_panel", "advanced", "Raise AUTO Resolutions dock"),
        "view.theme_light": CommandSpec("view.theme_light", "view", "Apply light UI theme"),
        "view.theme_dark": CommandSpec("view.theme_dark", "view", "Apply dark UI theme"),
        "view.show_log": CommandSpec("view.show_log", "view", "Raise live log dock"),
        "status.refresh": CommandSpec("status.refresh", "status", "Refresh live Status page telemetry"),
        "help.guide": CommandSpec("help.guide", "help", "Open offline user guide"),
        "help.about": CommandSpec("help.about", "help", "Display About dialog"),
    }
)


class CommandRegistry:
    """Immutable-after-startup dispatcher backed strictly by ``STATIC_UI_COMMAND_SPECS``."""

    def __init__(self, bindings: dict[str, Callable[..., Any]]) -> None:
        unknown = set(bindings.keys()) - set(STATIC_UI_COMMAND_SPECS.keys())
        if unknown:
            raise UnknownCommandError(f"Cannot bind unallowlisted command(s): {sorted(unknown)}")
        missing = set(STATIC_UI_COMMAND_SPECS.keys()) - set(bindings.keys())
        if missing:
            raise ValueError(f"Missing required startup command binding(s): {sorted(missing)}")
        for cid, fn in bindings.items():
            if not callable(fn):
                raise TypeError(f"Command binding for {cid!r} must be callable, got {type(fn).__name__}")
        self._handlers: MappingProxyType[str, Callable[..., Any]] = MappingProxyType(dict(bindings))

    @property
    def command_ids(self) -> tuple[str, ...]:
        return tuple(STATIC_UI_COMMAND_SPECS.keys())

    def is_allowed(self, command_id: str) -> bool:
        return isinstance(command_id, str) and command_id in self._handlers

    def dispatch(self, command_id: str, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(command_id, str) or command_id not in self._handlers:
            raise UnknownCommandError(f"Rejected unknown command: {command_id!r}")
        handler = self._handlers[command_id]
        return handler(*args, **kwargs)
