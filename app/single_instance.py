"""Une seule instance de l'application à la fois.

La première instance écoute sur un QLocalServer ; une seconde instance s'y
connecte pour demander l'affichage de la fenêtre déjà ouverte, puis quitte.
"""

from __future__ import annotations

import getpass

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

_CONNECT_TIMEOUT_MS = 500


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"MobiDeskPro-{user}"


def notify_running_instance() -> bool:
    """True si une autre instance tourne déjà (elle est alors invitée à se
    mettre au premier plan)."""
    socket = QLocalSocket()
    socket.connectToServer(_server_name())
    if not socket.waitForConnected(_CONNECT_TIMEOUT_MS):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


def _bring_to_front() -> None:
    for widget in QApplication.topLevelWidgets():
        if widget.isVisible():
            if widget.isMinimized():
                widget.showNormal()
            widget.raise_()
            widget.activateWindow()


def start_listening(parent: QObject) -> QLocalServer:
    """Démarre l'écoute des futures instances (à garder référencé)."""
    server = QLocalServer(parent)
    QLocalServer.removeServer(_server_name())  # reste d'un arrêt brutal
    server.listen(_server_name())

    def on_connection() -> None:
        client = server.nextPendingConnection()
        if client is not None:
            client.disconnected.connect(client.deleteLater)
            _bring_to_front()

    server.newConnection.connect(on_connection)
    return server
