from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
)
from PySide6.QtCore import QUrl

from .backend_api import BackendAPI
from .config import ClientSettings, SettingsStore, default_mpv_pipe_name
from .deeplink import parse_deeplink
from .mpv_discovery import discover_mpv_candidates, resolve_mpv_executable, source_label, validate_mpv_path
from .mpv import MpvController
from .protocol import query_protocol_handler, register_protocol_handler
from .setup_flow import apply_client_integration, maybe_run_portable_bootstrap
from .sync import SyncWorker


def apply_playback_snapshot_to_mpv(mpv: MpvController, playback: dict | None) -> bool:
    if not playback:
        return False
    current_media = playback.get("current_media")
    media_url = current_media.get("media_url") if isinstance(current_media, dict) else None
    if not media_url:
        return False
    position_ms = int(playback.get("position_ms", 0) or 0)
    if media_url != mpv.current_url():
        mpv.load_media(media_url, position_ms)
    playback_state = str(playback.get("playback_state") or "").lower()
    if playback_state == "paused":
        mpv.pause()
    elif playback_state == "playing":
        mpv.play()
    return True


@dataclass(slots=True)
class ActiveSession:
    backend_url: str
    room_id: str
    room_name: str
    username: str
    device_session_token: str


class SettingsDialog(QDialog):
    def __init__(self, settings: ClientSettings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Yuntongbu Player Proxy Settings")
        self._mpv_path = QLineEdit(settings.mpv_path)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_mpv)
        detect_button = QPushButton("Re-detect")
        detect_button.clicked.connect(self._redetect_mpv)
        protocol_button = QPushButton("Re-register Protocol")
        protocol_button.clicked.connect(self._register_protocol)
        self._write_env = QCheckBox("Write current-user YT_CLIENT_MPV_PATH on save")
        self._write_env.setChecked(True)
        self._effective_path = QLabel()
        self._runtime_mode = QLabel(f"Runtime mode: {settings.runtime_mode}")

        row = QHBoxLayout()
        row.addWidget(self._mpv_path)
        row.addWidget(browse_button)
        row.addWidget(detect_button)
        row_widget = QWidget()
        row_widget.setLayout(row)

        form = QFormLayout(self)
        form.addRow("mpv.exe path", row_widget)
        form.addRow("Detected source", self._effective_path)
        form.addRow("", self._runtime_mode)
        form.addRow("", self._write_env)
        form.addRow("", protocol_button)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._mpv_path.textChanged.connect(self._refresh_detected_info)
        self._refresh_detected_info()

    def mpv_path(self) -> str:
        return self._mpv_path.text().strip()

    def should_write_env(self) -> bool:
        return self._write_env.isChecked()

    def _browse_mpv(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Select mpv executable", self._mpv_path.text() or "", "Executable (*.exe)")
        if selected:
            self._mpv_path.setText(selected)

    def _redetect_mpv(self) -> None:
        candidates = discover_mpv_candidates(
            stored_mpv_path=self._mpv_path.text(),
            user_env_mpv_path=self._settings.user_env_mpv_path,
        )
        if candidates:
            self._mpv_path.setText(candidates[0].path)
            return
        QMessageBox.information(self, "mpv Not Found", "No existing mpv.exe was detected. Browse to one manually.")

    def _register_protocol(self) -> None:
        try:
            register_protocol_handler(self._protocol_target())
        except Exception as exc:
            QMessageBox.critical(self, "Protocol Registration Failed", str(exc))
            return
        QMessageBox.information(self, "Protocol Registered", "Registered yuntongbu:// for the current user.")

    def _refresh_detected_info(self) -> None:
        current_text = self._mpv_path.text().strip()
        is_valid, error = validate_mpv_path(current_text)
        effective_path, detected_source = resolve_mpv_executable(
            configured_mpv_path=current_text,
            user_env_mpv_path=self._settings.user_env_mpv_path,
        )
        if current_text and not is_valid:
            self._effective_path.setText(error or "Invalid path.")
            return
        if effective_path:
            self._effective_path.setText(f"{source_label(detected_source)}: {effective_path}")
            return
        self._effective_path.setText("Not detected. The client can still start, but mpv playback will be unavailable.")

    def _protocol_target(self):
        if self._settings.runtime_mode == "development":
            return None
        return self._settings.executable_path


class DiagnosticsDialog(QDialog):
    def __init__(self, title: str, diagnostics_text: str, logs_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        self._logs_dir = logs_dir
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setPlainText(diagnostics_text)

        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self._copy)
        logs_button = QPushButton("Open Logs")
        logs_button.clicked.connect(self._open_logs)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(copy_button)
        buttons.addWidget(logs_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._text)
        layout.addLayout(buttons)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._text.toPlainText())

    def _open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._logs_dir)))


