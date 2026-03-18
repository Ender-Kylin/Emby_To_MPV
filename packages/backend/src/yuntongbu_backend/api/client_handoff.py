from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import RoomMember, User
from ..schemas import ClientHandoffRedeemRequest, ClientHandoffRedeemResponse, UserResponse
from ..security import create_device_session_token, decode_handoff_session_token, unwrap_handoff_payload
from ..services.rooms import room_to_state
from .deps import AppContext, get_context, get_session, load_room

router = APIRouter(prefix="/client-handoffs", tags=["client-handoffs"])


@router.post("/redeem", response_model=ClientHandoffRedeemResponse)
async def redeem_client_handoff(
    payload: ClientHandoffRedeemRequest,
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> ClientHandoffRedeemResponse:
    try:
        try:
            wrapped = unwrap_handoff_payload(payload.handoff_token)
            signed_token = wrapped["token"]
        except Exception:
            signed_token = payload.handoff_token
        claims = decode_handoff_session_token(context.settings, signed_token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid handoff token.") from exc

    user_id = str(claims["sub"])
    room_id = str(claims["room_id"])
    handoff_id = str(claims["jti"])

    try:
        await context.handoffs.redeem(handoff_id=handoff_id, user_id=user_id, room_id=room_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    room = await load_room(session, room_id)
    membership_result = await session.execute(
        select(RoomMember).where(RoomMember.room_id == room.id, RoomMember.user_id == user.id)
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None and room.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a room member.")

    device_session_token = create_device_session_token(
        context.settings,
        user_id=user.id,
        username=user.username,
        room_id=room.id,
        device_id=payload.device_id,
        device_name=payload.device_name,
    )
    return ClientHandoffRedeemResponse(
        device_session_token=device_session_token,
        room_id=room.id,
        room_name=room.name,
        user=UserResponse.model_validate(user),
        playback=room_to_state(room),
    )
