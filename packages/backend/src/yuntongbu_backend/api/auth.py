from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import RefreshToken, User
from ..schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenPairResponse, UserResponse
from ..security import create_access_token, hash_password, issue_refresh_token, verify_password
from .deps import find_user_by_identity, get_current_user, get_session, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenPairResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    settings=Depends(get_settings),
) -> TokenPairResponse:
    existing = await find_user_by_identity(session, payload.username)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists.")
    if payload.email:
        existing_email = await find_user_by_identity(session, payload.email)
        if existing_email is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists.")

    user = User(username=payload.username, email=payload.email, password_hash=hash_password(payload.password))
    session.add(user)
    await session.flush()
    response = await _issue_token_pair(session, settings, user)
    await session.commit()
    return response


@router.post("/login", response_model=TokenPairResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings=Depends(get_settings),
) -> TokenPairResponse:
    user = await find_user_by_identity(session, payload.username_or_email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")
    response = await _issue_token_pair(session, settings, user)
    await session.commit()
    return response


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_session),
    settings=Depends(get_settings),
) -> TokenPairResponse:
    token_hash = hashlib.sha256(payload.refresh_token.encode("utf-8")).hexdigest()
    result = await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    refresh_token = result.scalar_one_or_none()
    if refresh_token is None or refresh_token.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")
    if refresh_token.expires_at <= datetime.now(UTC).replace(tzinfo=None):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired.")
    refresh_token.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    user = await session.get(User, refresh_token.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    response = await _issue_token_pair(session, settings, user)
    await session.commit()
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(user)


async def _issue_token_pair(session: AsyncSession, settings, user: User) -> TokenPairResponse:
    access_token = create_access_token(settings, user.id)
    raw_refresh, refresh_hash = issue_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    return TokenPairResponse(access_token=access_token, refresh_token=raw_refresh, user=UserResponse.model_validate(user))
