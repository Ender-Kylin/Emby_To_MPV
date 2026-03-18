from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from pydantic_settings import BaseSettings, SettingsConfigDict

from .system_integration import executable_path, read_user_environment_variable, runtime_mode


def default_state_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        path = Path(local_app_data) / "Yuntongbu"
    else:
        path = Path(".secrets")
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_mpv_pipe_name() -> str:
    return rf"\\.\pipe\yuntongbu-mpv-{uuid4().hex}"


class ClientEnvironment(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YT_CLIENT_",
        env_file=".env",
        extra="ignore",
    )

    state_dir: Path = default_state_dir()
    log_level: str = "INFO"
    mpv_path: str = ""
    device_name: str = socket.gethostname()
    protocol_scheme: str = "yuntongbu"
    single_instance_name: str = "YuntongbuHelper"


@dataclass(slots=True)
class StoredClientConfig:
    device_id: str
    mpv_path: str = ""
    portable_setup_completed: bool = False


class SettingsStore:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> StoredClientConfig | None:
        if not self._file_path.exists():
            return None
        data = json.loads(self._file_path.read_text(encoding="utf-8"))
        return StoredClientConfig(
            device_id=str(data.get("device_id") or uuid4()),
            mpv_path=str(data.get("mpv_path") or ""),
            portable_setup_completed=bool(data.get("portable_setup_completed", False)),
        )

    def save(self, config: StoredClientConfig) -> None:
        self._file_path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


@dataclass(slots=True)
class ClientSettings:
    state_dir: Path
    logs_dir: Path
    settings_file: Path
    log_level: str
    mpv_path: str
    user_env_mpv_path: str | None
    mpv_pipe_name: str
    device_name: str
    device_id: str
    protocol_scheme: str
    single_instance_name: str
    runtime_mode: str
    executable_path: Path
    portable_setup_completed: bool

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "client.log"


def load_client_settings() -> tuple[ClientSettings, SettingsStore]:
    env = ClientEnvironment()
    env.state_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = env.state_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    settings_file = env.state_dir / "client-settings.json"
    store = SettingsStore(settings_file)
    stored = store.load()
    user_env_mpv_path = read_user_environment_variable("YT_CLIENT_MPV_PATH")
    config = StoredClientConfig(
        device_id=stored.device_id if stored is not None else str(uuid4()),
        mpv_path=stored.mpv_path if stored is not None and stored.mpv_path else (user_env_mpv_path or env.mpv_path),
        portable_setup_completed=stored.portable_setup_completed if stored is not None else False,
    )
    store.save(config)
    return (
        ClientSettings(
            state_dir=env.state_dir,
            logs_dir=logs_dir,
            settings_file=settings_file,
            log_level=env.log_level,
            mpv_path=config.mpv_path,
            user_env_mpv_path=user_env_mpv_path,
            mpv_pipe_name=default_mpv_pipe_name(),
            device_name=env.device_name,
            device_id=config.device_id,
            protocol_scheme=env.protocol_scheme,
            single_instance_name=env.single_instance_name,
            runtime_mode=runtime_mode(),
            executable_path=executable_path(),
            portable_setup_completed=config.portable_setup_completed,
        ),
        store,
    )
