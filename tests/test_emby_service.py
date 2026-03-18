from __future__ import annotations

import httpx
import pytest

from yuntongbu_backend.config import Settings
from yuntongbu_backend.security import CredentialCipher
from yuntongbu_backend.services.emby import EmbyError, EmbyService


def make_service() -> EmbyService:
    settings = Settings(
        jwt_secret="test-secret",
        encryption_secret="test-encryption-secret",
    )
    return EmbyService(settings, CredentialCipher(settings))


@pytest.mark.asyncio
async def test_authenticate_preserves_emby_base_path() -> None:
    service = make_service()
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "AccessToken": "access-token",
                "ServerId": "server-1",
                "ServerName": "Demo Server",
                "User": {"Id": "user-1", "Name": "owner"},
            },
        )

    original_client = service._client
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await original_client.aclose()

    try:
        session = await service.authenticate(
            server_url="http://emby.local/emby",
            username="owner",
            password="password",
        )
    finally:
        await service.close()

    assert session.base_url == "http://emby.local/emby"
    assert requests == ["http://emby.local/emby/Users/AuthenticateByName"]


@pytest.mark.asyncio
async def test_authenticate_falls_back_to_root_when_emby_prefix_is_missing() -> None:
    service = make_service()
    request_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        if request.url.path == "/emby/Users/AuthenticateByName":
            return httpx.Response(404, text="Not Found")
        return httpx.Response(
            200,
            json={
                "AccessToken": "access-token",
                "ServerId": "server-1",
                "ServerName": "Root Server",
                "User": {"Id": "user-1", "Name": "owner"},
            },
        )

    original_client = service._client
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await original_client.aclose()

    try:
        session = await service.authenticate(
            server_url="http://emby.local",
            username="owner",
            password="password",
        )
    finally:
        await service.close()

    assert session.base_url == "http://emby.local"
    assert request_paths == [
        "/emby/Users/AuthenticateByName",
        "/Users/AuthenticateByName",
    ]


@pytest.mark.asyncio
async def test_qualify_url_keeps_emby_prefix_for_root_relative_paths() -> None:
    service = make_service()
    try:
        assert service._qualify_url("http://emby.local/emby", "/Videos/item-1/stream.mkv") == (
            "http://emby.local/emby/Videos/item-1/stream.mkv"
        )
        assert service._qualify_url("http://emby.local/emby", "/emby/Videos/item-1/stream.mkv") == (
            "http://emby.local/emby/Videos/item-1/stream.mkv"
        )
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_authenticate_surfaces_invalid_credentials_message() -> None:
    service = make_service()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"Message": "Invalid username or password"})

    original_client = service._client
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await original_client.aclose()

    with pytest.raises(EmbyError, match="username or password is incorrect"):
        try:
            await service.authenticate(
                server_url="http://emby.local",
                username="owner",
                password="wrong-password",
            )
        finally:
            await service.close()


@pytest.mark.asyncio
async def test_rewrite_stream_host_replaces_matching_emby_media_host() -> None:
    service = make_service()
    try:
        rewritten = service._rewrite_stream_host(
            "https://media.micu.hk/emby/Videos/674459/stream.mkv?static=true&api_key=test"
        )
    finally:
        await service.close()

    assert rewritten == "https://tv.micu.hk/emby/Videos/674459/stream.mkv?static=true&api_key=test"
