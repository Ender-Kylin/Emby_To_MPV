from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yuntongbu_backend.app import create_app
from yuntongbu_backend.services.emby import ImportedQueue


@dataclass
class FakeResolvedPlayback:
    item_id: str
    title: str
    media_url: str
    media_source_id: str
    play_session_id: str
    emby_user_id: str
    duration_ms: int | None
    artwork_url: str | None


class FakeEmbyService:
    async def close(self) -> None:
        return None

    async def validate_binding(self, *, server_url: str, username: str, password: str) -> dict[str, str | None]:
        return {
            "server_id": "server-1",
            "server_name": "Test Server",
            "emby_user_id": "emby-user-1",
        }

    async def list_libraries(self, binding) -> list[dict[str, str | None]]:
        return [{"id": "library-1", "name": "Movies", "collection_type": "movies"}]

    async def list_items(self, binding, **kwargs) -> list[dict[str, str | int | bool | None]]:
        if kwargs.get("global_search"):
            return [
                {
                    "id": "playlist-1",
                    "name": "Demo Playlist",
                    "item_type": "Playlist",
                    "media_type": None,
                    "overview": "Curated episodes",
                    "duration_ms": None,
                    "artwork_url": "http://emby.local/playlist.jpg",
                    "is_folder": True,
                    "child_count": 2,
                    "can_play": False,
                    "can_import": True,
                },
                {
                    "id": "item-1",
                    "name": "Demo Movie",
                    "item_type": "Movie",
                    "media_type": "Video",
                    "overview": "Example",
                    "duration_ms": 120_000,
                    "artwork_url": "http://emby.local/art.jpg",
                    "is_folder": False,
                    "child_count": None,
                    "can_play": True,
                    "can_import": False,
                },
            ]
        return [
            {
                "id": "item-1",
                "name": "Demo Movie",
                "item_type": "Movie",
                "media_type": "Video",
                "overview": "Example",
                "duration_ms": 120_000,
                "artwork_url": "http://emby.local/art.jpg",
                "is_folder": False,
                "child_count": None,
                "can_play": True,
                "can_import": False,
            }
        ]

    async def import_queue(self, binding, item_id: str) -> ImportedQueue:
        return ImportedQueue(
            source_item_id=item_id,
            source_title="Demo Playlist",
            source_kind="playlist",
            items=[
                {
                    "id": "item-1",
                    "name": "Episode 1",
                    "item_type": "Episode",
                    "media_type": "Video",
                    "duration_ms": 120_000,
                    "artwork_url": "http://emby.local/item-1.jpg",
                    "is_folder": False,
                    "child_count": None,
                    "can_play": True,
                    "can_import": False,
                },
                {
                    "id": "item-2",
                    "name": "Episode 2",
                    "item_type": "Episode",
                    "media_type": "Video",
                    "duration_ms": 121_000,
                    "artwork_url": "http://emby.local/item-2.jpg",
                    "is_folder": False,
                    "child_count": None,
                    "can_play": True,
                    "can_import": False,
                },
            ],
        )

    async def resolve_playback(self, binding, item_id: str) -> FakeResolvedPlayback:
        titles = {
            "item-1": "Episode 1",
            "item-2": "Episode 2",
        }
        return FakeResolvedPlayback(
            item_id=item_id,
            title=titles.get(item_id, "Demo Movie"),
            media_url=f"http://emby.local/{item_id}.mkv",
            media_source_id="ms-1",
            play_session_id="ps-1",
            emby_user_id="emby-user-1",
            duration_ms=120_000,
            artwork_url=f"http://emby.local/{item_id}.jpg",
        )

    async def report_started(self, binding, room, position_ms: int = 0) -> None:
        return None

    async def report_progress(self, binding, room, **kwargs) -> None:
        return None

    async def report_stopped(self, binding, room, *, position_ms: int) -> None:
        return None


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("YT_JWT_SECRET", "test-secret")
    monkeypatch.setenv("YT_ENCRYPTION_SECRET", "test-encryption-secret")
    app = create_app()
    with TestClient(app) as test_client:
        test_client.app.state.context.emby_service = FakeEmbyService()
        yield test_client
