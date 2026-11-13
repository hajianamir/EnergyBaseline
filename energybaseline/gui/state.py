"""Shared application state (single user, single process)."""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QObject, Signal

from ..core.schema import DatasetSchema, default_config


class AppState(QObject):
    data_changed = Signal()
    run_changed = Signal()
    selection_changed = Signal()
    config_changed = Signal()

    def __init__(self):
        super().__init__()
        self.df: pd.DataFrame | None = None
        self.dataset_name = ""
        self.dataset_path = ""
        self.dataset_sha = ""
        self.dataset_id: int | None = None
        self.dataset_schema: DatasetSchema | None = None
        self.schema_id: int | None = None
        self.vsg = None  # VariableSelectionGate | None
        self.vsg_id: int | None = None
        self.tcp = None  # TestConfigPanel | None
        self.tcp_id: int | None = None
        self.compliance_runner = None  # CachedComplianceRunner | None
        self.data_dictionary: pd.DataFrame | None = None
        self.time_col: str | None = None
        self.targets: list[str] = []
        self.units: dict[str, str] = {}
        self.config: dict = default_config()
        self.setup_config: dict = {}
        self.run = None  # RunResult
        self.run_id: int | None = None
        self.locked: dict[str, dict] = {}  # target -> selection dict

    def effective_config(self) -> dict:
        cfg = dict(self.config)
        cfg.update({k: v for k, v in self.setup_config.items() if k != "per_target"})
        if self.setup_config.get("per_target"):
            cfg["per_target"] = self.setup_config["per_target"]
        if self.dataset_schema is not None:
            cfg["dataset_schema"] = self.dataset_schema.to_dict()
        if self.vsg is not None:
            cfg["vsg_sha256"] = self.vsg.vsg_sha256
        if self.tcp is not None:
            cfg["tcp_sha256"] = self.tcp.tcp_sha256
            if self.tcp.preregistration_sha256:
                cfg["preregistration_sha256"] = self.tcp.preregistration_sha256
        return cfg
