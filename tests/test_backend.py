from __future__ import annotations


def register_user(client, username: str, password: str = "password123", email: str | None = None) -> dict:
    response = client.post(
        "/auth/register",
        json={"username": username, "password": password, "email": email},
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_emby_binding(client, token: str, display_name: str = "My Emby") -> dict:
    response = client.post(
        "/emby-bindings",
        headers=auth_headers(token),
        json={
            "display_name": display_name,
            "server_url": "http://emby.local",
            "username": "emby-owner",
            "password": "emby-password",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def receive_until(websocket, message_type: str) -> dict:
    for _ in range(4):
        payload = websocket.receive_json()
        if payload["message_type"] == message_type:
            return payload
    raise AssertionError(f"Did not receive {message_type}")


def test_register_login_and_refresh(client) -> None:
    registered = register_user(client, "owner", email="owner@example.com")

    login = client.post(
        "/auth/login",
        json={"username_or_email": "owner@example.com", "password": "password123"},
    )
    assert login.status_code == 200, login.text
    login_data = login.json()
    assert login_data["user"]["username"] == "owner"

    refreshed = client.post("/auth/refresh", json={"refresh_token": registered["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    refresh_data = refreshed.json()
    assert refresh_data["access_token"]
    assert refresh_data["refresh_token"] != registered["refresh_token"]


def test_room_playback_and_websocket_broadcast(client) -> None:
    owner = register_user(client, "owner")
    member = register_user(client, "member")

    binding_id = create_emby_binding(client, owner["access_token"])["id"]

    room = client.post(
        "/rooms",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Watch Party", "writeback_enabled": True},
    )
    assert room.status_code == 201, room.text
    room_data = room.json()
    room_id = room_data["id"]

    joined = client.post(
        "/rooms/join",
        headers=auth_headers(member["access_token"]),
        json={"invite_code": room_data["invite_code"]},
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["is_owner"] is False

    rooms = client.get("/rooms", headers=auth_headers(owner["access_token"]))
    assert rooms.status_code == 200, rooms.text
    room_list = rooms.json()
    assert len(room_list) == 1
    assert room_list[0]["id"] == room_id
    assert room_list[0]["is_owner"] is True

    with client.websocket_connect(f"/ws/client?token={member['access_token']}") as websocket:
        first_message = websocket.receive_json()
        assert first_message["message_type"] == "server_notice"

        websocket.send_json(
            {
                "message_type": "client_hello",
                "payload": {
                    "room_id": room_id,
                    "device_id": "device-1",
                    "device_name": "Member PC",
                    "client_version": "0.1.0",
                },
            }
        )
        snapshot = websocket.receive_json()
        assert snapshot["message_type"] == "room_snapshot"

        with client.websocket_connect(f"/ws/rooms/{room_id}?token={owner['access_token']}") as browser_socket:
            observer_notice = browser_socket.receive_json()
            assert observer_notice["message_type"] == "server_notice"
            observer_snapshot = browser_socket.receive_json()
            assert observer_snapshot["message_type"] == "room_snapshot"

            members = client.get(
                f"/rooms/{room_id}/members",
                headers=auth_headers(owner["access_token"]),
            )
            assert members.status_code == 200, members.text
            member_rows = members.json()
            counts_by_user = {row["username"]: row["device_count"] for row in member_rows}
            assert counts_by_user["owner"] == 0
            assert counts_by_user["member"] == 1

            load = client.post(
                f"/rooms/{room_id}/playback/load",
                headers=auth_headers(owner["access_token"]),
                json={"binding_id": binding_id, "item_id": "item-1"},
            )
            assert load.status_code == 200, load.text
            state = load.json()["state"]
            assert state["current_media"]["media_url"] == "http://emby.local/item-1.mkv"

            command = receive_until(websocket, "playback_command")
            assert command["message_type"] == "playback_command"
            assert command["payload"]["command"] == "load"

            browser_command = receive_until(browser_socket, "playback_command")
            assert browser_command["message_type"] == "playback_command"
            assert browser_command["payload"]["command"] == "load"

            pause = client.post(
                f"/rooms/{room_id}/playback/pause",
                headers=auth_headers(owner["access_token"]),
            )
            assert pause.status_code == 200, pause.text
            pause_command = receive_until(websocket, "playback_command")
            assert pause_command["message_type"] == "playback_command"
            assert pause_command["payload"]["command"] == "pause"
            browser_pause = receive_until(browser_socket, "playback_command")
            assert browser_pause["payload"]["command"] == "pause"


def test_client_handoff_redeem_and_device_session(client) -> None:
    owner = register_user(client, "owner")

    room = client.post(
        "/rooms",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Desktop Playback", "writeback_enabled": False},
    )
    assert room.status_code == 201, room.text
    room_data = room.json()

    handoff = client.post(
        f"/rooms/{room_data['id']}/client-handoff",
        headers=auth_headers(owner["access_token"]),
    )
    assert handoff.status_code == 200, handoff.text
    handoff_data = handoff.json()
    assert handoff_data["deeplink_url"].startswith("yuntongbu://play?handoff=")
    assert handoff_data["handoff_token"]

    redeemed = client.post(
        "/client-handoffs/redeem",
        json={
            "handoff_token": handoff_data["handoff_token"],
            "device_name": "Windows Proxy",
            "device_id": "device-proxy-1",
        },
    )
    assert redeemed.status_code == 200, redeemed.text
    redeemed_data = redeemed.json()
    assert redeemed_data["room_id"] == room_data["id"]
    assert redeemed_data["room_name"] == "Desktop Playback"
    assert redeemed_data["device_session_token"]

    redeemed_again = client.post(
        "/client-handoffs/redeem",
        json={
            "handoff_token": handoff_data["handoff_token"],
            "device_name": "Windows Proxy",
            "device_id": "device-proxy-1",
        },
    )
    assert redeemed_again.status_code == 401, redeemed_again.text

    with client.websocket_connect(f"/ws/client?token={redeemed_data['device_session_token']}") as websocket:
        notice = websocket.receive_json()
        assert notice["message_type"] == "server_notice"
        websocket.send_json(
            {
                "message_type": "client_hello",
                "payload": {
                    "room_id": room_data["id"],
                    "device_id": "ignored-by-device-token",
                    "device_name": "Ignored Device Name",
                    "client_version": "0.1.0",
                },
            }
        )
        snapshot = websocket.receive_json()
        assert snapshot["message_type"] == "room_snapshot"
        assert snapshot["payload"]["state"]["room_id"] == room_data["id"]


def test_room_delete_is_owner_only_and_removes_room(client) -> None:
    owner = register_user(client, "owner")
    member = register_user(client, "member")

    room = client.post(
        "/rooms",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Disposable Room", "writeback_enabled": False},
    )
    assert room.status_code == 201, room.text
    room_data = room.json()
    room_id = room_data["id"]

    joined = client.post(
        "/rooms/join",
        headers=auth_headers(member["access_token"]),
        json={"invite_code": room_data["invite_code"]},
    )
    assert joined.status_code == 200, joined.text

    forbidden = client.delete(
        f"/rooms/{room_id}",
        headers=auth_headers(member["access_token"]),
    )
    assert forbidden.status_code == 403, forbidden.text

    deleted = client.delete(
        f"/rooms/{room_id}",
        headers=auth_headers(owner["access_token"]),
    )
    assert deleted.status_code == 204, deleted.text

    owner_rooms = client.get("/rooms", headers=auth_headers(owner["access_token"]))
    assert owner_rooms.status_code == 200, owner_rooms.text
    assert owner_rooms.json() == []

    member_rooms = client.get("/rooms", headers=auth_headers(member["access_token"]))
    assert member_rooms.status_code == 200, member_rooms.text
    assert member_rooms.json() == []

    missing = client.get(f"/rooms/{room_id}", headers=auth_headers(owner["access_token"]))
    assert missing.status_code == 404, missing.text


def test_emby_binding_crud(client) -> None:
    owner = register_user(client, "owner")
    created = create_emby_binding(client, owner["access_token"], display_name="Main Emby")

    listed = client.get("/emby-bindings", headers=auth_headers(owner["access_token"]))
    assert listed.status_code == 200, listed.text
    listed_rows = listed.json()
    assert len(listed_rows) == 1
    assert listed_rows[0]["display_name"] == "Main Emby"

    updated = client.patch(
        f"/emby-bindings/{created['id']}",
        headers=auth_headers(owner["access_token"]),
        json={
          "display_name": "Updated Emby",
          "server_url": "http://emby-updated.local",
          "username": "new-owner",
        },
    )
    assert updated.status_code == 200, updated.text
    updated_payload = updated.json()
    assert updated_payload["display_name"] == "Updated Emby"
    assert updated_payload["server_url"] == "http://emby-updated.local"
    assert updated_payload["username"] == "new-owner"

    deleted = client.delete(
        f"/emby-bindings/{created['id']}",
        headers=auth_headers(owner["access_token"]),
    )
    assert deleted.status_code == 204, deleted.text

    listed_after_delete = client.get("/emby-bindings", headers=auth_headers(owner["access_token"]))
    assert listed_after_delete.status_code == 200, listed_after_delete.text
    assert listed_after_delete.json() == []


def test_global_search_and_queue_import(client) -> None:
    owner = register_user(client, "owner")
    binding_id = create_emby_binding(client, owner["access_token"])["id"]

    room = client.post(
        "/rooms",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Queue Room", "writeback_enabled": False},
    )
    assert room.status_code == 201, room.text
    room_id = room.json()["id"]

    search = client.get(
        f"/emby-bindings/{binding_id}/items",
        headers=auth_headers(owner["access_token"]),
        params={"global_search": True, "search_term": "demo", "limit": 20},
    )
    assert search.status_code == 200, search.text
    items = search.json()
    assert any(item["item_type"] == "Playlist" and item["can_import"] for item in items)
    assert any(item["can_play"] for item in items)

    imported = client.post(
        f"/rooms/{room_id}/queue/import",
        headers=auth_headers(owner["access_token"]),
        json={"binding_id": binding_id, "item_id": "playlist-1"},
    )
    assert imported.status_code == 200, imported.text
    imported_state = imported.json()["state"]
    assert len(imported_state["queue_entries"]) == 2
    assert imported_state["queue_entries"][0]["source_kind"] == "playlist"
    assert imported_state["current_queue_index"] == 0
    assert imported_state["current_media"]["item_id"] == "item-1"

    queue_entry_id = imported_state["queue_entries"][1]["id"]
    loaded = client.post(
        f"/rooms/{room_id}/queue/{queue_entry_id}/load",
        headers=auth_headers(owner["access_token"]),
    )
    assert loaded.status_code == 200, loaded.text
    loaded_state = loaded.json()["state"]
    assert loaded_state["current_media"]["item_id"] == "item-2"
    assert loaded_state["current_queue_index"] == 1

    cleared = client.delete(
        f"/rooms/{room_id}/queue",
        headers=auth_headers(owner["access_token"]),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["state"]["queue_entries"] == []


def test_reimport_queue_replaces_existing_entries_without_position_conflict(client) -> None:
    owner = register_user(client, "owner")
    binding_id = create_emby_binding(client, owner["access_token"])["id"]

    room = client.post(
        "/rooms",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Queue Replace Room", "writeback_enabled": False},
    )
    assert room.status_code == 201, room.text
    room_id = room.json()["id"]

    first_import = client.post(
        f"/rooms/{room_id}/queue/import",
        headers=auth_headers(owner["access_token"]),
        json={"binding_id": binding_id, "item_id": "playlist-1"},
    )
    assert first_import.status_code == 200, first_import.text
    first_state = first_import.json()["state"]
    assert [entry["item_id"] for entry in first_state["queue_entries"]] == ["item-1", "item-2"]

    second_import = client.post(
        f"/rooms/{room_id}/queue/import",
        headers=auth_headers(owner["access_token"]),
        json={"binding_id": binding_id, "item_id": "playlist-1"},
    )
    assert second_import.status_code == 200, second_import.text
    second_state = second_import.json()["state"]
    assert len(second_state["queue_entries"]) == 2
    assert second_state["queue_entries"][0]["item_id"] == "item-1"
    assert second_state["queue_entries"][1]["item_id"] == "item-2"
    assert second_state["current_queue_index"] == 0


def test_web_pages_are_served(client) -> None:
    login_page = client.get("/app/login")
    assert login_page.status_code == 200
    assert "text/html" in login_page.headers["content-type"]
    assert "Web Console" in login_page.text

    register_page = client.get("/app/register")
    assert register_page.status_code == 200
    assert "Create Account" in register_page.text

    dashboard_page = client.get("/app/dashboard")
    assert dashboard_page.status_code == 200
    assert "Emby Bindings" in dashboard_page.text
    assert "Latest Validation Result" in dashboard_page.text

    room_page = client.get("/app/room/example-room")
    assert room_page.status_code == 200
    assert "Current Playback" in room_page.text
    assert "Refresh Bindings" in room_page.text
    assert "Search All" in room_page.text
    assert "Room Queue" in room_page.text


def test_websocket_stays_connected_when_writeback_fails(client) -> None:
    owner = register_user(client, "owner")
    binding_id = create_emby_binding(client, owner["access_token"])["id"]

    room = client.post(
        "/rooms",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Writeback Failure Room", "writeback_enabled": True},
    )
    assert room.status_code == 201, room.text
    room_id = room.json()["id"]

    load = client.post(
        f"/rooms/{room_id}/playback/load",
        headers=auth_headers(owner["access_token"]),
        json={"binding_id": binding_id, "item_id": "item-1"},
    )
    assert load.status_code == 200, load.text

    client.app.state.context.settings.writeback_interval_seconds = 0

    async def failing_progress(*args, **kwargs) -> None:
        if kwargs.get("event_name") == "TimeUpdate":
            raise RuntimeError("emby writeback failed")
        return None

    client.app.state.context.emby_service.report_progress = failing_progress

    with client.websocket_connect(f"/ws/client?token={owner['access_token']}") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "message_type": "client_hello",
                "payload": {
                    "room_id": room_id,
                    "device_id": "device-owner",
                    "device_name": "Owner PC",
                    "client_version": "0.1.0",
                },
            }
        )
        snapshot = websocket.receive_json()
        assert snapshot["message_type"] == "room_snapshot"

        websocket.send_json(
            {
                "message_type": "state_update",
                "payload": {
                    "state": {
                        "device_id": "device-owner",
                        "device_name": "Owner PC",
                        "room_id": room_id,
                        "playback_state": "playing",
                        "position_ms": 5_000,
                        "duration_ms": 120_000,
                        "playback_rate": 1.0,
                        "paused": False,
                        "path": "http://emby.local/item-1.mkv",
                        "error": None,
                    }
                },
            }
        )

        pause = client.post(
            f"/rooms/{room_id}/playback/pause",
            headers=auth_headers(owner["access_token"]),
        )
        assert pause.status_code == 200, pause.text
        pause_command = receive_until(websocket, "playback_command")
        assert pause_command["payload"]["command"] == "pause"


def test_player_snapshot_accepts_integer_device_counts(client) -> None:
    owner = register_user(client, "owner")

    room = client.post(
        "/rooms",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Multi Device Room", "writeback_enabled": False},
    )
    assert room.status_code == 201, room.text
    room_id = room.json()["id"]

    with client.websocket_connect(f"/ws/client?token={owner['access_token']}") as first_socket:
        assert first_socket.receive_json()["message_type"] == "server_notice"
        first_socket.send_json(
            {
                "message_type": "client_hello",
                "payload": {
                    "room_id": room_id,
                    "device_id": "device-1",
                    "device_name": "Owner PC 1",
                    "client_version": "0.1.0",
                },
            }
        )
        first_snapshot = first_socket.receive_json()
        assert first_snapshot["message_type"] == "room_snapshot"
        assert first_snapshot["payload"]["members"][0]["device_count"] == 1

        with client.websocket_connect(f"/ws/client?token={owner['access_token']}") as second_socket:
            assert second_socket.receive_json()["message_type"] == "server_notice"
            second_socket.send_json(
                {
                    "message_type": "client_hello",
                    "payload": {
                        "room_id": room_id,
                        "device_id": "device-2",
                        "device_name": "Owner PC 2",
                        "client_version": "0.1.0",
                    },
                }
            )
            second_snapshot = second_socket.receive_json()
            assert second_snapshot["message_type"] == "room_snapshot"
            assert second_snapshot["payload"]["members"][0]["device_count"] == 2
