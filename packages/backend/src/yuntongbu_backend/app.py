from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import auth_router, client_handoff_router, emby_router, rooms_router, web_router, websocket_router
from .api.deps import AppContext
from .config import Settings
from .database import build_database, init_models
from .security import CredentialCipher
from .services import ConnectionManager, EmbyService, HandoffManager


def create_app() -> FastAPI:
    static_dir = Path(__file__).resolve().parent / "static"
    settings = Settings()
    cipher = CredentialCipher(settings)
    database = build_database(settings)
    context = AppContext(
        settings=settings,
        database=database,
        cipher=cipher,
        emby_service=EmbyService(settings, cipher),
        connections=ConnectionManager(),
        handoffs=HandoffManager(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.context = context
        await init_models(database.engine)
        yield
        await context.emby_service.close()
        await database.engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(auth_router)
    app.include_router(client_handoff_router)
    app.include_router(emby_router)
    app.include_router(rooms_router)
    app.include_router(web_router)
    app.include_router(websocket_router)
    return app
