from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse


router = APIRouter(include_in_schema=False)

WEB_DIR = Path(__file__).resolve().parents[1] / "static" / "web"


def _page(name: str) -> FileResponse:
    return FileResponse(WEB_DIR / name)


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/app/dashboard")


@router.get("/app")
async def app_root() -> RedirectResponse:
    return RedirectResponse("/app/dashboard")


@router.get("/app/login")
async def login_page() -> FileResponse:
    return _page("login.html")


@router.get("/app/register")
async def register_page() -> FileResponse:
    return _page("register.html")


@router.get("/app/dashboard")
async def dashboard_page() -> FileResponse:
    return _page("dashboard.html")


@router.get("/app/room/{room_id}")
async def room_page(room_id: str) -> FileResponse:
    return _page("room.html")
