"""Single-instance application guard using ``QLocalServer`` / ``QLocalSocket`` (P1.3, SHL-1).

On launch:
* If a primary instance is already listening on the per-user pipe/socket, sends ``ACTIVATE`` so the primary
  window raises/activates itself, then signals the second instance to exit cleanly with code ``0``.
* If no live primary responds (including stale crash sockets on POSIX), claims the server name and listens.
"""

from __future__ import annotations

import getpass
import hashlib
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..config import get_base_dir

log = logging.getLogger("energybaseline.single_instance")


def default_instance_server_name() -> str:
    """Deterministic per-user + APP_HOME local server name safe on Windows named pipes and Unix domain sockets."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    raw = f"{user}::{get_base_dir().resolve()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"EnergyBaseline_SingleInstance_{digest}"


class SingleInstanceGuard(QObject):
    """Owns the primary ``QLocalServer`` or notifies an existing primary instance to activate its window."""

    activation_requested = Signal()

    def __init__(self, server_name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server_name = server_name or default_instance_server_name()
        self._server: QLocalServer | None = None
        self.is_primary = False

    def try_acquire(self, *, connect_timeout_ms: int = 300) -> bool:
        """Return ``True`` if this process became primary, or ``False`` if an active primary was notified."""
        probe = QLocalSocket()
        probe.connectToServer(self.server_name)
        if probe.waitForConnected(connect_timeout_ms):
            probe.write(b"ACTIVATE\n")
            probe.flush()
            probe.waitForBytesWritten(connect_timeout_ms)
            probe.waitForReadyRead(connect_timeout_ms)
            probe.disconnectFromServer()
            log.info("single_instance.activated_existing server=%s", self.server_name)
            self.is_primary = False
            return False
        probe.abort()

        # No live primary responded; clear any stale endpoint and listen.
        QLocalServer.removeServer(self.server_name)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self.server_name):
            # Race fallback: another instance bound in the narrow window; notify it.
            retry = QLocalSocket()
            retry.connectToServer(self.server_name)
            if retry.waitForConnected(connect_timeout_ms):
                retry.write(b"ACTIVATE\n")
                retry.flush()
                retry.waitForBytesWritten(connect_timeout_ms)
                retry.disconnectFromServer()
                self.is_primary = False
                return False
            log.warning(
                "single_instance.listen_failed server=%s error=%s",
                self.server_name,
                server.errorString(),
            )
            self.is_primary = True
            return True

        self._server = server
        self.is_primary = True
        log.info("single_instance.primary_listening server=%s", self.server_name)
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                continue
            conn.waitForReadyRead(200)
            payload = bytes(conn.readAll().data()).decode("utf-8", errors="ignore").strip()
            conn.write(b"ACK\n")
            conn.flush()
            conn.waitForBytesWritten(100)
            conn.disconnectFromServer()
            conn.deleteLater()
            if payload.startswith("ACTIVATE") or payload == "":
                self.activation_requested.emit()

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            QLocalServer.removeServer(self.server_name)
            self._server = None
        self.is_primary = False
