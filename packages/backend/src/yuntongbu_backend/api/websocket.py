from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from yuntongbu_shared_protocol import ClientHello, Heartbeat, RoomSnapshotMessage, RoomSnapshotPayload, ServerNoticeMessage, ServerNoticePayload, StateUpdate, SyncCorrectionMessage, SyncCorrectionPayload, build_client_message_adapter

from ..models import EmbyBinding, Room, RoomMember, User
from ..security import decode_access_token, decode_device_session_token
from ..services import ConnectionKind
from ..services.rooms import expected_position_ms, room_members_to_response, room_to_state

router = APIRouter(tags=["websocket"])

client_message_adapter = build_client_message_adapter()


@dataclass(slots=True)
class AuthenticatedSocketUser:
    user: User
    token_kind: str
    authorized_room_id: str | None = None
    authorized_device_id: str | None = None
    authorized_device_name: str | None = None


@router.websocket("/ws/client")
async def client_socket(websocket: WebSocket, token: str = Query(...)) -> None:
    context = websocket.app.state.context
    auth = await _authenticate_player_socket(context, websocket, token)
    if auth is None:
        return

    await websocket.accept()
    connection = await context.connections.add(
        websocket,
        user_id=auth.user.id,
        username=auth.user.username,
        kind=ConnectionKind.PLAYER,
        authorized_room_id=auth.authorized_room_id,
        authorized_device_id=auth.authorized_device_id,
        authorized_device_name=auth.authorized_device_name,
    )
    await websocket.send_json(ServerNoticeMessage(payload=ServerNoticePayload(message="Connected to backend.")).model_dump(mode="json"))

    try:
        while True:
            raw = await websocket.receive_json()
            message = client_message_adapter.validate_python(raw)
            if isinstance(message, ClientHello):
                await _handle_client_hello(context, auth.user.id, connection, message)
            elif isinstance(message, Heartbeat):
                await context.connections.update_seen(connection)
            elif isinstance(message, StateUpdate):
                await _handle_state_update(context, auth.user.id, connection, message)
            else:
                await context.connections.update_seen(connection)
    except WebSocketDisconnect:
        await context.connections.disconnect(connection)
    except Exception:
        await context.connections.disconnect(connection)
        await websocket.close()


@router.websocket("/ws/rooms/{room_id}")
async def browser_room_socket(
    websocket: WebSocket,
    room_id: str,
    token: str = Query(...),
) -> None:
    context = websocket.app.state.context
    auth = await _authenticate_browser_socket(context, websocket, token)
    if auth is None:
        return

    async with context.database.session_maker() as session:
        try:
            room = await _authorized_room(session, room_id, auth.user.id)
        except HTTPException:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await websocket.accept()
    connection = await context.connections.add(
        websocket,
        user_id=auth.user.id,
        username=auth.user.username,
        kind=ConnectionKind.BROWSER,
    )
    await context.connections.register_client(connection, room_id=room.id)
    await websocket.send_json(
        ServerNoticeMessage(
            payload=ServerNoticePayload(message="Connected to room observer."),
        ).model_dump(mode="json")
    )
    await _send_snapshot(context, websocket, room)

    try:
        while True:
            await websocket.receive_text()
            await context.connections.update_seen(connection)
    except WebSocketDisconnect:
        await context.connections.disconnect(connection)
    except Exception:
        await context.connections.disconnect(connection)
        await websocket.close()


async def _handle_client_hello(context, user_id: str, connection, message: ClientHello) -> None:
    room_id = connection.authorized_room_id or message.payload.room_id
    device_id = connection.authorized_device_id or message.payload.device_id
    device_name = connection.authorized_device_name or message.payload.device_name
    if room_id:
        async with context.database.session_maker() as session:
            room = await _authorized_room(session, room_id, user_id)
            await context.connections.register_client(
                connection,
                room_id=room.id,
                device_id=device_id,
                device_name=device_name,
            )
            await _send_snapshot(context, connection.websocket, room)
            return
    await context.connections.register_client(
        connection,
        room_id=None,
        device_id=device_id,
        device_name=device_name,
    )


