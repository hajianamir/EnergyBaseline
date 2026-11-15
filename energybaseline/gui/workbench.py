"""Native JSON workbench editor; computation stays in the existing spawn process pool."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from ..core import ingest
from ..core.pool import get_pool_manager, pack_payload
from ..io.paths import resolve_picked_path


class WorkbenchPanel(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.reference = None
        self.result = None
        self.handle = None
        self._generation = 0
        self._active_generation = None
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Exploratory tools only. No hypothesis tests, baseline acceptance, refitting or model persistence. "
            "Load a version-1 job from examples/workbench; inputs use the current raw dataset."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QHBoxLayout()
        self.load_button = QPushButton("Load Job JSON…")
        self.load_button.clicked.connect(lambda: self.load_request())
        buttons.addWidget(self.load_button)
        self.reference_button = QPushButton("Load Reference Dataset…")
        self.reference_button.clicked.connect(lambda: self.load_reference())
        buttons.addWidget(self.reference_button)
        self.run_button = QPushButton("Run Exploratory Job")
        self.run_button.clicked.connect(self.run_job)
        buttons.addWidget(self.run_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setAccessibleName("Versioned exploratory workbench job JSON")
        self.editor.setPlaceholderText('{"version": 1, "operation": "...", "parameters": {...}}')
        self.editor.textChanged.connect(self._invalidate)
        layout.addWidget(self.editor)
        self.status = QLabel("No workbench evidence. Baseline state is unchanged.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Input-bound workbench result JSON")
        layout.addWidget(self.output)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        state.data_changed.connect(self.reset)

    def _invalidate(self):
        self._generation += 1
        self.result = None
        self.output.clear()
        if self.handle is not None:
            self.handle.cancel()
            self.status.setText("Inputs changed. Cancelling stale work; run the revised job after cancellation.")

    def reset(self):
        self._invalidate()
        self.reference = None
        self.editor.clear()
        self.status.setText("Dataset changed. Job, references and displayed evidence cleared.")

    def load_request(self, picked_path=None, *, allowed_roots=None):
        self._invalidate()
        if picked_path is None:
            picked_path, _ = QFileDialog.getOpenFileName(self, "Load Workbench Job", "", "JSON (*.json)")
        if not picked_path:
            return False
        try:
            path = resolve_picked_path(picked_path, allowed_roots=allowed_roots)
            if path.stat().st_size > 1_000_000:
                raise ValueError("Request too large.")
            request = json.loads(path.read_text(encoding="utf-8"))
            self.editor.setPlainText(json.dumps(request, indent=2, allow_nan=False))
            self.status.setText("Job loaded. Explicitly run to compute new evidence.")
            return True
        except (OSError, ValueError, UnicodeError):
            self.editor.clear()
            self.status.setText("Job could not be loaded. Choose a valid, permitted JSON file.")
            return False

    def load_reference(self, picked_path=None, *, allowed_roots=None):
        self._invalidate()
        self.reference = None
        if picked_path is None:
            picked_path, _ = QFileDialog.getOpenFileName(
                self, "Load Workbench Reference", "", "Data (*.csv *.tsv *.xlsx *.parquet)"
            )
        if not picked_path:
            return False
        try:
            path = resolve_picked_path(picked_path, allowed_roots=allowed_roots)
            self.reference = ingest.load_file(path)
            self.status.setText("Reference loaded in memory only. Explicitly run the job.")
            return True
        except (OSError, ValueError, ingest.IngestError):
            self.status.setText("Reference could not be loaded; prior evidence and reference cleared.")
            return False

    def run_job(self):
        self.result = None
        self.output.clear()
        if self.handle is not None:
            return
        try:
            if len(self.editor.toPlainText()) > 1_000_000:
                raise ValueError("Request too large.")
            request = json.loads(self.editor.toPlainText())
            frame = None if request.get("operation") == "linear-optimize" else self.state.df
            manager = get_pool_manager()
            cache = manager.cache_dir()
            envelope = pack_payload((frame, self.reference), cache, prefix="workbench_inputs")
            cleanup = [Path(envelope["path"])] if envelope.get("__eb_ref__") else []
            self.handle = manager.submit_command(
                "worker.workbench", {"request": request, "inputs_envelope": envelope}, cleanup_paths=cleanup
            )
            self._active_generation = self._generation
            self.run_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.status.setText("Running in an isolated worker process. Baseline state is unchanged.")
            self.timer.start()
        except (ValueError, TypeError, AttributeError, OSError, RuntimeError):
            self.status.setText("Job submission blocked. Check the version-1 JSON and required input dataset.")

    def cancel(self):
        if self.handle is not None:
            self.handle.cancel()
            self.status.setText("Cancelling exploratory job…")

    def poll(self):
        if self.handle is None or not self.handle.done_event.is_set():
            return
        self.timer.stop()
        handle, self.handle = self.handle, None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if handle.status == "completed" and handle.result_value is not None:
            # Cancellation on any edit/reload invalidates the request, even if compute just finished.
            from ..workbench.common import digest

            try:
                valid = self._active_generation == self._generation
                valid = valid and digest(json.loads(self.editor.toPlainText())) == handle.result_value["spec_sha256"]
                current_hash = ingest.sha256_dataframe(self.state.df) if self.state.df is not None else None
                valid = valid and (
                    handle.result_value["input_sha256"] is None or handle.result_value["input_sha256"] == current_hash
                )
                reference_hash = ingest.sha256_dataframe(self.reference) if self.reference is not None else None
                valid = valid and handle.result_value["reference_sha256"] == reference_hash
            except (ValueError, TypeError, KeyError):
                valid = False
            if valid:
                self.result = handle.result_value
                self.output.setPlainText(json.dumps(self.result, indent=2, allow_nan=False))
                self.status.setText(
                    "Completed. Evidence is bound to the job and input hashes, not promoted to a baseline."
                )
                return
        self.result = None
        self.output.clear()
        self.status.setText("Job failed, was cancelled or became stale. No evidence retained; baseline unchanged.")
