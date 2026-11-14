from __future__ import annotations

import threading

from .helpers import bearer, claim_device, hello


def test_panel_to_offline_device_is_not_sent(client, tokens):
    claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])
    response = client.post(
        "/api/v1/devices/dev-alice/panel",
        json={"volume": 40, "emotion": "happy"},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["online"] is False
    assert body["delivered"] is False
    assert "离线" in body["error"]


def test_panel_requires_something_to_do(client, tokens):
    claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])
    response = client.post("/api/v1/devices/dev-alice/panel", json={}, headers=auth)
    assert response.status_code in (422,)


def test_panel_rejects_unknown_emotion(client, tokens):
    claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])
    response = client.post(
        "/api/v1/devices/dev-alice/panel",
        json={"emotion": "excited"},
        headers=auth,
    )
    assert response.status_code == 422


def test_panel_roundtrip_updates_state(client, tokens):
    device_token = claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        result: dict = {}

        def push() -> None:
            result["response"] = client.post(
                "/api/v1/devices/dev-alice/panel",
                json={"volume": 35, "emotion": "happy", "text": "hello"},
                headers=auth,
            )

        worker = threading.Thread(target=push)
        worker.start()
        request = websocket.receive_json()
        assert request["t"] == "panel"
        assert request["volume"] == 35
        assert request["emotion"] == "happy"
        assert request["text"] == "hello"
        assert request["clear"] is False
        websocket.send_json(
            {"v": 1, "t": "panel_ack", "session": 0, "ok": True, "volume": 35}
        )
        worker.join(timeout=15)
        assert not worker.is_alive()

        body = result["response"].json()
        assert body["delivered"] is True
        assert body["volume"] == 35
        assert body["emotion"] == "happy"
        assert body["active"] is True

        state = client.get("/api/v1/devices/dev-alice/panel", headers=auth).json()
        assert state["volume"] == 35
        assert state["emotion"] == "happy"
        assert state["active"] is True


def test_hello_reports_volume(client, tokens):
    device_token = claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        websocket.send_json(
            {
                "v": 1,
                "t": "hello",
                "session": 0,
                "device_id": "dev-alice",
                "fw_version": "test-fw",
                "protocol": "1",
                "hardware": "esp32-s3",
                "volume": 42,
            }
        )
        assert websocket.receive_json()["t"] == "hello"

        state = client.get("/api/v1/devices/dev-alice/panel", headers=auth).json()
        assert state["volume"] == 42


def test_panel_clear_resets_active(client, tokens):
    device_token = claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        result: dict = {}

        def push() -> None:
            result["response"] = client.post(
                "/api/v1/devices/dev-alice/panel",
                json={"clear": True, "volume": 60},
                headers=auth,
            )

        worker = threading.Thread(target=push)
        worker.start()
        request = websocket.receive_json()
        assert request["t"] == "panel"
        assert request["clear"] is True
        assert request["emotion"] is None
        websocket.send_json(
            {"v": 1, "t": "panel_ack", "session": 0, "ok": True, "volume": 60}
        )
        worker.join(timeout=15)
        assert not worker.is_alive()

        body = result["response"].json()
        assert body["delivered"] is True
        assert body["active"] is False
        assert body["volume"] == 60
