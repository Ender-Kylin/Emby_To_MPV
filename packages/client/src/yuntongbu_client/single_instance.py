from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceBridge(QObject):
    message_received = Signal(str)

    def __init__(self, server_name: str) -> None:
        super().__init__()
        self._server_name = server_name
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)

    def start_or_forward(self, message: str | None) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self._server_name)
        if socket.waitForConnected(150):
            if message:
                socket.write(message.encode("utf-8"))
                socket.flush()
                socket.waitForBytesWritten(500)
            socket.disconnectFromServer()
            return False

        QLocalServer.removeServer(self._server_name)
        if not self._server.listen(self._server_name):
            raise RuntimeError(f"Unable to listen on single-instance bridge '{self._server_name}'.")
        return True

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda sock=socket: self._read_message(sock))
            socket.disconnected.connect(socket.deleteLater)

    def _read_message(self, socket: QLocalSocket) -> None:
        payload = bytes(socket.readAll()).decode("utf-8").strip()
        if payload:
            self.message_received.emit(payload)
        socket.disconnectFromServer()
