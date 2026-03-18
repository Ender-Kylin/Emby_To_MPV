from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .config import load_client_settings
from .runtime import HelperRuntime
from .setup_flow import apply_client_integration
from .single_instance import SingleInstanceBridge


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--deeplink")
    parser.add_argument("--setup-mode", choices=["install"])
    parser.add_argument("--mpv-path")
    parser.add_argument("--write-env", action="store_true")
    parser.add_argument("--register-protocol", action="store_true")
    parser.add_argument("--mark-installed", action="store_true")
    args, _ = parser.parse_known_args()

    settings, settings_store = load_client_settings()
    _configure_logging(settings.log_level, settings.log_file)

    if args.setup_mode:
        _run_setup_mode(
            settings,
            settings_store,
            mpv_path=args.mpv_path,
            write_env=args.write_env,
            register_protocol=args.register_protocol,
            mark_installed=args.mark_installed,
        )
        return

    app = QApplication(sys.argv)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        raise SystemExit("System tray is unavailable on this machine.")

    bridge = SingleInstanceBridge(settings.single_instance_name)
    if not bridge.start_or_forward(args.deeplink):
        return

    runtime = HelperRuntime(app, settings, settings_store)
    bridge.message_received.connect(runtime.handle_deeplink)
    runtime.start()

    if args.deeplink:
        QTimer.singleShot(0, lambda: runtime.handle_deeplink(args.deeplink))

    sys.exit(app.exec())


def _configure_logging(log_level: str, log_file) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = RotatingFileHandler(log_file, maxBytes=512_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    stream = sys.stdout or sys.stderr
    if stream is not None:
        stream_handler = logging.StreamHandler(stream)
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)


def _run_setup_mode(
    settings,
    settings_store,
    *,
    mpv_path: str | None,
    write_env: bool,
    register_protocol: bool,
    mark_installed: bool,
) -> None:
    apply_client_integration(
        settings,
        settings_store,
        mpv_path=mpv_path,
        write_environment_variable=write_env,
        register_protocol=register_protocol,
        mark_installed=mark_installed,
        portable_setup_completed=True,
    )


if __name__ == "__main__":
    main()