async def _handle_state_update(context, user_id: str, connection, message: StateUpdate) -> None:
    await context.connections.update_seen(connection, state=message.payload.state.model_dump(mode="json"))
    room_id = connection.authorized_room_id or message.payload.state.room_id
    if not room_id:
        return

    async with context.database.session_maker() as session:
        room = await _authorized_room(session, room_id, user_id)
        expected = expected_position_ms(room)
        drift_ms = message.payload.state.position_ms - expected
        if abs(drift_ms) >= context.settings.sync_small_drift_ms:
            await connection.websocket.send_json(
                SyncCorrectionMessage(
                    payload=SyncCorrectionPayload(
                        command_id=f"{room.state_version}:sync",
                        state=room_to_state(room),
                        expected_position_ms=expected,
                        drift_ms=drift_ms,
                    )
                ).model_dump(mode="json")
            )
        await _writeback_progress_if_due(context, session, room, message)


async def _writeback_progress_if_due(context, session: AsyncSession, room: Room, message: StateUpdate) -> None:
    if not room.writeback_enabled or not room.current_binding_id or room.current_item_id is None:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    if room.last_writeback_at and now - room.last_writeback_at < timedelta(seconds=context.settings.writeback_interval_seconds):
        return
    binding = await session.get(EmbyBinding, room.current_binding_id)
    if binding is None:
        return
    if room.writeback_started_at is None:
        await context.emby_service.report_started(binding, room, position_ms=message.payload.state.position_ms)
        room.writeback_started_at = now
    await context.emby_service.report_progress(
        binding,
        room,
        position_ms=message.payload.state.position_ms,
        event_name="TimeUpdate",
        paused=message.payload.state.paused,
    )
    room.last_writeback_at = now
    await session.commit()


async def _send_snapshot(context, websocket: WebSocket, room: Room) -> None:
    await websocket.send_json(
        RoomSnapshotMessage(
            payload=RoomSnapshotPayload(
                state=room_to_state(room),
                members=[
                    member.model_dump(mode="json")
                    for member in room_members_to_response(room, await context.connections.online_devices_by_user(room.id))
                ],
            )
        ).model_dump(mode="json")
    )


async def _authenticate_player_socket(context, websocket: WebSocket, token: str) -> AuthenticatedSocketUser | None:
    payload = None
    token_kind = ""
    try:
        payload = decode_device_session_token(context.settings, token)
        token_kind = "device_session"
    except Exception:
        try:
            payload = decode_access_token(context.settings, token)
            token_kind = "access"
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None

    async with context.database.session_maker() as session:
        user = await session.get(User, payload["sub"])
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None
        return AuthenticatedSocketUser(
            user=user,
            token_kind=token_kind,
            authorized_room_id=str(payload["room_id"]) if token_kind == "device_session" else None,
            authorized_device_id=str(payload["device_id"]) if token_kind == "device_session" else None,
            authorized_device_name=str(payload["device_name"]) if token_kind == "device_session" else None,
        )


async def _authenticate_browser_socket(context, websocket: WebSocket, token: str) -> AuthenticatedSocketUser | None:
    try:
        payload = decode_access_token(context.settings, token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None

    async with context.database.session_maker() as session:
        user = await session.get(User, payload["sub"])
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None
        return AuthenticatedSocketUser(user=user, token_kind="access")


async def _authorized_room(session: AsyncSession, room_id: str, user_id: str) -> Room:
    result = await session.execute(
        select(Room)
        .where(Room.id == room_id)
        .options(
            selectinload(Room.members).selectinload(RoomMember.user),
            selectinload(Room.queue_entries),
        )
    )
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    membership_result = await session.execute(select(RoomMember).where(RoomMember.room_id == room.id, RoomMember.user_id == user_id))
    membership = membership_result.scalar_one_or_none()
    if membership is None and room.owner_user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a room member.")
    return room
