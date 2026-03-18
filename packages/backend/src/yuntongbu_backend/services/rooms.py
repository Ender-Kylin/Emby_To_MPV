from __future__ import annotations

import secrets
from datetime import UTC, datetime

from yuntongbu_shared_protocol import MediaDescriptor, PlaybackCommand, PlaybackSessionState, PlaybackState, QueueEntryDescriptor

from ..models import Room, RoomQueueEntry
from ..schemas import RoomMemberResponse, RoomResponse


def generate_invite_code() -> str:
    return secrets.token_hex(3).upper()


def expected_position_ms(room: Room, now: datetime | None = None) -> int:
    if room.playback_state != PlaybackState.PLAYING.value:
        return max(room.target_position_ms, 0)
    now = now or datetime.now(UTC).replace(tzinfo=None)
    elapsed_ms = int((now - room.server_timestamp).total_seconds() * 1000)
    return max(room.target_position_ms + elapsed_ms, 0)


def room_to_state(room: Room) -> PlaybackSessionState:
    current_media = None
    if room.current_item_id and room.current_media_url:
        current_media = MediaDescriptor(
            binding_id=room.current_binding_id,
            item_id=room.current_item_id,
            title=room.current_item_name,
            media_url=room.current_media_url,
            media_source_id=room.current_media_source_id,
            play_session_id=room.current_play_session_id,
            emby_user_id=room.current_emby_user_id,
            duration_ms=room.duration_ms,
            artwork_url=room.artwork_url,
        )
    queue_entries = [
        QueueEntryDescriptor(
            id=entry.id,
            binding_id=entry.binding_id,
            item_id=entry.item_id,
            title=entry.title,
            item_type=entry.item_type,
            duration_ms=entry.duration_ms,
            artwork_url=entry.artwork_url,
            source_item_id=entry.source_item_id,
            source_title=entry.source_title,
            source_kind=entry.source_kind,
        )
        for entry in room.queue_entries
    ]
    current_queue_index = None
    if room.current_item_id:
        for index, entry in enumerate(room.queue_entries):
            if entry.item_id == room.current_item_id and entry.binding_id == room.current_binding_id:
                current_queue_index = index
                break
    return PlaybackSessionState(
        room_id=room.id,
        version=room.state_version,
        playback_state=PlaybackState(room.playback_state),
        position_ms=expected_position_ms(room),
        server_time=room.server_timestamp,
        current_media=current_media,
        queue_entries=queue_entries,
        current_queue_index=current_queue_index,
        writeback_enabled=room.writeback_enabled,
    )


def apply_room_command(
    room: Room,
    command: PlaybackCommand,
    *,
    media: MediaDescriptor | None = None,
    position_ms: int | None = None,
) -> PlaybackSessionState:
    now = datetime.now(UTC).replace(tzinfo=None)
    current_position = expected_position_ms(room, now=now)

    if command == PlaybackCommand.LOAD:
        if media is None:
            raise ValueError("load command requires media")
        room.current_binding_id = media.binding_id
        room.current_item_id = media.item_id
        room.current_item_name = media.title
        room.current_media_source_id = media.media_source_id
        room.current_media_url = media.media_url
        room.current_play_session_id = media.play_session_id
        room.current_emby_user_id = media.emby_user_id
        room.artwork_url = media.artwork_url
        room.duration_ms = media.duration_ms
        room.playback_state = PlaybackState.PLAYING.value
        room.target_position_ms = max(position_ms or 0, 0)
        room.writeback_started_at = None
        room.last_writeback_at = None
    elif command == PlaybackCommand.PLAY:
        room.playback_state = PlaybackState.PLAYING.value
        room.target_position_ms = current_position
    elif command == PlaybackCommand.PAUSE:
        room.playback_state = PlaybackState.PAUSED.value
        room.target_position_ms = current_position
    elif command in {PlaybackCommand.SEEK, PlaybackCommand.SYNC}:
        room.target_position_ms = max(position_ms or 0, 0)
        if room.playback_state == PlaybackState.STOPPED.value:
            room.playback_state = PlaybackState.PAUSED.value
    elif command == PlaybackCommand.STOP:
        room.playback_state = PlaybackState.STOPPED.value
        room.target_position_ms = 0
    else:
        raise ValueError(f"Unsupported command: {command}")

    room.server_timestamp = now
    room.state_version += 1
    return room_to_state(room)


def room_to_response(room: Room, *, current_user_id: str) -> RoomResponse:
    return RoomResponse(
        id=room.id,
        name=room.name,
        invite_code=room.invite_code,
        owner_user_id=room.owner_user_id,
        is_owner=room.owner_user_id == current_user_id,
        writeback_enabled=room.writeback_enabled,
        playback=room_to_state(room),
        created_at=room.created_at,
        updated_at=room.updated_at,
    )


def room_members_to_response(room: Room, online_devices_by_user: dict[str, int]) -> list[RoomMemberResponse]:
    responses: list[RoomMemberResponse] = []
    for member in room.members:
        responses.append(
            RoomMemberResponse(
                user_id=member.user.id,
                username=member.user.username,
                is_owner=member.user_id == room.owner_user_id or member.role == "owner",
                online=online_devices_by_user.get(member.user_id, 0) > 0,
                device_count=online_devices_by_user.get(member.user_id, 0),
            )
        )
    return responses


def replace_room_queue(room: Room, entries: list[RoomQueueEntry]) -> None:
    room.queue_entries.clear()
    room.queue_entries.extend(entries)
