"""Native exact-plan registration and execution using the trusted spawn worker boundary."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.pool import get_pool_manager, pack_payload
from ..core.vsg import ComplianceGateError
from ..statistics import assert_plan_authorized, validate_plan
from .workbench import WorkbenchPanel


class StatisticalPlanPanel(WorkbenchPanel):
    def __init__(self, state):
        super().__init__(state)
        self.notice.setText(
            "Inference requires accepted VSG for these exact inputs and this exact plan registered before TCP lock. "
            "Declare metadata as GROUP roles in the schema, accept VSG, load the plan, register it here, "
            "then explicitly lock TCP in Setup. No automatic acceptance, amendment, refit or baseline promotion."
        )
        self.load_button.setText("Load Statistical Plan JSON…")
        self.reference_button.setText("Register Exact Plan Before Lock")
        self.reference_button.clicked.disconnect()
        self.reference_button.clicked.connect(self.register_plan)
        self.run_button.setText("Run Preregistered Plan")
        self.editor.setAccessibleName("Versioned preregistered statistical plan JSON")
        self.editor.setPlaceholderText("Load a version-1 plan from examples/statistics.")
        self.status.setText("No statistical evidence. VSG acceptance and exact-plan TCP lock are required.")
        self.state.config_changed.connect(self._invalidate)
        self.state.selection_changed.connect(self._invalidate)

    def register_plan(self):
        self._invalidate()
        try:
            plan = json.loads(self.editor.toPlainText())
            validate_plan(plan)
            if self.state.vsg is None or self.state.tcp is None or self.state.tcp.vsg is not self.state.vsg:
                raise ValueError("No matching gates.")
            self.state.vsg.assert_accepted()
            self.state.tcp.register_extension_plan(plan)
            self.state.config_changed.emit()
            self.status.setText("Exact plan registered. Now explicitly lock TCP in Setup before execution.")
            return True
        except (ValueError, TypeError, AttributeError, RuntimeError):
            self.status.setText(
                "Registration blocked. Accept matching VSG first; locked TCP requires an audited amendment."
            )
            return False

    def run_job(self):
        self.result = None
        self.output.clear()
        if self.handle is not None:
            return
        try:
            if len(self.editor.toPlainText()) > 200_000:
                raise ValueError("Plan too large.")
            plan = json.loads(self.editor.toPlainText())
            assert_plan_authorized(plan, self.state.df, self.state.vsg, self.state.tcp)
            manager = get_pool_manager()
            envelope = pack_payload(
                (self.state.df, self.state.vsg, self.state.tcp), manager.cache_dir(), prefix="statistics_inputs"
            )
            cleanup = [Path(envelope["path"])] if envelope.get("__eb_ref__") else []
            self.handle = manager.submit_command(
                "worker.statistical_plan", {"request": plan, "inputs_envelope": envelope}, cleanup_paths=cleanup
            )
            self._active_generation = self._generation
            self.run_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.status.setText("Running the locked plan in an isolated worker; no partial results are released.")
            self.timer.start()
        except (ValueError, TypeError, AttributeError, OSError, RuntimeError):
            self.status.setText(
                "Execution blocked. Accept matching VSG and explicitly register/lock this exact plan first."
            )

    def poll(self):
        if self.handle is not None and self.handle.done_event.is_set() and self.handle.result_value is not None:
            try:
                assert_plan_authorized(
                    json.loads(self.editor.toPlainText()), self.state.df, self.state.vsg, self.state.tcp
                )
                if self.handle.result_value["preregistration_sha256"] != self.state.tcp.preregistration_sha256:
                    raise ComplianceGateError("Stale lock.")
            except (ValueError, TypeError, AttributeError, KeyError, RuntimeError):
                self._generation += 1
        super().poll()
