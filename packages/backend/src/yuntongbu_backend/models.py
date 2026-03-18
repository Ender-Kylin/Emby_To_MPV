from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user")
    emby_bindings: Mapped[list["EmbyBinding"]] = relationship(back_populates="user")
    owned_rooms: Mapped[list["Room"]] = relationship(back_populates="owner")
    room_memberships: Mapped[list["RoomMember"]] = relationship(back_populates="user")


class RefreshToken(TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class EmbyBinding(TimestampMixin, Base):
    __tablename__ = "emby_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    server_url: Mapped[str] = mapped_column(String(512))
    username: Mapped[str] = mapped_column(String(128))
    encrypted_password: Mapped[str] = mapped_column(Text)
    server_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    server_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    emby_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="emby_bindings")
    rooms: Mapped[list["Room"]] = relationship(back_populates="current_binding")


class Room(TimestampMixin, Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(128))
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    current_binding_id: Mapped[str | None] = mapped_column(ForeignKey("emby_bindings.id", ondelete="SET NULL"), nullable=True)
    current_item_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_item_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    current_media_source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_play_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_emby_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artwork_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_position_ms: Mapped[int] = mapped_column(Integer, default=0)
    playback_state: Mapped[str] = mapped_column(String(32), default="stopped")
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    writeback_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    server_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_writeback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    writeback_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[User] = relationship(back_populates="owned_rooms")
    current_binding: Mapped[EmbyBinding | None] = relationship(back_populates="rooms")
    members: Mapped[list["RoomMember"]] = relationship(back_populates="room", cascade="all, delete-orphan")
    queue_entries: Mapped[list["RoomQueueEntry"]] = relationship(
        back_populates="room",
        cascade="all, delete-orphan",
        order_by="RoomQueueEntry.position",
    )


class RoomQueueEntry(TimestampMixin, Base):
    __tablename__ = "room_queue_entries"
    __table_args__ = (UniqueConstraint("room_id", "position", name="uq_room_queue_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    binding_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    item_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(256))
    item_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artwork_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_item_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)

    room: Mapped[Room] = relationship(back_populates="queue_entries")


class RoomMember(TimestampMixin, Base):
    __tablename__ = "room_members"
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_room_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")

    room: Mapped[Room] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="room_memberships")
