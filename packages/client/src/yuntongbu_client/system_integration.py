from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path


MPV_ENV_VAR = "YT_CLIENT_MPV_PATH"
PROTOCOL_KEY = r"Software\Classes\yuntongbu"
INSTALL_MARKER_NAME = "install-mode.json"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_frozen_bundle() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path:
    return Path(sys.executable).resolve()


def bundle_root() -> Path:
    return executable_path().parent


def install_marker_path() -> Path:
    return bundle_root() / INSTALL_MARKER_NAME


def runtime_mode() -> str:
    if not is_frozen_bundle():
        return "development"
    if install_marker_path().exists():
        return "installed"
    return "portable"


def read_user_environment_variable(name: str) -> str | None:
    if not is_windows():
        return os.environ.get(name)

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value = str(winreg.QueryValueEx(key, name)[0]).strip()
            return value or None
    except OSError:
        return os.environ.get(name)


def write_user_environment_variable(name: str, value: str) -> None:
    if not is_windows():
        os.environ[name] = value
        return

    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    os.environ[name] = value
    broadcast_environment_change()


def broadcast_environment_change() -> None:
    if not is_windows():
        return

    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW(
        0xFFFF,
        0x001A,
        0,
        "Environment",
        0x0002,
        5000,
        None,
    )


def write_install_marker(*, installed: bool = True) -> Path:
    marker = install_marker_path()
    marker.write_text(
        json.dumps({"installed": installed}, indent=2),
        encoding="utf-8",
    )
    return marker
