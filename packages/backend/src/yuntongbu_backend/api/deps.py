from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..database import DatabaseContext
from ..models import Room, RoomMember, User
from ..security import CredentialCipher, decode_access_token
from ..services import ConnectionManager, EmbyService, HandoffManager


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


@dataclass(slots=True)
class AppContext:
    settings: Settings
    database: DatabaseContext
    cipher: CredentialCipher
    emby_service: EmbyService
    connections: ConnectionManager
    handoffs: HandoffManager


def get_context(request: Request) -> AppContext:
    return request.app.state.context


async def get_session(context: AppContext = Depends(get_context)) -> AsyncIterator[AsyncSession]:
    async with context.database.session_maker() as session:
        yield session


def get_settings(context: AppContext = Depends(get_context)) -> Settings:
    return context.settings


def get_emby_service(context: AppContext = Depends(get_context)) -> EmbyService:
    return context.emby_service


def get_connections(context: AppContext = Depends(get_context)) -> ConnectionManager:
    return context.connections


async def find_user_by_identity(session: AsyncSession, identity: str) -> User | None:
    result = await session.execute(select(User).where(or_(User.username == identity, User.email == identity)))
    return result.scalar_one_or_none()


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    try:
        payload = decode_access_token(settings, token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token.") from exc

    result = await session.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    return user


async def load_room(session: AsyncSession, room_id: str) -> Room:
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
    return room


async def get_room_for_user(room_id: str, user: User, session: AsyncSession) -> Room:
    room = await load_room(session, room_id)
    membership_result = await session.execute(
        select(RoomMember).where(RoomMember.room_id == room.id, RoomMember.user_id == user.id)
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None and room.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a room member.")
    return room


async def ensure_room_owner(
    room_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Room:
    room = await get_room_for_user(room_id, user, session)
    if room.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Room owner required.")
    return room
