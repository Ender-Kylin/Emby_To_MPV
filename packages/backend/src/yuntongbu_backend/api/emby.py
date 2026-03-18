from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EmbyBinding, User
from ..schemas import EmbyBindingCreateRequest, EmbyBindingResponse, EmbyBindingUpdateRequest, EmbyItemResponse, EmbyLibraryResponse
from ..services.emby import EmbyError
from .deps import AppContext, get_context, get_current_user, get_emby_service, get_session

router = APIRouter(prefix="/emby-bindings", tags=["emby"])


@router.get("", response_model=list[EmbyBindingResponse])
async def list_bindings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[EmbyBindingResponse]:
    result = await session.execute(select(EmbyBinding).where(EmbyBinding.user_id == user.id).order_by(EmbyBinding.created_at.desc()))
    return [EmbyBindingResponse.model_validate(binding) for binding in result.scalars().all()]


@router.post("", response_model=EmbyBindingResponse, status_code=status.HTTP_201_CREATED)
async def create_binding(
    payload: EmbyBindingCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> EmbyBindingResponse:
    try:
        validation = await context.emby_service.validate_binding(
            server_url=payload.server_url,
            username=payload.username,
            password=payload.password,
        )
    except EmbyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    binding = EmbyBinding(
        user_id=user.id,
        display_name=payload.display_name,
        server_url=payload.server_url.rstrip("/"),
        username=payload.username,
        encrypted_password=context.cipher.encrypt(payload.password),
        server_id=validation["server_id"],
        server_name=validation["server_name"],
        emby_user_id=validation["emby_user_id"],
        last_validated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(binding)
    await session.commit()
    await session.refresh(binding)
    return EmbyBindingResponse.model_validate(binding)


@router.patch("/{binding_id}", response_model=EmbyBindingResponse)
async def update_binding(
    binding_id: str,
    payload: EmbyBindingUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> EmbyBindingResponse:
    binding = await _get_binding(binding_id, user.id, session)
    server_url = payload.server_url or binding.server_url
    username = payload.username or binding.username
    password = payload.password or context.cipher.decrypt(binding.encrypted_password)

    try:
        validation = await context.emby_service.validate_binding(server_url=server_url, username=username, password=password)
    except EmbyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.display_name is not None:
        binding.display_name = payload.display_name
    binding.server_url = server_url.rstrip("/")
    binding.username = username
    binding.encrypted_password = context.cipher.encrypt(password)
    binding.server_id = validation["server_id"]
    binding.server_name = validation["server_name"]
    binding.emby_user_id = validation["emby_user_id"]
    binding.last_validated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(binding)
    return EmbyBindingResponse.model_validate(binding)


@router.delete("/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(
    binding_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    binding = await _get_binding(binding_id, user.id, session)
    await session.delete(binding)
    await session.commit()


@router.get("/{binding_id}/libraries", response_model=list[EmbyLibraryResponse])
async def list_libraries(
    binding_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    emby_service=Depends(get_emby_service),
) -> list[EmbyLibraryResponse]:
    binding = await _get_binding(binding_id, user.id, session)
    try:
        data = await emby_service.list_libraries(binding)
    except EmbyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [EmbyLibraryResponse.model_validate(item) for item in data]


@router.get("/{binding_id}/items", response_model=list[EmbyItemResponse])
async def list_items(
    binding_id: str,
    parent_id: str | None = Query(default=None),
    recursive: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    search_term: str | None = Query(default=None),
    global_search: bool = Query(default=False),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    emby_service=Depends(get_emby_service),
) -> list[EmbyItemResponse]:
    binding = await _get_binding(binding_id, user.id, session)
    try:
        data = await emby_service.list_items(
            binding,
            parent_id=parent_id,
            recursive=recursive,
            limit=limit,
            search_term=search_term,
            global_search=global_search,
        )
    except EmbyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [EmbyItemResponse.model_validate(item) for item in data]


async def _get_binding(binding_id: str, user_id: str, session: AsyncSession) -> EmbyBinding:
    result = await session.execute(select(EmbyBinding).where(EmbyBinding.id == binding_id, EmbyBinding.user_id == user_id))
    binding = result.scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Emby binding not found.")
    return binding
