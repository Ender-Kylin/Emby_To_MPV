from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..config import Settings
from ..security import create_handoff_session_token, wrap_handoff_payload


@dataclass(slots=True)
class IssuedHandoff:
    id: str
    user_id: str
    room_id: str
    expires_at: datetime


class HandoffManager:
    def __init__(self) -> None:
        self._issued: dict[str, IssuedHandoff] = {}
        self._used: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def issue(self, settings: Settings, *, backend_url: str, user_id: str, room_id: str) -> tuple[str, datetime]:
        async with self._lock:
            self._cleanup_locked()
            handoff_id = secrets.token_urlsafe(18)
            expires_at = datetime.now(UTC) + timedelta(seconds=settings.handoff_token_ttl_seconds)
            self._issued[handoff_id] = IssuedHandoff(
                id=handoff_id,
                user_id=user_id,
                room_id=room_id,
                expires_at=expires_at,
            )
            signed_token = create_handoff_session_token(
                settings,
                user_id=user_id,
                room_id=room_id,
                handoff_id=handoff_id,
            )
            return wrap_handoff_payload(backend_url=backend_url, signed_token=signed_token), expires_at

    async def redeem(self, *, handoff_id: str, user_id: str, room_id: str) -> None:
        async with self._lock:
            self._cleanup_locked()
            if handoff_id in self._used:
                raise ValueError("This handoff link has already been used.")
            record = self._issued.get(handoff_id)
            if record is None or record.expires_at <= datetime.now(UTC):
                raise ValueError("This handoff link is invalid or expired.")
            if record.user_id != user_id or record.room_id != room_id:
                raise ValueError("This handoff link does not match the requested room.")
            self._issued.pop(handoff_id, None)
            self._used[handoff_id] = record.expires_at

    def _cleanup_locked(self) -> None:
        now = datetime.now(UTC)
        for handoff_id in [key for key, value in self._issued.items() if value.expires_at <= now]:
            self._issued.pop(handoff_id, None)
        for handoff_id in [key for key, expires_at in self._used.items() if expires_at <= now]:
            self._used.pop(handoff_id, None)
