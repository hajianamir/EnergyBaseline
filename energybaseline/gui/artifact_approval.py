"""Explicit independent-digest approval for previous-session executable artifacts."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QMessageBox

from ..config.runtime_env import is_interactive_ui
from ..lock import ArtifactIntegrityError


def load_with_artifact_approval(parent, loader):
    """Never derive executable-code approval from the untrusted adjacent sidecar."""
    try:
        return loader()
    except ArtifactIntegrityError:
        if not is_interactive_ui():
            raise
        digest, ok = QInputDialog.getText(
            parent,
            "Approve executable artifact",
            "This file can execute Python code. Only continue if you trust its producer.\n"
            "Enter its SHA-256 from an independently trusted record or channel.\n"
            "Do not copy a checksum from an untrusted file's adjacent sidecar:",
        )
        if not ok:
            raise ArtifactIntegrityError("Artifact approval cancelled; nothing was loaded.") from None
        digest = digest.strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ArtifactIntegrityError("A complete independently approved SHA-256 is required.") from None
        answer = QMessageBox.warning(
            parent,
            "Executable-code trust",
            "Approving this digest authorizes executable Python content, not just data.\n"
            "Have you verified the digest using an independent trusted source?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            raise ArtifactIntegrityError("Executable-code trust was not approved; nothing was loaded.")
        return loader(trusted_artifact_sha256=digest)