class HelperRuntime(QObject):
    def __init__(self, app: QApplication, settings: ClientSettings, settings_store: SettingsStore) -> None:
        super().__init__()
        self._app = app
        self._settings = settings
        self._settings_store = settings_store
        self._logger = logging.getLogger("yuntongbu.client")
        self._mpv = MpvController(self._settings)
        self._worker: SyncWorker | None = None
        self._active_session: ActiveSession | None = None
        self._last_status = "Idle"
        self._last_error: str | None = None
        self._last_sync_status = "Idle"
        self._last_playback_state = "stopped"

        icon = app.style().standardIcon(QStyle.SP_MediaPlay)
        self._tray = QSystemTrayIcon(icon)
        self._tray.setToolTip("Yuntongbu Player Proxy")
        self._tray.activated.connect(self._on_activated)

        menu = QMenu()
        self._status_action = QAction("Status: Idle", menu)
        self._status_action.setEnabled(False)
        stop_action = QAction("Stop Playback", menu)
        stop_action.triggered.connect(self.stop_playback)
        restart_action = QAction("Restart Player Proxy", menu)
        restart_action.triggered.connect(self.restart_proxy)
        settings_action = QAction("Settings...", menu)
        settings_action.triggered.connect(self.open_settings)
        diagnostics_action = QAction("Diagnostics...", menu)
        diagnostics_action.triggered.connect(self.open_diagnostics)
        register_action = QAction("Re-register Protocol", menu)
        register_action.triggered.connect(self.reregister_protocol)
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit)

        menu.addAction(self._status_action)
        menu.addSeparator()
        menu.addAction(stop_action)
        menu.addAction(restart_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addAction(diagnostics_action)
        menu.addAction(register_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)

        self._mpv.status_changed.connect(self._on_mpv_status)
        self._mpv.state_changed.connect(self._on_mpv_state)

    def start(self) -> None:
        self._tray.show()
        try:
            if self._settings.runtime_mode == "portable":
                maybe_run_portable_bootstrap(self._settings, self._settings_store)
            else:
                register_protocol_handler(self._protocol_target())
                self._logger.info("Protocol handler registered.")
        except Exception as exc:
            self._record_error(f"Protocol registration failed: {exc}")
        if self._mpv.mpv_available():
            self._set_status("Idle", notify=False)
        else:
            self._set_status("mpv not configured", notify=False)

    def handle_deeplink(self, deeplink: str) -> None:
        self._logger.info("Handling deeplink.")
        try:
            payload = parse_deeplink(deeplink)
            api = BackendAPI(payload.backend_url)
            redeemed = api.redeem_handoff(
                payload.handoff_token,
                device_name=self._settings.device_name,
                device_id=self._settings.device_id,
            )
            self._activate_session(api, payload.backend_url, redeemed)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text or f"{exc.response.status_code} {exc.response.reason_phrase}"
            self._record_error(f"Handoff redeem failed: {detail}")
        except Exception as exc:
            self._record_error(f"Unable to open local playback handoff: {exc}")

    def stop_playback(self) -> None:
        self._stop_worker()
        self._active_session = None
        self._mpv.shutdown()
        self._settings.mpv_pipe_name = default_mpv_pipe_name()
        self._last_sync_status = "Idle"
        self._last_playback_state = "stopped"
        self._set_status("Idle")

    def restart_proxy(self) -> None:
        self.stop_playback()
        try:
            if self._settings.runtime_mode != "portable":
                register_protocol_handler(self._protocol_target())
            self._set_status("Proxy restarted")
        except Exception as exc:
            self._record_error(f"Protocol registration failed: {exc}")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._settings)
        if dialog.exec() != QDialog.Accepted:
            return
        mpv_path = dialog.mpv_path()
        try:
            apply_client_integration(
                self._settings,
                self._settings_store,
                mpv_path=mpv_path,
                write_environment_variable=dialog.should_write_env(),
                register_protocol=False,
                mark_installed=False,
                portable_setup_completed=self._settings.portable_setup_completed,
            )
        except ValueError as exc:
            QMessageBox.warning(None, "Invalid mpv Path", str(exc))
            return
        self._logger.info("Updated mpv path to %s", mpv_path)
        self.stop_playback()
        self._notify("Saved settings. The player proxy will use the new mpv path next time it starts playback.")

    def open_diagnostics(self) -> None:
        dialog = DiagnosticsDialog(
            "Yuntongbu Diagnostics",
            self.build_diagnostics_text(),
            self._settings.logs_dir,
        )
        dialog.exec()

    def reregister_protocol(self) -> None:
        try:
            register_protocol_handler(self._protocol_target())
            self._notify("Re-registered the yuntongbu:// protocol.")
        except Exception as exc:
            self._record_error(f"Protocol registration failed: {exc}")

    def quit(self) -> None:
        self.stop_playback()
        self._tray.hide()
        self._app.quit()

    def build_diagnostics_text(self) -> str:
        protocol = query_protocol_handler()
        protocol_text = "Not registered." if protocol is None else f"{protocol['description']}\nCommand: {protocol['command']}"
        effective_mpv_path, effective_source = self._mpv.resolved_mpv_details()
        lines = [
            "Yuntongbu Player Proxy Diagnostics",
            "",
            f"Runtime mode: {self._settings.runtime_mode}",
            f"Device name: {self._settings.device_name}",
            f"Device id: {self._settings.device_id}",
            f"Current room: {self._active_session.room_name if self._active_session else 'Idle'}",
            f"Room id: {self._active_session.room_id if self._active_session else '-'}",
            f"User: {self._active_session.username if self._active_session else '-'}",
            f"Playback state: {self._last_playback_state}",
            f"Sync status: {self._last_sync_status}",
            f"Configured mpv path: {self._settings.mpv_path or '-'}",
            f"User env mpv path: {self._settings.user_env_mpv_path or '-'}",
            f"Resolved mpv path: {effective_mpv_path or '-'}",
            f"Resolved mpv source: {source_label(effective_source)}",
            f"mpv available: {self._mpv.mpv_available()}",
            f"Log file: {self._settings.log_file}",
            "",
            "Protocol registration:",
            protocol_text,
        ]
        if self._last_error:
            lines.extend(["", "Last error:", self._last_error])
        return "\n".join(lines)

    def _activate_session(self, api: BackendAPI, backend_url: str, redeemed: dict) -> None:
        self._stop_worker()
        self._settings.mpv_pipe_name = default_mpv_pipe_name()
        self._mpv.ensure_running()
        playback = redeemed.get("playback")
        if apply_playback_snapshot_to_mpv(self._mpv, playback):
            current_media = playback.get("current_media") or {}
            self._logger.info("Primed mpv from handoff playback: %s", current_media.get("media_url"))
        else:
            self._logger.info("Handoff playback contained no current media to load.")
        self._active_session = ActiveSession(
            backend_url=backend_url,
            room_id=redeemed["room_id"],
            room_name=redeemed["room_name"],
            username=redeemed["user"]["username"],
            device_session_token=redeemed["device_session_token"],
        )
        self._worker = SyncWorker(
            api=api,
            mpv=self._mpv,
            session_token=redeemed["device_session_token"],
            room_id=redeemed["room_id"],
            device_id=self._settings.device_id,
            device_name=self._settings.device_name,
        )
        self._worker.status_changed.connect(self._on_sync_status)
        self._worker.room_state_changed.connect(self._on_room_state_changed)
        self._worker.start()
        self._last_error = None
        self._set_status(f"Connected to {redeemed['room_name']}")
        self._notify(f"Connected local playback to room {redeemed['room_name']}.")

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None

    def _on_sync_status(self, message: str) -> None:
        self._logger.info(message)
        self._last_sync_status = message
        self._set_status(message, notify=False)

    def _on_room_state_changed(self, state: dict) -> None:
        self._last_playback_state = str(state.get("playback_state", "unknown"))
        room_name = self._active_session.room_name if self._active_session else "Idle"
        self._tray.setToolTip(f"Yuntongbu Player Proxy | {room_name} | {self._last_playback_state}")
        self._status_action.setText(f"Status: {self._last_playback_state}")

    def _on_mpv_status(self, message: str) -> None:
        self._logger.info("mpv: %s", message)
        self._last_sync_status = message
        self._tray.setToolTip(f"Yuntongbu Player Proxy | {message}")

    def _on_mpv_state(self, state: dict) -> None:
        self._last_playback_state = str(state.get("playback_state", self._last_playback_state))

    def _set_status(self, message: str, *, notify: bool = True) -> None:
        self._last_status = message
        self._status_action.setText(f"Status: {message}")
        if notify:
            self._notify(message)

    def _record_error(self, message: str) -> None:
        self._last_error = message
        self._logger.error(message)
        self._status_action.setText("Status: Error")
        self._notify(message, icon=QSystemTrayIcon.Critical)

    def _notify(self, message: str, *, icon=QSystemTrayIcon.Information) -> None:
        self._tray.showMessage("Yuntongbu", message, icon, 3500)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.open_diagnostics()

    def _protocol_target(self):
        if self._settings.runtime_mode == "development":
            return None
        return self._settings.executable_path
