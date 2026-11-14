from __future__ import annotations

import json

from app.protocol import TYPE_DOWNLINK, pack_frame, parse_frame


PASSWORD = "correct-horse-battery-staple"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client, username: str = "alice") -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()


def claim_device(client, tokens: dict, device_id: str = "dev-alice", user: str = "alice") -> str:
    response = client.post(
        "/api/v1/pairings",
        json={"device_id": device_id},
        headers=bearer(tokens[user]["access_token"]),
    )
    assert response.status_code == 200, response.text
    pairing_token = response.json()["pairing_token"]
    response = client.post(
        "/api/v1/devices/claim",
        json={"device_id": device_id, "pairing_token": pairing_token},
    )
    assert response.status_code == 200, response.text
    return response.json()["device_token"]


def hello(websocket, device_id: str = "dev-alice", panel_auto: bool = False) -> None:
    message = {
        "v": 1,
        "t": "hello",
        "session": 0,
        "device_id": device_id,
        "fw_version": "test-fw",
        "protocol": "1",
        "hardware": "esp32-s3",
    }
    if panel_auto:
        message["panel_auto"] = True
    websocket.send_text(json.dumps(message))
    response = websocket.receive_json()
    assert response["t"] == "hello", response


def start(websocket, session: int) -> None:
    websocket.send_json({"v": 1, "t": "start", "session": session, "sample_rate": 16000})


def receive_dialogue(websocket, session: int) -> tuple[list[dict], bytes]:
    events: list[dict] = []
    audio = bytearray()
    sequences: list[int] = []
    while True:
        message = websocket.receive()
        if message.get("bytes") is not None:
            frame = parse_frame(message["bytes"], expected_type=TYPE_DOWNLINK)
            assert frame.session == session
            sequences.append(frame.sequence)
            audio.extend(frame.payload)
            continue
        event = json.loads(message["text"])
        events.append(event)
        assert event["v"] == 1
        if event["t"] == "error":
            raise AssertionError(event)
        if event["t"] == "done":
            break
    assert sequences == list(range(len(sequences)))
    return events, bytes(audio)


def send_pcm(websocket, session: int, pcm: bytes, chunk_size: int = 640) -> None:
    for sequence, offset in enumerate(range(0, len(pcm), chunk_size)):
        websocket.send_bytes(
            pack_frame(1, session, sequence, pcm[offset : offset + chunk_size])
        )
    websocket.send_json({"v": 1, "t": "end", "session": session})
