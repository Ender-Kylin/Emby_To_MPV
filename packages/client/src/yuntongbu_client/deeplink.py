from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass(slots=True)
class DeepLinkPayload:
    backend_url: str
    handoff_token: str


def parse_deeplink(url: str) -> DeepLinkPayload:
    parsed = urlparse(url)
    if parsed.scheme != "yuntongbu":
        raise ValueError("Unsupported deeplink scheme.")
    action = parsed.netloc or parsed.path.lstrip("/")
    if action != "play":
        raise ValueError("Unsupported deeplink action.")
    query = parse_qs(parsed.query)
    handoff_values = query.get("handoff")
    if not handoff_values or not handoff_values[0]:
        raise ValueError("Missing deeplink handoff token.")
    wrapped_payload = _decode_wrapped_handoff(handoff_values[0])
    return DeepLinkPayload(
        backend_url=str(wrapped_payload["backend_url"]).rstrip("/"),
        handoff_token=str(wrapped_payload["token"]),
    )


def _decode_wrapped_handoff(token: str) -> dict[str, str]:
    padding = "=" * (-len(token) % 4)
    decoded = base64.urlsafe_b64decode((token + padding).encode("utf-8")).decode("utf-8")
    payload = json.loads(decoded)
    if not payload.get("backend_url") or not payload.get("token"):
        raise ValueError("Invalid handoff token.")
    return payload
