from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from fastapi import WebSocket

from yuntongbu_shared_protocol import ServerToClientMessage


class ConnectionKind(StrEnum):
    PLAYER = "player"
    BROWSER = "browser"


@dataclass(slots=True)
class ConnectedClient:
    websocket: WebSocket
    user_id: str
    username: str
    kind: ConnectionKind = ConnectionKind.PLAYER
    authorized_room_id: str | None = None
    authorized_device_id: str | None = None
    authorized_device_name: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    room_id: str | None = None
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_state: dict | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, ConnectedClient] = {}
        self._room_index: dict[str, set[int]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def add(
        self,
        websocket: WebSocket,
        *,
        user_id: str,
        username: str,
        kind: ConnectionKind = ConnectionKind.PLAYER,
        authorized_room_id: str | None = None,
        authorized_device_id: str | None = None,
        authorized_device_name: str | None = None,
    ) -> ConnectedClient:
        connection = ConnectedClient(
            websocket=websocket,
            user_id=user_id,
            username=username,
            kind=kind,
            authorized_room_id=authorized_room_id,
            authorized_device_id=authorized_device_id,
            authorized_device_name=authorized_device_name,
        )
        async with self._lock:
            self._connections[id(websocket)] = connection
        return connection

    async def register_client(
        self,
        connection: ConnectedClient,
        *,
        room_id: str | None,
        device_id: str | None = None,
        device_name: str | None = None,
    ) -> None:
        async with self._lock:
            if connection.room_id:
                self._room_index[connection.room_id].discard(id(connection.websocket))
            connection.room_id = room_id
            connection.device_id = device_id
            connection.device_name = device_name
            connection.last_seen_at = datetime.now(UTC)
            if room_id:
                self._room_index[room_id].add(id(connection.websocket))

    async def update_seen(self, connection: ConnectedClient, state: dict | None = None) -> None:
        async with self._lock:
            connection.last_seen_at = datetime.now(UTC)
            if state is not None:
                connection.last_state = state

    async def disconnect(self, connection: ConnectedClient) -> None:
        async with self._lock:
            ws_id = id(connection.websocket)
            self._connections.pop(ws_id, None)
            if connection.room_id:
                self._room_index[connection.room_id].discard(ws_id)
                if not self._room_index[connection.room_id]:
                    self._room_index.pop(connection.room_id, None)

    async def disconnect_room(self, room_id: str) -> None:
        for connection in await self.room_clients(room_id):
            try:
                await connection.websocket.close()
            except Exception:
                pass
            await self.disconnect(connection)

    async def room_clients(self, room_id: str) -> list[ConnectedClient]:
        async with self._lock:
            ids = list(self._room_index.get(room_id, set()))
            return [self._connections[ws_id] for ws_id in ids if ws_id in self._connections]

    async def online_devices_by_user(self, room_id: str) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for client in await self.room_clients(room_id):
            if client.kind != ConnectionKind.PLAYER:
                continue
            counts[client.user_id] += 1
        return dict(counts)

    async def broadcast(self, room_id: str, message: ServerToClientMessage) -> None:
        payload = message.model_dump(mode="json")
        stale: list[ConnectedClient] = []
        for connection in await self.room_clients(room_id):
            try:
                await connection.websocket.send_json(payload)
            except Exception:
                stale.append(connection)
        for connection in stale:
            await self.disconnect(connection)
