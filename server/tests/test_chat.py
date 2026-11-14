from __future__ import annotations

import base64
import struct

from fastapi.testclient import TestClient

from .helpers import bearer


PCM = b"\x00\x01" * 320  # 20 ms @ 16 kHz


def _wav_header_ok(data: bytes) -> bool:
    return (
        data[:4] == b"RIFF"
        and data[8:12] == b"WAVE"
        and struct.unpack("<H", data[22:24])[0] == 1
        and struct.unpack("<I", data[24:28])[0] == 16000
        and struct.unpack("<H", data[34:36])[0] == 16
    )


def test_chat_text_requires_login(client: TestClient):
    assert client.post("/api/v1/chat/text", json={"text": "在吗"}).status_code == 401


def test_chat_text_replies(client: TestClient, tokens: dict):
    response = client.post(
        "/api/v1/chat/text",
        json={"text": "在吗", "history": [{"role": "user", "content": "你好"}]},
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 200, response.text
    assert "ESP32-S3" in response.json()["reply"]


def test_chat_text_rejects_long_history(client: TestClient, tokens: dict):
    history = [{"role": "user", "content": "字" * 2000}] * 3
    response = client.post(
        "/api/v1/chat/text",
        json={"text": "在吗", "history": history},
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 422


def test_chat_audio_round_trip(client: TestClient, tokens: dict):
    response = client.post(
        "/api/v1/chat/audio?format=pcm",
        files={"file": ("speech.pcm", PCM, "application/octet-stream")},
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    assert _wav_header_ok(response.content)
    user_text = base64.b64decode(response.headers["X-User-Text-B64"]).decode()
    reply_text = base64.b64decode(response.headers["X-Reply-Text-B64"]).decode()
    assert user_text == "你好，介绍一下你自己"
    assert "ESP32-S3" in reply_text


def test_chat_audio_rejects_empty_upload(client: TestClient, tokens: dict):
    response = client.post(
        "/api/v1/chat/audio?format=pcm",
        files={"file": ("speech.pcm", b"", "application/octet-stream")},
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 400


def test_chat_tts_returns_wav(client: TestClient, tokens: dict):
    response = client.post(
        "/api/v1/chat/tts",
        json={"text": "测试一下。"},
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 200, response.text
    assert _wav_header_ok(response.content)
    assert response.headers["X-Sample-Rate"] == "16000"


def test_chat_tts_rejects_empty_text(client: TestClient, tokens: dict):
    response = client.post(
        "/api/v1/chat/tts",
        json={"text": ""},
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 422
