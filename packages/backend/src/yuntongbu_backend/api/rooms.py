from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from yuntongbu_shared_protocol import (
    MediaDescriptor,
    PlaybackCommand,
    PlaybackCommandMessage,
    PlaybackCommandPayload,
    PlaybackState,
    RoomSnapshotMessage,
    RoomSnapshotPayload,
)

from ..models import EmbyBinding, Room, RoomMember, RoomQueueEntry, User, new_uuid
from ..schemas import ClientHandoffCreateResponse, JoinRoomRequest, PlaybackLoadRequest, PlaybackStateEnvelope, QueueImportRequest, RoomCreateRequest, RoomMemberResponse, RoomResponse, SeekRequest, ToggleWritebackRequest
from ..services.emby import EmbyError
from ..services.rooms import apply_room_command, generate_invite_code, replace_room_queue, room_members_to_response, room_to_response, room_to_state
from .deps import AppContext, ensure_room_owner, get_connections, get_context, get_current_user, get_session, get_room_for_user, load_room

router = APIRouter(prefix="/rooms", tags=["rooms"])


@router.get("", response_model=list[RoomResponse])
async def list_rooms(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[RoomResponse]:
    result = await session.execute(
        select(Room)
        .join(RoomMember, RoomMember.room_id == Room.id)
        .where(RoomMember.user_id == user.id)
        .options(selectinload(Room.queue_entries))
        .order_by(Room.updated_at.desc())
    )
    rooms = result.scalars().unique().all()
    return [room_to_response(room, current_user_id=user.id) for room in rooms]


@router.post("", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    payload: RoomCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RoomResponse:
    room = Room(
        name=payload.name,
        invite_code=generate_invite_code(),
        owner_user_id=user.id,
        writeback_enabled=payload.writeback_enabled,
        playback_state=PlaybackState.STOPPED.value,
        server_timestamp=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(room)
    await session.flush()
    session.add(RoomMember(room_id=room.id, user_id=user.id, role="owner"))
    await session.commit()
    room = await load_room(session, room.id)
    return room_to_response(room, current_user_id=user.id)


@router.post("/join", response_model=RoomResponse)
async def join_room(
    payload: JoinRoomRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RoomResponse:
    result = await session.execute(select(Room).where(Room.invite_code == payload.invite_code.upper()))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite code is invalid.")
    membership_result = await session.execute(select(RoomMember).where(RoomMember.room_id == room.id, RoomMember.user_id == user.id))
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        session.add(RoomMember(room_id=room.id, user_id=user.id, role="member"))
        await session.commit()
    room = await load_room(session, room.id)
    return room_to_response(room, current_user_id=user.id)


@router.get("/{room_id}", response_model=RoomResponse)
async def get_room(
    room_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RoomResponse:
    room = await get_room_for_user(room_id, user, session)
    return room_to_response(room, current_user_id=user.id)


@router.post("/{room_id}/client-handoff", response_model=ClientHandoffCreateResponse)
async def create_client_handoff(
    room_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> ClientHandoffCreateResponse:
    room = await get_room_for_user(room_id, user, session)
    backend_url = str(request.base_url).rstrip("/")
    handoff_token, expires_at = await context.handoffs.issue(
        context.settings,
        backend_url=backend_url,
        user_id=user.id,
        room_id=room.id,
    )
    return ClientHandoffCreateResponse(
        handoff_token=handoff_token,
        deeplink_url=f"yuntongbu://play?handoff={handoff_token}",
        expires_at=expires_at,
    )


@router.get("/{room_id}/members", response_model=list[RoomMemberResponse])
async def get_room_members(
    room_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    connections=Depends(get_connections),
) -> list[RoomMemberResponse]:
    room = await get_room_for_user(room_id, user, session)
    online = await connections.online_devices_by_user(room.id)
    return room_members_to_response(room, online)


@router.post("/{room_id}/writeback-toggle", response_model=PlaybackStateEnvelope)
async def toggle_writeback(
    room_id: str,
    payload: ToggleWritebackRequest,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    room.writeback_enabled = payload.enabled
    await session.commit()
    await _broadcast_snapshot(context, room)
    return PlaybackStateEnvelope(state=room_to_state(room))


@router.post("/{room_id}/playback/load", response_model=PlaybackStateEnvelope)
async def load_playback(
    room_id: str,
    payload: PlaybackLoadRequest,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    binding = await _load_owner_binding(session, room, payload.binding_id)
    try:
        resolved = await context.emby_service.resolve_playback(binding, payload.item_id)
    except EmbyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    replace_room_queue(room, [])
    state = apply_room_command(
        room,
        PlaybackCommand.LOAD,
        media=MediaDescriptor(
            binding_id=binding.id,
            item_id=resolved.item_id,
            title=resolved.title,
            media_url=resolved.media_url,
            media_source_id=resolved.media_source_id,
            play_session_id=resolved.play_session_id,
            emby_user_id=resolved.emby_user_id,
            duration_ms=resolved.duration_ms,
            artwork_url=resolved.artwork_url,
        ),
    )
    if room.writeback_enabled:
        await context.emby_service.report_started(binding, room, position_ms=0)
        now = datetime.now(UTC).replace(tzinfo=None)
        room.writeback_started_at = now
        room.last_writeback_at = now
    await session.commit()
    await _broadcast_command(context, room, PlaybackCommand.LOAD)
    return PlaybackStateEnvelope(state=state)


@router.post("/{room_id}/playback/play", response_model=PlaybackStateEnvelope)
async def play(
    room_id: str,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    state = apply_room_command(room, PlaybackCommand.PLAY)
    await _writeback_if_enabled(context, session, room, event_name="Unpause")
    await session.commit()
    await _broadcast_command(context, room, PlaybackCommand.PLAY)
    return PlaybackStateEnvelope(state=state)


@router.post("/{room_id}/playback/pause", response_model=PlaybackStateEnvelope)
async def pause(
    room_id: str,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    state = apply_room_command(room, PlaybackCommand.PAUSE)
    await _writeback_if_enabled(context, session, room, event_name="Pause", paused=True)
    await session.commit()
    await _broadcast_command(context, room, PlaybackCommand.PAUSE)
    return PlaybackStateEnvelope(state=state)


@router.post("/{room_id}/playback/seek", response_model=PlaybackStateEnvelope)
async def seek(
    room_id: str,
    payload: SeekRequest,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    state = apply_room_command(room, PlaybackCommand.SEEK, position_ms=payload.position_ms)
    await _writeback_if_enabled(context, session, room, event_name="TimeUpdate")
    await session.commit()
    await _broadcast_command(context, room, PlaybackCommand.SEEK, target_position_ms=payload.position_ms)
    return PlaybackStateEnvelope(state=state)


@router.post("/{room_id}/playback/stop", response_model=PlaybackStateEnvelope)
async def stop(
    room_id: str,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    state = apply_room_command(room, PlaybackCommand.STOP)
    await _stop_writeback_if_enabled(context, session, room)
    await session.commit()
    await _broadcast_command(context, room, PlaybackCommand.STOP)
    return PlaybackStateEnvelope(state=state)


@router.post("/{room_id}/queue/import", response_model=PlaybackStateEnvelope)
async def import_queue(
    room_id: str,
    payload: QueueImportRequest,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    binding = await _load_owner_binding(session, room, payload.binding_id)
    try:
        imported = await context.emby_service.import_queue(binding, payload.item_id)
        first_item_id = str(imported.items[0]["id"])
        resolved = await context.emby_service.resolve_playback(binding, first_item_id)
    except EmbyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _replace_room_queue(
        session,
        room,
        [
            RoomQueueEntry(
                id=new_uuid(),
                position=index,
                binding_id=binding.id,
                item_id=str(item["id"]),
                title=str(item["name"]),
                item_type=str(item["item_type"]) if item.get("item_type") else None,
                artwork_url=str(item["artwork_url"]) if item.get("artwork_url") else None,
                duration_ms=int(item["duration_ms"]) if item.get("duration_ms") is not None else None,
                source_item_id=imported.source_item_id,
                source_title=imported.source_title,
                source_kind=imported.source_kind,
            )
            for index, item in enumerate(imported.items)
        ],
    )

    state = apply_room_command(
        room,
        PlaybackCommand.LOAD,
        media=MediaDescriptor(
            binding_id=binding.id,
            item_id=resolved.item_id,
            title=resolved.title,
            media_url=resolved.media_url,
            media_source_id=resolved.media_source_id,
            play_session_id=resolved.play_session_id,
            emby_user_id=resolved.emby_user_id,
            duration_ms=resolved.duration_ms,
            artwork_url=resolved.artwork_url,
        ),
    )
    if room.writeback_enabled:
        await context.emby_service.report_started(binding, room, position_ms=0)
        now = datetime.now(UTC).replace(tzinfo=None)
        room.writeback_started_at = now
        room.last_writeback_at = now
    await session.commit()
    await _broadcast_command(context, room, PlaybackCommand.LOAD)
    return PlaybackStateEnvelope(state=state)


@router.post("/{room_id}/queue/{entry_id}/load", response_model=PlaybackStateEnvelope)
async def load_queue_entry(
    room_id: str,
    entry_id: str,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    entry = next((item for item in room.queue_entries if item.id == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue entry not found.")
    if not entry.binding_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Queue entry has no associated Emby binding.")

    binding = await _load_owner_binding(session, room, entry.binding_id)
    try:
        resolved = await context.emby_service.resolve_playback(binding, entry.item_id)
    except EmbyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    state = apply_room_command(
        room,
        PlaybackCommand.LOAD,
        media=MediaDescriptor(
            binding_id=binding.id,
            item_id=resolved.item_id,
            title=resolved.title,
            media_url=resolved.media_url,
            media_source_id=resolved.media_source_id,
            play_session_id=resolved.play_session_id,
            emby_user_id=resolved.emby_user_id,
            duration_ms=resolved.duration_ms,
            artwork_url=resolved.artwork_url,
        ),
    )
    if room.writeback_enabled:
        await context.emby_service.report_started(binding, room, position_ms=0)
        now = datetime.now(UTC).replace(tzinfo=None)
        room.writeback_started_at = now
        room.last_writeback_at = now
    await session.commit()
    await _broadcast_command(context, room, PlaybackCommand.LOAD)
    return PlaybackStateEnvelope(state=state)


@router.delete("/{room_id}/queue", response_model=PlaybackStateEnvelope)
async def clear_queue(
    room_id: str,
    room: Room = Depends(ensure_room_owner),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> PlaybackStateEnvelope:
    await _replace_room_queue(session, room, [])
    await session.commit()
    await _broadcast_snapshot(context, room)
    return PlaybackStateEnvelope(state=room_to_state(room))


async def _broadcast_command(
    context: AppContext,
    room: Room,
    command: PlaybackCommand,
    *,
    target_position_ms: int | None = None,
) -> None:
    state = room_to_state(room)
    await context.connections.broadcast(
        room.id,
        PlaybackCommandMessage(
            payload=PlaybackCommandPayload(
                command_id=f"{room.state_version}:{command.value}",
                command=command,
                state=state,
                target_position_ms=target_position_ms,
            )
        ),
    )
    await _broadcast_snapshot(context, room)


async def _broadcast_snapshot(context: AppContext, room: Room) -> None:
    online = await context.connections.online_devices_by_user(room.id)
    await context.connections.broadcast(
        room.id,
        RoomSnapshotMessage(
            payload=RoomSnapshotPayload(
                state=room_to_state(room),
                members=[member.model_dump(mode="json") for member in room_members_to_response(room, online)],
            )
        ),
    )


async def _writeback_if_enabled(
    context: AppContext,
    session: AsyncSession,
    room: Room,
    *,
    event_name: str,
    paused: bool = False,
) -> None:
    if not room.writeback_enabled or not room.current_binding_id:
        return
    binding = await session.get(EmbyBinding, room.current_binding_id)
    if binding is None:
        return
    if room.writeback_started_at is None:
        await context.emby_service.report_started(binding, room, position_ms=room.target_position_ms)
        room.writeback_started_at = datetime.now(UTC).replace(tzinfo=None)
    await context.emby_service.report_progress(
        binding,
        room,
        position_ms=room.target_position_ms,
        event_name=event_name,
        paused=paused,
    )
    room.last_writeback_at = datetime.now(UTC).replace(tzinfo=None)


async def _stop_writeback_if_enabled(context: AppContext, session: AsyncSession, room: Room) -> None:
    if not room.writeback_enabled or not room.current_binding_id:
        return
    binding = await session.get(EmbyBinding, room.current_binding_id)
    if binding is None:
        return
    await context.emby_service.report_stopped(binding, room, position_ms=room.target_position_ms)


async def _replace_room_queue(session: AsyncSession, room: Room, entries: list[RoomQueueEntry]) -> None:
    replace_room_queue(room, [])
    await session.flush()
    if entries:
        replace_room_queue(room, entries)


async def _load_owner_binding(session: AsyncSession, room: Room, binding_id: str) -> EmbyBinding:
    result = await session.execute(
        select(EmbyBinding).where(EmbyBinding.id == binding_id, EmbyBinding.user_id == room.owner_user_id)
    )
    binding = result.scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Emby binding not found.")
    return binding
