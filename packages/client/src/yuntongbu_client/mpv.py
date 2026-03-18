from __future__ import annotations

import json
import os
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Signal

from yuntongbu_shared_protocol import ClientPlaybackState, PlaybackState

from .config import ClientSettings
from .mpv_discovery import resolve_mpv_executable, source_label


class MpvController(QObject):
    state_changed = Signal(dict)
    status_changed = Signal(str)

    def __init__(self, settings: ClientSettings) -> None:
        super().__init__()
        self._settings = settings
        self._lock = threading.RLock()
        self._pipe_lock = threading.Lock()
        self._pipe_ready = threading.Event()
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._pipe = None
        self._process: subprocess.Popen | None = None
        self._last_loaded_url: str | None = None
        self._last_load_started_at = 0.0
        self._last_seek_at = 0.0
        self._state = {
            "playback_state": PlaybackState.STOPPED.value,
            "position_ms": 0,
            "duration_ms": None,
            "paused": False,
            "playback_rate": 1.0,
            "path": None,
            "error": None,
        }

    def ensure_running(self) -> None:
        with self._lock:
            if self._process and self._process.poll() is None:
                return
            executable = self._resolved_mpv_executable()
            if executable is None:
                message = (
                    "mpv executable was not found. Configure it in Settings or set YT_CLIENT_MPV_PATH for the current user."
                )
                self._update_state(error=message, playback_state=PlaybackState.ERROR.value)
                self.status_changed.emit(message)
                raise RuntimeError(message)
            self._reader_stop.clear()
            self._pipe_ready.clear()
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(
                [
                    executable,
                    "--idle=yes",
                    "--force-window=yes",
                    "--cache=yes",
                    "--demuxer-max-bytes=128M",
                    "--demuxer-max-back-bytes=32M",
                    "--demuxer-readahead-secs=20",
                    f"--input-ipc-server={self._settings.mpv_pipe_name}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()
            self.status_changed.emit("mpv started.")

    def shutdown(self) -> None:
        self._reader_stop.set()
        with self._lock:
            running = self._process is not None and self._process.poll() is None
        if running:
            try:
                self.stop()
            except Exception:
                pass
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
        self.status_changed.emit("mpv stopped.")

    def current_url(self) -> str | None:
        with self._lock:
            return self._last_loaded_url

    def snapshot(self, *, device_id: str, device_name: str, room_id: str) -> ClientPlaybackState:
        with self._lock:
            return ClientPlaybackState(
                device_id=device_id,
                device_name=device_name,
                room_id=room_id,
                playback_state=PlaybackState(self._state["playback_state"]),
                position_ms=int(self._state["position_ms"]),
                duration_ms=self._state["duration_ms"],
                playback_rate=float(self._state["playback_rate"]),
                paused=bool(self._state["paused"]),
                path=self._state["path"],
                error=self._state["error"],
            )

    def load_media(self, media_url: str, position_ms: int = 0) -> None:
        self.ensure_running()
        self._last_loaded_url = media_url
        self._last_load_started_at = time.monotonic()
        self._send_command(["loadfile", media_url, "replace"])
        if position_ms > 0:
            time.sleep(0.15)
            self.seek_absolute(position_ms)
        self.play()

    def play(self) -> None:
        self._send_command(["set_property", "pause", False])
        self._update_state(playback_state=PlaybackState.PLAYING.value, paused=False)

    def pause(self) -> None:
        self._send_command(["set_property", "pause", True])
        self._update_state(playback_state=PlaybackState.PAUSED.value, paused=True)

    def stop(self) -> None:
        self._send_command(["stop"])
        self._update_state(playback_state=PlaybackState.STOPPED.value, paused=False, position_ms=0)

    def seek_absolute(self, position_ms: int) -> None:
        self._last_seek_at = time.monotonic()
        self._send_command(["seek", max(position_ms / 1000.0, 0.0), "absolute"])
        self._update_state(position_ms=max(position_ms, 0))

    def set_speed(self, speed: float) -> None:
        self._send_command(["set_property", "speed", speed])
        self._update_state(playback_rate=speed)

    def _reader_loop(self) -> None:
        pipe = None
        deadline = time.time() + 10
        while not self._reader_stop.is_set() and time.time() < deadline:
            try:
                pipe = open(self._settings.mpv_pipe_name, "r+b", buffering=0)
                break
            except OSError:
                time.sleep(0.25)
        if pipe is None:
            self._update_state(error="Unable to connect to mpv IPC pipe.")
            self.status_changed.emit("Failed to attach to mpv IPC.")
            return

        self._pipe = pipe
        self._pipe_ready.set()
        self._observe_defaults()
        while not self._reader_stop.is_set():
            try:
                line = pipe.readline()
            except OSError:
                break
            if not line:
                time.sleep(0.1)
                continue
            try:
                payload = json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError:
                continue
            self._handle_message(payload)

    def _observe_defaults(self) -> None:
        for property_name in ["pause", "time-pos", "duration", "path", "idle-active"]:
            self._send_command(["observe_property", 1, property_name])

    def _handle_message(self, payload: dict) -> None:
        if payload.get("event") == "property-change":
            name = payload.get("name")
            data = payload.get("data")
            if name == "pause":
                paused = bool(data)
                self._update_state(
                    paused=paused,
                    playback_state=PlaybackState.PAUSED.value if paused else PlaybackState.PLAYING.value,
                )
            elif name == "time-pos":
                self._update_state(position_ms=int(float(data or 0) * 1000))
            elif name == "duration":
                self._update_state(duration_ms=int(float(data or 0) * 1000) if data is not None else None)
            elif name == "path":
                self._update_state(path=data)
            elif name == "idle-active" and data:
                self._update_state(playback_state=PlaybackState.STOPPED.value, paused=False, position_ms=0)
        elif payload.get("event") == "end-file":
            self._update_state(playback_state=PlaybackState.STOPPED.value, paused=False)

    def _send_command(self, command: list[object]) -> None:
        self.ensure_running()
        if not self._pipe_ready.wait(timeout=5):
            self._update_state(error="Timed out waiting for mpv IPC.")
            return
        if self._pipe is None:
            return
        raw = json.dumps({"command": command}, ensure_ascii=True).encode("utf-8") + b"\n"
        with self._pipe_lock:
            try:
                self._pipe.write(raw)
                self._pipe.flush()
            except OSError as exc:
                self._update_state(error=str(exc))

    def _update_state(self, **changes: object) -> None:
        with self._lock:
            self._state.update(changes)
            snapshot = dict(self._state)
        self.state_changed.emit(snapshot)

    def mpv_available(self) -> bool:
        return self._resolved_mpv_executable() is not None

    def resolved_mpv_details(self) -> tuple[str | None, str | None]:
        return resolve_mpv_executable(
            configured_mpv_path=self._settings.mpv_path,
            user_env_mpv_path=self._settings.user_env_mpv_path,
        )

    def resolved_mpv_source_label(self) -> str:
        _, source = self.resolved_mpv_details()
        return source_label(source)

    def should_delay_sync_correction(self) -> bool:
        now = time.monotonic()
        return (now - self._last_load_started_at) < 4.0 or (now - self._last_seek_at) < 2.5

    def _resolved_mpv_executable(self) -> str | None:
        resolved, _ = self.resolved_mpv_details()
        return resolved
