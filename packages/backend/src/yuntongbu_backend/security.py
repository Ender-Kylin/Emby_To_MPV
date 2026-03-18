from __future__ import annotations

import hashlib
import json
import secrets
import base64
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from jose import jwt
from passlib.context import CryptContext

from .config import Settings


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


class CredentialCipher:
    def __init__(self, settings: Settings) -> None:
        self._fernet = Fernet(settings.credential_key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(settings: Settings, user_id: str) -> str:
    return _create_typed_token(
        settings,
        token_type="access",
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.access_token_ttl_minutes),
        sub=user_id,
    )


def decode_access_token(settings: Settings, token: str) -> dict[str, str | int]:
    return _decode_typed_token(settings, token, expected_type="access")


def create_handoff_session_token(
    settings: Settings,
    *,
    user_id: str,
    room_id: str,
    handoff_id: str,
) -> str:
    return _create_typed_token(
        settings,
        token_type="handoff",
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.handoff_token_ttl_seconds),
        sub=user_id,
        room_id=room_id,
        jti=handoff_id,
    )


def decode_handoff_session_token(settings: Settings, token: str) -> dict[str, str | int]:
    return _decode_typed_token(settings, token, expected_type="handoff")


def create_device_session_token(
    settings: Settings,
    *,
    user_id: str,
    username: str,
    room_id: str,
    device_id: str,
    device_name: str,
) -> str:
    return _create_typed_token(
        settings,
        token_type="device_session",
        expires_at=datetime.now(UTC) + timedelta(hours=settings.device_session_ttl_hours),
        sub=user_id,
        username=username,
        room_id=room_id,
        device_id=device_id,
        device_name=device_name,
    )


def decode_device_session_token(settings: Settings, token: str) -> dict[str, str | int]:
    return _decode_typed_token(settings, token, expected_type="device_session")


def wrap_handoff_payload(*, backend_url: str, signed_token: str) -> str:
    payload = json.dumps(
        {
            "backend_url": backend_url.rstrip("/"),
            "token": signed_token,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")


def unwrap_handoff_payload(token: str) -> dict[str, str]:
    padding = "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode((token + padding).encode("utf-8")).decode("utf-8")
    payload = json.loads(raw)
    backend_url = str(payload["backend_url"]).rstrip("/")
    signed_token = str(payload["token"])
    if not backend_url or not signed_token:
        raise ValueError("Invalid handoff payload.")
    return {"backend_url": backend_url, "token": signed_token}


def issue_refresh_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return raw_token, token_hash


def _create_typed_token(
    settings: Settings,
    *,
    token_type: str,
    expires_at: datetime,
    **claims: str,
) -> str:
    now = datetime.now(UTC)
    payload = {
        **claims,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _decode_typed_token(settings: Settings, token: str, *, expected_type: str) -> dict[str, str | int]:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != expected_type:
        raise ValueError("invalid token type")
    return payload
