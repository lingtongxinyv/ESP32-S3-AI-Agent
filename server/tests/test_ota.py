from __future__ import annotations

import hashlib

from .helpers import bearer, claim_device


def upload_firmware(
    client,
    access_token: str,
    content: bytes,
    version: str = "1.2.3",
    hardware: str = "esp32-s3",
    protocol: str = "1",
) -> dict:
    response = client.post(
        "/api/v1/firmware",
        headers=bearer(access_token),
        data={"version": version, "hardware": hardware, "protocol": protocol},
        files={"file": ("firmware.bin", content, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_firmware_hash_publish_and_device_ota_flow(client, tokens):
    device_token = claim_device(client, tokens)
    content = bytes(range(256)) * 17 + b"ota-payload"
    alice_access = tokens["alice"]["access_token"]
    release = upload_firmware(client, alice_access, content)
    assert release["sha256"] == hashlib.sha256(content).hexdigest()
    assert release["size"] == len(content)
    assert release["published_at"] is None
    assert "file_path" not in release
    assert client.get("/api/v1/firmware", headers=bearer(alice_access)).json()["items"][0][
        "id"
    ] == release["id"]

    requested_before_publish = client.post(
        "/api/v1/devices/dev-alice/ota",
        headers=bearer(alice_access),
        json={"firmware_id": release["id"]},
    )
    assert requested_before_publish.status_code == 400
    assert client.post(
        f"/api/v1/firmware/{release['id']}/publish", headers=bearer(alice_access)
    ).status_code == 403
    published = client.post(
        f"/api/v1/firmware/{release['id']}/publish",
        headers=bearer(tokens["admin"]["access_token"]),
    )
    assert published.status_code == 200
    assert published.json()["published_at"] is not None

    requested = client.post(
        "/api/v1/devices/dev-alice/ota",
        headers=bearer(alice_access),
        json={"firmware_id": release["id"]},
    )
    assert requested.status_code == 200
    assert requested.json()["event"] == "requested"
    assert client.post(
        "/api/v1/devices/dev-alice/ota",
        headers=bearer(tokens["bob"]["access_token"]),
        json={"firmware_id": release["id"]},
    ).status_code == 404

    current = client.get("/api/v1/device/ota/current", headers=bearer(device_token))
    assert current.status_code == 200
    current_data = current.json()
    assert current_data["update_available"] is True
    assert current_data["firmware_id"] == release["id"]
    assert current_data["sha256"] == release["sha256"]
    assert current_data["download_url"].endswith(
        f"{release['id']}?token={device_token}"
    )
    prefixed = client.get(
        "/api/v1/device/ota/current",
        headers={**bearer(device_token), "X-Forwarded-Prefix": "/agent"},
    ).json()
    assert "/agent/api/v1/device/ota/download/" in prefixed["download_url"]

    downloaded = client.get(
        current_data["download_url"], headers=bearer(device_token)
    )
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["x-sha256"] == hashlib.sha256(content).hexdigest()
    assert hashlib.sha256(downloaded.content).hexdigest() == release["sha256"]

    result = client.post(
        "/api/v1/device/ota/result",
        headers=bearer(device_token),
        json={"firmware_id": release["id"], "status": "success", "detail": "verified"},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "success"
    events = client.get(
        "/api/v1/devices/dev-alice/ota", headers=bearer(alice_access)
    ).json()["items"]
    assert [event["event"] for event in events] == ["result", "download", "requested"]
    assert events[0]["fw_version"] == release["version"]
    assert client.get(
        "/api/v1/devices/dev-alice/ota", headers=bearer(tokens["bob"]["access_token"])
    ).status_code == 404

    current_after = client.get(
        "/api/v1/device/ota/current", headers=bearer(device_token)
    ).json()
    # 下载/校验成功不代表新固件已启动；运行版本只能由设备 hello 更新。
    assert current_after["current_version"] == ""
    assert current_after["update_available"] is True


def test_device_ota_rest_accepts_query_token(client, tokens):
    """cpolar 会剥离 Authorization 头，设备 REST 必须支持 ?token=。"""
    device_token = claim_device(client, tokens)
    release = upload_firmware(client, tokens["admin"]["access_token"], b"firmware")
    assert client.post(
        f"/api/v1/firmware/{release['id']}/publish",
        headers=bearer(tokens["admin"]["access_token"]),
    ).status_code == 200

    current = client.get(f"/api/v1/device/ota/current?token={device_token}")
    assert current.status_code == 200
    assert f"?token={device_token}" in current.json()["download_url"]
    assert client.get(
        f"/api/v1/device/ota/download/{release['id']}?token={device_token}"
    ).status_code == 200
    assert client.post(
        f"/api/v1/device/ota/result?token={device_token}",
        json={"firmware_id": release["id"], "status": "failed", "detail": "test"},
    ).status_code == 200
    assert client.get("/api/v1/device/ota/current?token=wrong-token").status_code == 401


def test_ota_download_url_uses_configured_public_base(client, tokens, monkeypatch):
    device_token = claim_device(client, tokens)
    release = upload_firmware(client, tokens["admin"]["access_token"], b"firmware")
    assert client.post(
        f"/api/v1/firmware/{release['id']}/publish",
        headers=bearer(tokens["admin"]["access_token"]),
    ).status_code == 200
    monkeypatch.setattr(
        client.app.state.settings, "public_base_url", "https://agent.example/agent"
    )

    current = client.get(
        "/api/v1/device/ota/current", headers=bearer(device_token)
    )
    assert current.status_code == 200
    assert current.json()["download_url"] == (
        f"https://agent.example/agent/api/v1/device/ota/download/{release['id']}"
        f"?token={device_token}"
    )
    download = client.get(
        f"/api/v1/device/ota/download/{release['id']}?token={device_token}"
    )
    assert download.status_code == 200
    assert download.content == b"firmware"


class _OnlineConnection:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.messages = []

    async def send_json(self, session, event_type, **payload):
        if self.fail:
            raise RuntimeError("simulated closed connection")
        self.messages.append({"session": session, "t": event_type, **payload})


def _published_release(client, tokens, **kwargs) -> dict:
    release = upload_firmware(client, tokens["admin"]["access_token"], b"remote-ota", **kwargs)
    response = client.post(
        f"/api/v1/firmware/{release['id']}/publish",
        headers=bearer(tokens["admin"]["access_token"]),
    )
    assert response.status_code == 200
    return release


def test_online_ota_request_notifies_device(client, tokens):
    claim_device(client, tokens)
    release = _published_release(client, tokens)
    connection = _OnlineConnection()
    client.app.state.online_devices.sessions["dev-alice"] = connection

    response = client.post(
        "/api/v1/devices/dev-alice/ota",
        headers=bearer(tokens["alice"]["access_token"]),
        json={"firmware_id": release["id"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert connection.messages == [
        {"session": 0, "t": "ota_check", "firmware_id": release["id"]}
    ]


def test_ota_notification_failure_keeps_pending_request(client, tokens):
    claim_device(client, tokens)
    release = _published_release(client, tokens)
    client.app.state.online_devices.sessions["dev-alice"] = _OnlineConnection(fail=True)

    response = client.post(
        "/api/v1/devices/dev-alice/ota",
        headers=bearer(tokens["alice"]["access_token"]),
        json={"firmware_id": release["id"]},
    )

    assert response.status_code == 200
    events = client.get(
        "/api/v1/devices/dev-alice/ota", headers=bearer(tokens["alice"]["access_token"])
    ).json()["items"]
    assert [(item["event"], item["status"]) for item in events] == [("requested", "pending")]


def test_ota_request_rejects_incompatible_hardware_and_protocol(client, tokens):
    claim_device(client, tokens)
    for release in (
        _published_release(client, tokens, version="2.0.0", hardware="esp32-c3"),
        _published_release(client, tokens, version="2.0.1", protocol="2"),
    ):
        response = client.post(
            "/api/v1/devices/dev-alice/ota",
            headers=bearer(tokens["alice"]["access_token"]),
            json={"firmware_id": release["id"]},
        )
        assert response.status_code == 400
        assert "不匹配" in response.json()["detail"]
