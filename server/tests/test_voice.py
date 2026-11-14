from __future__ import annotations

import struct

from fastapi.testclient import TestClient


TOKEN = {"Authorization": "Bearer test-voice-token"}


def test_voice_requires_service_token(client: TestClient):
    assert client.post("/api/v1/voice/asr", files={"file": ("a.pcm", b"\x00\x01", "application/octet-stream")}).status_code == 401
    assert client.post("/api/v1/voice/tts", json={"text": "你好"}).status_code == 401
    assert client.post(
        "/api/v1/voice/tts",
        json={"text": "你好"},
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 403


def test_asr_accepts_raw_pcm(client: TestClient):
    response = client.post(
        "/api/v1/voice/asr?format=pcm",
        files={"file": ("a.pcm", b"\x00\x00" * 3200, "application/octet-stream")},
        headers=TOKEN,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"]
    assert body["audio_bytes"] == 6400


def test_asr_rejects_empty_audio(client: TestClient):
    response = client.post(
        "/api/v1/voice/asr?format=pcm",
        files={"file": ("a.pcm", b"", "application/octet-stream")},
        headers=TOKEN,
    )
    assert response.status_code == 400


def test_tts_returns_16k_wav_by_default(client: TestClient):
    response = client.post("/api/v1/voice/tts", json={"text": "你好"}, headers=TOKEN)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["x-sample-rate"] == "16000"
    data = response.content
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    channels = struct.unpack("<H", data[22:24])[0]
    rate = struct.unpack("<I", data[24:28])[0]
    bits = struct.unpack("<H", data[34:36])[0]
    assert (rate, channels, bits) == (16000, 1, 16)
    assert len(data) > 44


def test_tts_returns_raw_pcm_when_requested(client: TestClient):
    response = client.post(
        "/api/v1/voice/tts",
        json={"text": "你好", "format": "pcm"},
        headers=TOKEN,
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/L16"
    assert response.headers["x-format"] == "s16le"
    assert len(response.content) == 6400


def test_tts_rejects_blank_text(client: TestClient):
    assert client.post("/api/v1/voice/tts", json={"text": ""}, headers=TOKEN).status_code == 422
