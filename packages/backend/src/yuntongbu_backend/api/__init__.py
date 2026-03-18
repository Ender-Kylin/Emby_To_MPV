from .auth import router as auth_router
from .client_handoff import router as client_handoff_router
from .emby import router as emby_router
from .rooms import router as rooms_router
from .web import router as web_router
from .websocket import router as websocket_router

__all__ = ["auth_router", "client_handoff_router", "emby_router", "rooms_router", "web_router", "websocket_router"]
