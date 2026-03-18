from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YT_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Yuntongbu Backend"
    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./.data/yuntongbu.db"
    jwt_secret: str = "change-me"
    encryption_secret: str = "change-me"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 30
    handoff_token_ttl_seconds: int = 120
    device_session_ttl_hours: int = 12
    sync_small_drift_ms: int = 1500
    sync_seek_drift_ms: int = 4000
    writeback_interval_seconds: int = 10
    emby_request_timeout_seconds: int = 15
    emby_client_name: str = "Yuntongbu"
    emby_device_name: str = "Yuntongbu Backend"
    emby_device_id: str = "yuntongbu-backend"
    host: str = "0.0.0.0"
    port: int = 8000

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        if not value:
            return ["*"]
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def credential_key(self) -> bytes:
        digest = hashlib.sha256(self.encryption_secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def ensure_data_dir(self) -> None:
        if "sqlite" not in self.database_url or ":///" not in self.database_url:
            return
        path = Path(self.database_url.split(":///", maxsplit=1)[1])
        if path.parent and str(path.parent) not in {".", ""}:
            path.parent.mkdir(parents=True, exist_ok=True)
