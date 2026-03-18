from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MpvCandidate:
    path: str
    source: str


SOURCE_LABELS = {
    "settings": "Saved settings",
    "user_env": "User environment variable",
    "path": "PATH",
    "common": "Common install location",
}


def discover_mpv_candidates(
    *,
    stored_mpv_path: str | None,
    user_env_mpv_path: str | None,
) -> list[MpvCandidate]:
    candidates: list[MpvCandidate] = []
    seen: set[str] = set()

    def add(path: str | None, source: str) -> None:
        normalized = normalize_mpv_path(path)
        if normalized is None:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(MpvCandidate(path=normalized, source=source))

    add(stored_mpv_path, "settings")
    add(user_env_mpv_path, "user_env")
    add(_which_mpv(), "path")
    for candidate in common_mpv_locations():
        add(candidate, "common")

    return candidates


def resolve_mpv_executable(
    *,
    configured_mpv_path: str | None,
    user_env_mpv_path: str | None,
) -> tuple[str | None, str | None]:
    direct = normalize_mpv_path(configured_mpv_path)
    if direct:
        return direct, "settings"

    env_path = normalize_mpv_path(user_env_mpv_path)
    if env_path:
        return env_path, "user_env"

    path_candidate = _which_mpv()
    if path_candidate:
        return path_candidate, "path"

    for common_path in common_mpv_locations():
        normalized = normalize_mpv_path(common_path)
        if normalized:
            return normalized, "common"
    return None, None


def validate_mpv_path(path: str | None) -> tuple[bool, str | None]:
    candidate = (path or "").strip()
    if not candidate:
        return True, None
    normalized = normalize_mpv_path(candidate)
    if normalized is None:
        return False, "The selected mpv path does not exist."
    if Path(normalized).name.lower() != "mpv.exe":
        return False, "The selected file must be mpv.exe."
    return True, None


def source_label(source: str | None) -> str:
    if source is None:
        return "Not detected"
    return SOURCE_LABELS.get(source, source)


def common_mpv_locations() -> list[str]:
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(program_files) / "mpv" / "mpv.exe",
        Path(program_files_x86) / "mpv" / "mpv.exe",
    ]
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "mpv" / "mpv.exe")
    candidates.append(Path(r"D:\Programs\mpv\mpv.exe"))
    return [str(path) for path in candidates]


def normalize_mpv_path(path: str | None) -> str | None:
    candidate = (path or "").strip()
    if not candidate:
        return None

    file_candidate = Path(candidate)
    if file_candidate.is_file():
        return str(file_candidate.resolve())
    if file_candidate.is_absolute():
        return None

    resolved = shutil.which(candidate)
    if resolved:
        return str(Path(resolved).resolve())
    return None


def _which_mpv() -> str | None:
    resolved = shutil.which("mpv.exe") or shutil.which("mpv")
    if not resolved:
        return None
    return str(Path(resolved).resolve())
