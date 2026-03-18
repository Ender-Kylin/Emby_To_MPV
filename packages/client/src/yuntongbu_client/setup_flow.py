from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .config import ClientSettings, SettingsStore, StoredClientConfig
from .mpv_discovery import discover_mpv_candidates, normalize_mpv_path, source_label, validate_mpv_path
from .protocol import register_protocol_handler
from .system_integration import MPV_ENV_VAR, write_install_marker, write_user_environment_variable


@dataclass(slots=True)
class PortableBootstrapDecision:
    mpv_path: str
    register_protocol: bool
    write_environment_variable: bool


class PortableBootstrapDialog(QDialog):
    def __init__(self, settings: ClientSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Yuntongbu Portable Setup")
        self.resize(560, 260)
        self._save_local_only = False

        description = QLabel(
            "Select an mpv.exe path for this portable client. You can also register the yuntongbu:// protocol and"
            " write the current-user YT_CLIENT_MPV_PATH now, or keep the changes local to this portable copy."
        )
        description.setWordWrap(True)

        self._mpv_combo = QComboBox()
        self._mpv_combo.setEditable(True)
        self._source_label = QLabel("Detected source: Not detected")
        self._populate_candidates(settings)

        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_mpv)

        selector_row = QHBoxLayout()
        selector_row.addWidget(self._mpv_combo, 1)
        selector_row.addWidget(browse_button)
        selector_widget = QWidget()
        selector_widget.setLayout(selector_row)

        self._register_protocol = QCheckBox("Register yuntongbu:// for the current user")
        self._register_protocol.setChecked(True)
        self._write_env = QCheckBox("Write current-user YT_CLIENT_MPV_PATH")
        self._write_env.setChecked(True)

        continue_button = QPushButton("Continue")
        continue_button.clicked.connect(self._accept_continue)
        local_only_button = QPushButton("Use Local Settings Only")
        local_only_button.clicked.connect(self._accept_local_only)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(local_only_button)
        actions.addWidget(continue_button)

        layout = QVBoxLayout(self)
        layout.addWidget(description)
        layout.addWidget(selector_widget)
        layout.addWidget(self._source_label)
        layout.addWidget(self._register_protocol)
        layout.addWidget(self._write_env)
        layout.addStretch(1)
        layout.addLayout(actions)

        self._mpv_combo.editTextChanged.connect(self._refresh_source_label)
        self._refresh_source_label(self._mpv_combo.currentText())

    @property
    def save_local_only(self) -> bool:
        return self._save_local_only

    def decision(self) -> PortableBootstrapDecision:
        return PortableBootstrapDecision(
            mpv_path=self._mpv_combo.currentText().strip(),
            register_protocol=self._register_protocol.isChecked() and not self._save_local_only,
            write_environment_variable=self._write_env.isChecked() and not self._save_local_only,
        )

    def _populate_candidates(self, settings: ClientSettings) -> None:
        candidates = discover_mpv_candidates(
            stored_mpv_path=settings.mpv_path,
            user_env_mpv_path=settings.user_env_mpv_path,
        )
        if not candidates and settings.mpv_path:
            self._mpv_combo.addItem(settings.mpv_path)
            return
        for candidate in candidates:
            self._mpv_combo.addItem(candidate.path, source_label(candidate.source))

    def _refresh_source_label(self, value: str) -> None:
        index = self._mpv_combo.currentIndex()
        detected_source = self._mpv_combo.itemData(index) if index >= 0 else None
        if index >= 0 and self._mpv_combo.itemText(index) == value and detected_source:
            self._source_label.setText(f"Detected source: {detected_source}")
        else:
            self._source_label.setText("Detected source: Manual selection")

    def _browse_mpv(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select mpv executable",
            self._mpv_combo.currentText() or "",
            "Executable (*.exe)",
        )
        if selected:
            self._mpv_combo.setEditText(selected)

    def _accept_continue(self) -> None:
        if not self._validate():
            return
        self._save_local_only = False
        self.accept()

    def _accept_local_only(self) -> None:
        if not self._validate():
            return
        self._save_local_only = True
        self.accept()

    def _validate(self) -> bool:
        is_valid, error = validate_mpv_path(self._mpv_combo.currentText())
        if not is_valid:
            QMessageBox.warning(self, "Invalid mpv Path", error or "Invalid mpv path.")
            return False
        return True


def apply_client_integration(
    settings: ClientSettings,
    settings_store: SettingsStore,
    *,
    mpv_path: str | None,
    write_environment_variable: bool,
    register_protocol: bool,
    mark_installed: bool,
    portable_setup_completed: bool | None,
) -> None:
    candidate = (mpv_path or settings.mpv_path).strip()
    is_valid, error = validate_mpv_path(candidate)
    if not is_valid:
        raise ValueError(error or "Invalid mpv path.")
    normalized = normalize_mpv_path(candidate) if candidate else None

    stored = StoredClientConfig(
        device_id=settings.device_id,
        mpv_path=normalized or candidate,
        portable_setup_completed=settings.portable_setup_completed if portable_setup_completed is None else portable_setup_completed,
    )
    settings_store.save(stored)
    settings.mpv_path = stored.mpv_path
    settings.portable_setup_completed = stored.portable_setup_completed

    if write_environment_variable and stored.mpv_path:
        if normalized is None:
            raise ValueError("The selected mpv path cannot be written to the environment variable.")
        write_user_environment_variable(MPV_ENV_VAR, normalized)
        settings.user_env_mpv_path = normalized

    if register_protocol:
        register_protocol_handler(settings.executable_path)

    if mark_installed:
        write_install_marker(installed=True)


def maybe_run_portable_bootstrap(
    settings: ClientSettings,
    settings_store: SettingsStore,
    parent=None,
) -> bool:
    if settings.runtime_mode != "portable" or settings.portable_setup_completed:
        return False

    dialog = PortableBootstrapDialog(settings, parent=parent)
    result = dialog.exec()
    decision = dialog.decision()
    apply_client_integration(
        settings,
        settings_store,
        mpv_path=decision.mpv_path,
        write_environment_variable=decision.write_environment_variable if result == QDialog.Accepted else False,
        register_protocol=decision.register_protocol if result == QDialog.Accepted else False,
        mark_installed=False,
        portable_setup_completed=True,
    )
    return True
