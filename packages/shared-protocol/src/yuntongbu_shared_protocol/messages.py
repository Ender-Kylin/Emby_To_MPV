from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class PlaybackState(StrEnum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    BUFFERING = "buffering"
    ERROR = "error"


class PlaybackCommand(StrEnum):
    LOAD = "load"
    PLAY = "play"
    PAUSE = "pause"
    SEEK = "seek"
    STOP = "stop"
    SYNC = "sync"


class BasePayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MediaDescriptor(BasePayloadModel):
    binding_id: str | None = None
    item_id: str | None = None
    title: str | None = None
    media_url: str | None = None
    media_source_id: str | None = None
    play_session_id: str | None = None
    emby_user_id: str | None = None
    duration_ms: int | None = None
    artwork_url: str | None = None


class QueueEntryDescriptor(BasePayloadModel):
    id: str
    binding_id: str | None = None
    item_id: str
    title: str
    item_type: str | None = None
    duration_ms: int | None = None
    artwork_url: str | None = None
    source_item_id: str | None = None
    source_title: str | None = None
    source_kind: str | None = None


class PlaybackSessionState(BasePayloadModel):
    room_id: str
    version: int = 0
    playback_state: PlaybackState = PlaybackState.STOPPED
    position_ms: int = 0
    server_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    current_media: MediaDescriptor | None = None
    queue_entries: list[QueueEntryDescriptor] = Field(default_factory=list)
    current_queue_index: int | None = None
    writeback_enabled: bool = False


class ClientPlaybackState(BasePayloadModel):
    device_id: str
    device_name: str
    room_id: str
    playback_state: PlaybackState = PlaybackState.STOPPED
    position_ms: int = 0
    duration_ms: int | None = None
    playback_rate: float = 1.0
    paused: bool = False
    path: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None


class ClientHelloPayload(BasePayloadModel):
    room_id: str | None = None
    device_id: str
    device_name: str
    client_version: str = "0.1.0"


class HeartbeatPayload(BasePayloadModel):
    room_id: str | None = None
    device_id: str
    playback_state: PlaybackState = PlaybackState.STOPPED
    position_ms: int = 0


class StateUpdatePayload(BasePayloadModel):
    state: ClientPlaybackState


class CommandAckPayload(BasePayloadModel):
    device_id: str
    command_id: str
    ok: bool = True
    detail: str | None = None


class ClientErrorPayload(BasePayloadModel):
    device_id: str
    detail: str


class RoomMemberDescriptor(BasePayloadModel):
    user_id: str
    username: str
    is_owner: bool
    online: bool = False
    device_count: int = 0


class RoomSnapshotPayload(BasePayloadModel):
    state: PlaybackSessionState
    members: list[RoomMemberDescriptor] = Field(default_factory=list)


class PlaybackCommandPayload(BasePayloadModel):
    command_id: str
    command: PlaybackCommand
    state: PlaybackSessionState
    target_position_ms: int | None = None


class SyncCorrectionPayload(BasePayloadModel):
    command_id: str
    state: PlaybackSessionState
    expected_position_ms: int
    drift_ms: int


class ServerNoticePayload(BasePayloadModel):
    level: Literal["info", "warning", "error"] = "info"
    message: str


class ClientHello(BasePayloadModel):
    message_type: Literal["client_hello"] = "client_hello"
    payload: ClientHelloPayload


class Heartbeat(BasePayloadModel):
    message_type: Literal["heartbeat"] = "heartbeat"
    payload: HeartbeatPayload


class StateUpdate(BasePayloadModel):
    message_type: Literal["state_update"] = "state_update"
    payload: StateUpdatePayload


class CommandAck(BasePayloadModel):
    message_type: Literal["command_ack"] = "command_ack"
    payload: CommandAckPayload


class ClientError(BasePayloadModel):
    message_type: Literal["client_error"] = "client_error"
    payload: ClientErrorPayload


class RoomSnapshotMessage(BasePayloadModel):
    message_type: Literal["room_snapshot"] = "room_snapshot"
    payload: RoomSnapshotPayload


class PlaybackCommandMessage(BasePayloadModel):
    message_type: Literal["playback_command"] = "playback_command"
    payload: PlaybackCommandPayload


class SyncCorrectionMessage(BasePayloadModel):
    message_type: Literal["sync_correction"] = "sync_correction"
    payload: SyncCorrectionPayload


class ServerNoticeMessage(BasePayloadModel):
    message_type: Literal["server_notice"] = "server_notice"
    payload: ServerNoticePayload


ClientToServerMessage = Annotated[
    Union[ClientHello, Heartbeat, StateUpdate, CommandAck, ClientError],
    Field(discriminator="message_type"),
]
ServerToClientMessage = Annotated[
    Union[
        RoomSnapshotMessage,
        PlaybackCommandMessage,
        SyncCorrectionMessage,
        ServerNoticeMessage,
    ],
    Field(discriminator="message_type"),
]


def build_client_message_adapter() -> TypeAdapter[ClientToServerMessage]:
    return TypeAdapter(ClientToServerMessage)


def build_server_message_adapter() -> TypeAdapter[ServerToClientMessage]:
    return TypeAdapter(ServerToClientMessage)
