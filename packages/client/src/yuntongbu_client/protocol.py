from __future__ import annotations

import sys
from pathlib import Path

from .system_integration import PROTOCOL_KEY, is_frozen_bundle


def preferred_protocol_executable(executable: Path | None = None) -> Path:
    if executable is not None:
        return executable
    current = Path(sys.executable).resolve()
    if is_frozen_bundle():
        return current
    if current.name.lower() == "python.exe":
        pythonw = current.with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw
    return current


def protocol_command(executable: Path | None = None) -> str:
    target = preferred_protocol_executable(executable)
    if is_frozen_bundle() or executable is not None:
        return f'"{target}" --deeplink "%1"'
    return f'"{target}" -m yuntongbu_client.app --deeplink "%1"'


def register_protocol_handler(executable: Path | None = None) -> bool:
    if sys.platform != "win32":
        return False

    import winreg

    target = preferred_protocol_executable(executable)
    command = protocol_command(executable)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Yuntongbu Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        with winreg.CreateKey(key, "DefaultIcon") as icon_key:
            winreg.SetValueEx(icon_key, "", 0, winreg.REG_SZ, str(target))

        with winreg.CreateKey(key, r"shell\open\command") as command_key:
            winreg.SetValueEx(command_key, "", 0, winreg.REG_SZ, command)

    return True


def query_protocol_handler() -> dict[str, str] | None:
    if sys.platform != "win32":
        return None

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY) as key:
            description = str(winreg.QueryValueEx(key, "")[0])
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY + r"\shell\open\command") as key:
            command = str(winreg.QueryValueEx(key, "")[0])
    except OSError:
        return None

    return {
        "description": description,
        "command": command,
    }
