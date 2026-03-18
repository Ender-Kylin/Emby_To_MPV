# Yuntongbu

`Yuntongbu` is a `uv`-managed Python workspace for synchronized `mpv` playback on Windows clients. It integrates with `Emby` for media browsing and direct playback links while the backend coordinates room membership, playback authority, and progress synchronization.

## Workspace Layout

- `packages/shared-protocol`: shared Pydantic models for backend/client websocket messages.
- `packages/backend`: FastAPI backend, auth, Emby binding, room state, and websocket hub.
- `packages/client`: Windows PySide6 tray client that launches `mpv` and applies sync commands.

## Quick Start

1. Install dependencies:

   ```powershell
   uv sync
   ```

2. Start the backend:

   ```powershell
   uv run yuntongbu-backend
   ```

3. Open the web console:

   - Login: `http://127.0.0.1:8000/app/login`
   - Register: `http://127.0.0.1:8000/app/register`
   - Dashboard: `http://127.0.0.1:8000/app/dashboard`

4. Start the Windows player proxy:

   ```powershell
   uv run yuntongbu-client
   ```

5. In a room page, click `Open In Local mpv`.
   The browser will open a `yuntongbu://` link to hand the room off to the local tray proxy.

## Build the Windows Client

The client packaging pipeline produces both a portable archive and a standard installer.

1. Install packaging dependencies:

   ```powershell
   uv sync --group packaging
   ```

2. Build the portable client and installer:

   ```powershell
   .\scripts\build_client.ps1
   ```

   The build uses `PyInstaller onedir` and `Inno Setup`.

3. If `Inno Setup 6` is not on `PATH`, set `ISCC_PATH` to `ISCC.exe` or install it in one of the default locations:

   - `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`
   - `C:\Program Files\Inno Setup 6\ISCC.exe`

4. Output artifacts:

   - `dist\yuntongbu-client-portable.zip`
   - `dist\yuntongbu-client-installer.exe`

The installer lets the user choose the install directory, detects existing `mpv.exe` candidates, writes the current-user `YT_CLIENT_MPV_PATH`, initializes the client settings file, and registers `yuntongbu://`. The portable build shows a first-run setup dialog instead of silently changing the current user's registry or environment.

## Environment

Backend environment variables use the `YT_` prefix. Important keys:

- `YT_DATABASE_URL`: defaults to `sqlite+aiosqlite:///./.data/yuntongbu.db`
- `YT_JWT_SECRET`: JWT signing key
- `YT_ENCRYPTION_SECRET`: secret used to derive the credential encryption key
- `YT_CORS_ORIGINS`: comma-separated origins, defaults to `*`

Client environment variables use the `YT_CLIENT_` prefix. Important keys:

- `YT_CLIENT_MPV_PATH`
- `YT_CLIENT_LOG_LEVEL`

## Linux systemd

For a simple Ubuntu deployment, a ready-to-use `systemd` service template is included:

- Service: `packaging/linux/yuntongbu-backend.service`
- Env example: `packaging/linux/yuntongbu-backend.env.example`

The template is written for this layout:

- repo: `/root/Emby_To_MPV`
- `uv`: `/root/.local/bin/uv`
- SQLite: `/root/Emby_To_MPV/sql/yuntongbu.db`

Install it with:

```bash
cd /root/Emby_To_MPV
mkdir -p sql
sudo cp packaging/linux/yuntongbu-backend.service /etc/systemd/system/yuntongbu-backend.service
sudo cp packaging/linux/yuntongbu-backend.env.example /etc/default/yuntongbu-backend
sudo systemctl daemon-reload
sudo systemctl enable --now yuntongbu-backend
```

Then inspect status and logs with:

```bash
sudo systemctl status yuntongbu-backend
journalctl -u yuntongbu-backend -f
```

## Notes

- The backend ships with SQLite defaults for local development. Production can switch `YT_DATABASE_URL` to PostgreSQL without code changes.
- Multi-node fan-out through Redis is not implemented in this initial version; websocket fan-out is in-process.
- The web console is the primary control surface for login, rooms, and Emby media selection.
- The Windows client is now a local player proxy only. It does not manage login or rooms; it only registers the `yuntongbu://` protocol, launches `mpv`, and joins sync after a browser handoff.
