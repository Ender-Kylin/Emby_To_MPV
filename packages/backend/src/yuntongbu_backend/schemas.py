from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from yuntongbu_shared_protocol import PlaybackSessionState


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class UserResponse(APIModel):
    id: str
    username: str
    email: str | None = None


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username_or_email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class TokenPairResponse(APIModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class EmbyBindingCreateRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=128)
    server_url: str = Field(min_length=4, max_length=512)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class EmbyBindingUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=128)
    server_url: str | None = Field(default=None, min_length=4, max_length=512)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=512)


class EmbyBindingResponse(APIModel):
    id: str
    display_name: str
    server_url: str
    username: str
    server_id: str | None = None
    server_name: str | None = None
    emby_user_id: str | None = None
    last_validated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EmbyLibraryResponse(APIModel):
    id: str
    name: str
    collection_type: str | None = None


class EmbyItemResponse(APIModel):
    id: str
    name: str
    item_type: str | None = None
    media_type: str | None = None
    overview: str | None = None
    duration_ms: int | None = None
    artwork_url: str | None = None
    is_folder: bool = False
    child_count: int | None = None
    can_play: bool = False
    can_import: bool = False


class RoomCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    writeback_enabled: bool = False


class JoinRoomRequest(BaseModel):
    invite_code: str = Field(min_length=4, max_length=16)


class ToggleWritebackRequest(BaseModel):
    enabled: bool


class SeekRequest(BaseModel):
    position_ms: int = Field(ge=0)


class PlaybackLoadRequest(BaseModel):
    binding_id: str
    item_id: str


class QueueImportRequest(BaseModel):
    binding_id: str
    item_id: str


class ClientHandoffCreateResponse(APIModel):
    handoff_token: str
    deeplink_url: str
    expires_at: datetime


class ClientHandoffRedeemRequest(BaseModel):
    handoff_token: str = Field(min_length=20)
    device_name: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)


class ClientHandoffRedeemResponse(APIModel):
    device_session_token: str
    room_id: str
    room_name: str
    user: UserResponse
    playback: PlaybackSessionState


class RoomMemberResponse(APIModel):
    user_id: str
    username: str
    is_owner: bool
    online: bool = False
    device_count: int = 0


class RoomResponse(APIModel):
    id: str
    name: str
    invite_code: str
    owner_user_id: str
    is_owner: bool
    writeback_enabled: bool
    playback: PlaybackSessionState
    created_at: datetime
    updated_at: datetime


class PlaybackStateEnvelope(APIModel):
    state: PlaybackSessionState
