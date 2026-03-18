from __future__ import annotations

import json
import time
from threading import Event

from PySide6.QtCore import QThread, Signal
from websockets.sync.client import connect

from yuntongbu_shared_protocol import (
    ClientHello,
    ClientHelloPayload,
    Heartbeat,
    HeartbeatPayload,
    PlaybackCommand,
    PlaybackCommandMessage,
    PlaybackState,
    RoomSnapshotMessage,
    SyncCorrectionMessage,
    build_server_message_adapter,
)

from .backend_api import BackendAPI
from .mpv import MpvController


class SyncWorker(QThread):
    status_changed = Signal(str)
    room_state_changed = Signal(dict)

    def __init__(
        self,
        *,
        api: BackendAPI,
        mpv: MpvController,
        session_token: str,
        room_id: str,
        device_id: str,
        device_name: str,
    ) -> None:
        super().__init__()
        self._api = api
        self._mpv = mpv
        self._session_token = session_token
        self._room_id = room_id
        self._device_id = device_id
        self._device_name = device_name
        self._stop = Event()
        self._adapter = build_server_message_adapter()

    def stop(self) -> None:
        self._stop.set()
        self.wait(3000)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_connection()
            except Exception as exc:
                if self._stop.is_set():
                    return
                self.status_changed.emit(f"Sync disconnected: {exc}")
                time.sleep(3)

    def _run_connection(self) -> None:
        ws_url = self._api.websocket_url(self._session_token)
        with connect(ws_url, open_timeout=10, close_timeout=3) as websocket:
            websocket.send(
                ClientHello(
                    payload=ClientHelloPayload(
                        room_id=self._room_id,
                        device_id=self._device_id,
                        device_name=self._device_name,
                    )
                ).model_dump_json()
            )
            self.status_changed.emit("Connected to room sync.")
            last_heartbeat = 0.0
            last_state_push = 0.0
            while not self._stop.is_set():
                now = time.monotonic()
                if now - last_heartbeat >= 2:
                    state = self._mpv.snapshot(
                        device_id=self._device_id,
                        device_name=self._device_name,
                        room_id=self._room_id,
                    )
                    websocket.send(
                        Heartbeat(
                            payload=HeartbeatPayload(
                                room_id=self._room_id,
                                device_id=self._device_id,
                                playback_state=state.playback_state,
                                position_ms=state.position_ms,
                            )
                        ).model_dump_json()
                    )
                    last_heartbeat = now

                if now - last_state_push >= 1:
                    websocket.send(
                        json.dumps(
                            {
                                "message_type": "state_update",
                                "payload": {
                                    "state": self._mpv.snapshot(
                                        device_id=self._device_id,
                                        device_name=self._device_name,
                                        room_id=self._room_id,
                                    ).model_dump(mode="json")
                                },
                            }
                        )
                    )
                    last_state_push = now

                try:
                    raw_message = websocket.recv(timeout=0.5)
                except TimeoutError:
                    continue
                if raw_message is None:
                    continue
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                message = self._adapter.validate_python(json.loads(raw_message))
                self._handle_server_message(message)

    def _handle_server_message(self, message) -> None:
        if isinstance(message, RoomSnapshotMessage):
            state = message.payload.state
            self.room_state_changed.emit(state.model_dump(mode="json"))
            self._apply_target_state(state.model_dump(mode="json"))
            return
        if isinstance(message, PlaybackCommandMessage):
            self.room_state_changed.emit(message.payload.state.model_dump(mode="json"))
            self._apply_command(message)
            return
        if isinstance(message, SyncCorrectionMessage):
            self._mpv.seek_absolute(message.payload.expected_position_ms)
            return
        self.status_changed.emit(message.payload.message)

    def _apply_command(self, message: PlaybackCommandMessage) -> None:
        state = message.payload.state
        if message.payload.command == PlaybackCommand.LOAD and state.current_media and state.current_media.media_url:
            self._mpv.load_media(state.current_media.media_url, state.position_ms)
        elif message.payload.command == PlaybackCommand.PLAY:
            self._mpv.play()
        elif message.payload.command == PlaybackCommand.PAUSE:
            self._mpv.pause()
        elif message.payload.command in {PlaybackCommand.SEEK, PlaybackCommand.SYNC}:
            self._mpv.seek_absolute(message.payload.target_position_ms or state.position_ms)
        elif message.payload.command == PlaybackCommand.STOP:
            self._mpv.stop()

    def _apply_target_state(self, state: dict) -> None:
        current_media = state.get("current_media")
        if current_media and current_media.get("media_url") and current_media.get("media_url") != self._mpv.current_url():
            self._mpv.load_media(current_media["media_url"], state.get("position_ms", 0))
        playback_state = state.get("playback_state")
        if playback_state == PlaybackState.PAUSED.value:
            self._mpv.pause()
        elif playback_state == PlaybackState.PLAYING.value and current_media:
            self._mpv.play()
