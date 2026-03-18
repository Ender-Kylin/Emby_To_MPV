from __future__ import annotations

from urllib.parse import urlparse

import httpx


class BackendAPI:
    def __init__(self, backend_url: str) -> None:
        self.backend_url = backend_url.rstrip("/")

    def redeem_handoff(self, handoff_token: str, *, device_name: str, device_id: str) -> dict:
        with httpx.Client(base_url=self.backend_url, timeout=15) as client:
            response = client.post(
                "/client-handoffs/redeem",
                json={
                    "handoff_token": handoff_token,
                    "device_name": device_name,
                    "device_id": device_id,
                },
            )
            response.raise_for_status()
            return response.json()

    def websocket_url(self, session_token: str) -> str:
        parsed = urlparse(self.backend_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/ws/client?token={session_token}"
