from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from ..config import Settings
from ..models import EmbyBinding, Room
from ..security import CredentialCipher


class EmbyError(RuntimeError):
    pass


@dataclass(slots=True)
class EmbySession:
    base_url: str
    access_token: str
    user_id: str
    username: str
    server_id: str | None
    server_name: str | None


@dataclass(slots=True)
class ResolvedPlayback:
    item_id: str
    title: str
    media_url: str
    media_source_id: str
    play_session_id: str
    emby_user_id: str
    duration_ms: int | None
    artwork_url: str | None


@dataclass(slots=True)
class ImportedQueue:
    source_item_id: str
    source_title: str
    source_kind: str
    items: list[dict[str, str | int | bool | None]]


class EmbyService:
    def __init__(self, settings: Settings, cipher: CredentialCipher) -> None:
        self._settings = settings
        self._cipher = cipher
        self._client = httpx.AsyncClient(timeout=settings.emby_request_timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def validate_binding(self, *, server_url: str, username: str, password: str) -> dict[str, str | None]:
        session = await self.authenticate(server_url=server_url, username=username, password=password)
        return {
            "server_id": session.server_id,
            "server_name": session.server_name,
            "emby_user_id": session.user_id,
        }

    async def list_libraries(self, binding: EmbyBinding) -> list[dict[str, str | None]]:
        session = await self._session_for_binding(binding)
        payload = await self._request("GET", session, f"/Users/{session.user_id}/Views")
        items = payload.get("Items", payload)
        return [
            {
                "id": item["Id"],
                "name": item.get("Name", "Unknown"),
                "collection_type": item.get("CollectionType"),
            }
            for item in items
        ]

    async def list_items(
        self,
        binding: EmbyBinding,
        *,
        parent_id: str | None = None,
        recursive: bool = False,
        limit: int = 50,
        search_term: str | None = None,
        global_search: bool = False,
    ) -> list[dict[str, str | int | bool | None]]:
        session = await self._session_for_binding(binding)
        params = {
            "Limit": str(limit),
            "Fields": "Overview,Path,ChildCount",
        }
        if global_search:
            params["Recursive"] = "true"
            params["IncludeItemTypes"] = "Movie,Episode,Video,Playlist,BoxSet"
            if search_term:
                params["SearchTerm"] = search_term
        else:
            params["Recursive"] = str(recursive).lower()
            params["IncludeItemTypes"] = "Movie,Episode,Video,Playlist,BoxSet"
            if parent_id:
                params["ParentId"] = parent_id
            if search_term:
                params["SearchTerm"] = search_term

        payload = await self._request("GET", session, f"/Users/{session.user_id}/Items", params=params)
        items = payload.get("Items", payload)
        return [self._map_item_summary(session, item) for item in items]

    async def import_queue(self, binding: EmbyBinding, item_id: str) -> ImportedQueue:
        session = await self._session_for_binding(binding)
        source = await self._request("GET", session, f"/Users/{session.user_id}/Items/{item_id}")
        source_type = source.get("Type")
        if source_type == "Playlist":
            payload = await self._request(
                "GET",
                session,
                f"/Playlists/{item_id}/Items",
                params={
                    "UserId": session.user_id,
                    "Limit": "500",
                    "Fields": "Overview,Path,ChildCount",
                },
            )
        elif source_type == "BoxSet":
            payload = await self._request(
                "GET",
                session,
                f"/Users/{session.user_id}/Items",
                params={
                    "AncestorIds": item_id,
                    "Recursive": "true",
                    "Limit": "500",
                    "Fields": "Overview,Path,ChildCount",
                    "MediaTypes": "Video",
                    "SortBy": "SortName",
                },
            )
        else:
            raise EmbyError("Only Emby playlists and box sets can be imported into the room queue.")

        items = payload.get("Items", payload)
        mapped_items = [
            self._map_item_summary(session, item)
            for item in items
            if self._item_can_play(item)
        ]
        if not mapped_items:
            raise EmbyError("The selected playlist or box set does not contain playable video items.")

        return ImportedQueue(
            source_item_id=item_id,
            source_title=source.get("Name", "Imported Queue"),
            source_kind=(source_type or "unknown").lower(),
            items=mapped_items,
        )

    async def resolve_playback(self, binding: EmbyBinding, item_id: str) -> ResolvedPlayback:
        session = await self._session_for_binding(binding)
        item = await self._request("GET", session, f"/Users/{session.user_id}/Items/{item_id}")
        playback_info = await self._request("GET", session, f"/Items/{item_id}/PlaybackInfo", params={"UserId": session.user_id})
        media_sources = playback_info.get("MediaSources") or []
        if not media_sources:
            raise EmbyError("No playable media source returned by Emby.")
        media_source = media_sources[0]
        play_session_id = playback_info.get("PlaySessionId") or secrets.token_urlsafe(12)
        direct_stream_url = media_source.get("DirectStreamUrl")

        if direct_stream_url:
            media_url = self._qualify_url(session.base_url, direct_stream_url)
            if media_source.get("AddApiKeyToDirectStreamUrl"):
                media_url = self._append_query(media_url, {"api_key": session.access_token})
        else:
            container = media_source.get("Container") or "mkv"
            media_url = self._append_query(
                self._join_under_base(session.base_url, f"/Videos/{item_id}/stream.{container}"),
                {
                    "static": "true",
                    "MediaSourceId": media_source["Id"],
                    "PlaySessionId": play_session_id,
                    "api_key": session.access_token,
                },
            )

        return ResolvedPlayback(
            item_id=item_id,
            title=item.get("Name", "Unknown"),
            media_url=media_url,
            media_source_id=media_source["Id"],
            play_session_id=play_session_id,
            emby_user_id=session.user_id,
            duration_ms=self._ticks_to_ms(item.get("RunTimeTicks") or media_source.get("RunTimeTicks")),
            artwork_url=self._image_url(session.base_url, item_id, item.get("PrimaryImageTag"), session.access_token),
        )

    async def report_started(self, binding: EmbyBinding, room: Room, position_ms: int = 0) -> None:
        await self._report_playstate(binding, room, position_ms=position_ms, endpoint="/Sessions/Playing")

    async def report_progress(
        self,
        binding: EmbyBinding,
        room: Room,
        *,
        position_ms: int,
        event_name: str = "TimeUpdate",
        paused: bool = False,
    ) -> None:
        await self._report_playstate(
            binding,
            room,
            position_ms=position_ms,
            endpoint="/Sessions/Playing/Progress",
            extra={"EventName": event_name, "IsPaused": paused},
        )

    async def report_stopped(self, binding: EmbyBinding, room: Room, *, position_ms: int) -> None:
        await self._report_playstate(binding, room, position_ms=position_ms, endpoint="/Sessions/Playing/Stopped")

    async def authenticate(self, *, server_url: str, username: str, password: str) -> EmbySession:
        attempt_errors: list[str] = []
        for base_url in self._candidate_base_urls(server_url):
            try:
                response = await self._client.post(
                    self._join_under_base(base_url, "/Users/AuthenticateByName"),
                    headers={
                        "X-Emby-Authorization": self._authorization_header(),
                        "Content-Type": "application/json",
                    },
                    json={"Username": username, "Pw": password},
                )
            except httpx.TimeoutException as exc:
                attempt_errors.append(
                    f"{base_url} timed out. Check that the Emby server is online and reachable from this backend. ({exc})"
                )
                continue
            except httpx.HTTPError as exc:
                attempt_errors.append(
                    f"{base_url} could not be reached. Check the Emby server URL and network connectivity. ({exc})"
                )
                continue

            if response.status_code in {401, 403}:
                raise EmbyError("Emby authentication failed: username or password is incorrect.")
            if response.status_code == 404:
                attempt_errors.append(
                    f"{base_url} did not expose the Emby authentication API. Try the server root URL or the /emby path."
                )
                continue
            if response.status_code >= 500:
                attempt_errors.append(f"{base_url} returned Emby server error HTTP {response.status_code}.")
                continue
            if response.status_code >= 400:
                attempt_errors.append(f"{base_url} returned HTTP {response.status_code}: {self._response_detail(response)}")
                continue

            data = response.json()
            user = data.get("User") or {}
            if not data.get("AccessToken") or not user.get("Id"):
                attempt_errors.append(f"{base_url} returned an incomplete authentication response")
                continue

            return EmbySession(
                base_url=base_url,
                access_token=data["AccessToken"],
                user_id=user["Id"],
                username=user.get("Name", username),
                server_id=data.get("ServerId"),
                server_name=user.get("ServerName") or data.get("ServerName"),
            )

        if not attempt_errors:
            raise EmbyError("Emby authentication failed.")
        raise EmbyError(f"Emby authentication failed. Tried: {'; '.join(attempt_errors)}")

    async def _session_for_binding(self, binding: EmbyBinding) -> EmbySession:
        return await self.authenticate(
            server_url=binding.server_url,
            username=binding.username,
            password=self._cipher.decrypt(binding.encrypted_password),
        )

    async def _request(
        self,
        method: str,
        session: EmbySession,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict | None = None,
    ) -> dict:
        try:
            response = await self._client.request(
                method,
                self._join_under_base(session.base_url, path),
                params=params,
                json=json,
                headers={
                    "X-Emby-Authorization": self._authorization_header(user_id=session.user_id, token=session.access_token),
                    "X-Emby-Token": session.access_token,
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise EmbyError(
                f"Emby request timed out for {path}. Check that the Emby server is online and reachable. ({exc})"
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbyError(
                f"Could not reach the Emby server for {path}. Check the server URL and network connectivity. ({exc})"
            ) from exc

        if response.status_code in {401, 403}:
            raise EmbyError("Emby request failed: saved Emby credentials are no longer valid.")
        if response.status_code == 404:
            raise EmbyError(
                f"Emby API endpoint was not found for {path}. Check whether the server URL should include /emby."
            )
        if response.status_code >= 400:
            raise EmbyError(f"Emby request failed for {path}: {self._response_detail(response)}")
        if not response.content:
            return {}
        return response.json()

    async def _report_playstate(
        self,
        binding: EmbyBinding,
        room: Room,
        *,
        position_ms: int,
        endpoint: str,
        extra: dict[str, str | int | bool] | None = None,
    ) -> None:
        session = await self._session_for_binding(binding)
        payload: dict[str, str | int | bool | list[str]] = {
            "QueueableMediaTypes": ["Video"],
            "CanSeek": True,
            "ItemId": room.current_item_id or "",
            "MediaSourceId": room.current_media_source_id or "",
            "IsPaused": room.playback_state == "paused",
            "PlayMethod": "DirectPlay",
            "PlaySessionId": room.current_play_session_id or "",
            "PositionTicks": position_ms * 10_000,
        }
        if extra:
            payload.update(extra)
        await self._request("POST", session, endpoint, json=payload)

    def _candidate_base_urls(self, server_url: str) -> list[str]:
        cleaned = server_url.strip().rstrip("/")
        parsed = urlparse(cleaned)
        if not parsed.scheme or not parsed.netloc:
            raise EmbyError("Emby server URL must include scheme and host, for example http://127.0.0.1:8096/emby.")

        base_path = parsed.path.rstrip("/")
        base_url = urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", "")).rstrip("/")

        candidates: list[str] = []
        if base_path:
            candidates.append(base_url)
            if base_path.endswith("/emby"):
                trimmed_path = base_path.removesuffix("/emby")
                trimmed_url = urlunparse((parsed.scheme, parsed.netloc, trimmed_path, "", "", "")).rstrip("/")
                if trimmed_url:
                    candidates.append(trimmed_url)
                else:
                    candidates.append(urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/"))
            else:
                candidates.append(f"{base_url}/emby")
        else:
            candidates.extend([f"{base_url}/emby", base_url])
        return list(dict.fromkeys(candidates))

    def _authorization_header(self, *, user_id: str | None = None, token: str | None = None) -> str:
        values = [
            f'Client="{self._settings.emby_client_name}"',
            f'Device="{self._settings.emby_device_name}"',
            f'DeviceId="{self._settings.emby_device_id}"',
            'Version="0.1.0"',
        ]
        if user_id:
            values.insert(0, f'UserId="{user_id}"')
        if token:
            values.append(f'Token="{token}"')
        return "Emby " + ", ".join(values)

    def _image_url(self, base_url: str, item_id: str, image_tag: str | None, access_token: str) -> str | None:
        if not image_tag:
            return None
        return self._append_query(
            self._join_under_base(base_url, f"/Items/{item_id}/Images/Primary"),
            {"tag": image_tag, "api_key": access_token},
        )

    def _qualify_url(self, base_url: str, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        if not path_or_url.startswith("/"):
            return self._join_under_base(base_url, path_or_url)

        parsed_base = urlparse(base_url)
        origin = urlunparse((parsed_base.scheme, parsed_base.netloc, "", "", "", ""))
        base_path = parsed_base.path.rstrip("/")
        if base_path and (path_or_url == base_path or path_or_url.startswith(f"{base_path}/")):
            return f"{origin}{path_or_url}"
        if base_path:
            return f"{origin}{base_path}{path_or_url}"
        return f"{origin}{path_or_url}"

    def _join_under_base(self, base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    def _append_query(self, url: str, params: dict[str, str]) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query))
        query.update(params)
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _response_detail(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            return text[:240] if text else response.reason_phrase

        for key in ("detail", "message", "Message", "error", "Error"):
            value = payload.get(key)
            if value:
                return str(value)
        return response.reason_phrase

    def _map_item_summary(self, session: EmbySession, item: dict) -> dict[str, str | int | bool | None]:
        item_type = item.get("Type")
        media_type = item.get("MediaType")
        return {
            "id": item["Id"],
            "name": item.get("Name", "Unknown"),
            "item_type": item_type,
            "media_type": media_type,
            "overview": item.get("Overview"),
            "duration_ms": self._ticks_to_ms(item.get("RunTimeTicks")),
            "artwork_url": self._image_url(session.base_url, item["Id"], item.get("PrimaryImageTag"), session.access_token),
            "is_folder": bool(item.get("IsFolder", False)),
            "child_count": item.get("ChildCount"),
            "can_play": self._item_can_play(item),
            "can_import": item_type in {"Playlist", "BoxSet"},
        }

    def _item_can_play(self, item: dict) -> bool:
        return item.get("MediaType") == "Video" and not bool(item.get("IsFolder", False))

    def _ticks_to_ms(self, ticks: int | None) -> int | None:
        if ticks is None:
            return None
        return int(ticks / 10_000)
