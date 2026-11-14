from __future__ import annotations

import threading

import pytest
from pydantic import ValidationError

from app.providers import dialogue_from_profile
from app.schemas import ProfilePayload, wake_word_to_command

from .helpers import bearer, claim_device, hello

BUNDLE = {
    "version": 1,
    "persona": "你是测试机器人",
    "llm": {
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "sk-test-llm",
    },
    "asr": {
        "base_url": "https://api.example.com/v1",
        "model": "whisper-1",
        "api_key": "sk-test-asr",
    },
    "tts": {
        "base_url": "https://api.example.com/v1",
        "model": "tts-1",
        "api_key": "sk-test-tts",
        "voice": "alloy",
    },
}


def test_push_config_requires_login(client, tokens):
    claim_device(client, tokens)
    response = client.post("/api/v1/devices/dev-alice/config", json=BUNDLE)
    assert response.status_code == 401


def test_push_config_to_offline_device_is_stored(client, tokens):
    claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])
    response = client.post("/api/v1/devices/dev-alice/config", json=BUNDLE, headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["online"] is False
    assert body["delivered"] is False
    assert body["version"] == 1

    status = client.get("/api/v1/devices/dev-alice/config/status", headers=auth).json()
    assert status["version"] == 1
    assert status["online"] is False
    profile = client.app.state.profiles.get("dev-alice")
    assert profile is not None
    assert profile.bundle["wake_command"] == "xiao ling xiao ling"


def test_custom_wake_word_is_trimmed_and_client_cannot_override_command(client, tokens):
    claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])
    payload = {
        **BUNDLE,
        "wake_word": "  小绿起床  ",
        "wake_command": "client supplied command",
    }
    response = client.post("/api/v1/devices/dev-alice/config", json=payload, headers=auth)
    assert response.status_code == 200, response.text
    profile = client.app.state.profiles.get("dev-alice")
    assert profile is not None
    assert profile.bundle["wake_word"] == "小绿起床"
    assert profile.bundle["wake_command"] == "xiao lv qi chuang"


def test_other_user_cannot_push_config(client, tokens):
    claim_device(client, tokens)
    response = client.post(
        "/api/v1/devices/dev-alice/config",
        json=BUNDLE,
        headers=bearer(tokens["bob"]["access_token"]),
    )
    assert response.status_code == 404


def test_invalid_bundle_is_rejected(client, tokens):
    claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])
    broken = {**BUNDLE, "llm": {"base_url": "", "model": "", "api_key": ""}}
    response = client.post("/api/v1/devices/dev-alice/config", json=broken, headers=auth)
    assert response.status_code == 422


def test_stored_config_is_delivered_on_hello(client, tokens):
    device_token = claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])
    client.post("/api/v1/devices/dev-alice/config", json=BUNDLE, headers=auth)

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        message = websocket.receive_json()
        assert message["t"] == "config"
        assert message["version"] == 1
        assert message["bundle"]["llm"]["model"] == "gpt-4o-mini"
        assert message["bundle"]["wake_word"] == "小绫小绫"
        assert message["bundle"]["wake_command"] == "xiao ling xiao ling"
        websocket.send_json(
            {"v": 1, "t": "config_ack", "session": 0, "version": 1, "ok": True}
        )


def test_wake_word_default_and_normalization():
    default = ProfilePayload.model_validate(BUNDLE)
    assert default.wake_word == "小绫小绫"
    assert wake_word_to_command(default.wake_word) == "xiao ling xiao ling"
    custom = ProfilePayload.model_validate({**BUNDLE, "wake_word": "  小绫起床  "})
    assert custom.wake_word == "小绫起床"
    assert wake_word_to_command(custom.wake_word) == "xiao ling qi chuang"


def test_wake_word_uses_phrase_context_for_polyphonic_characters():
    assert wake_word_to_command("重庆") == "chong qing"


@pytest.mark.parametrize(
    "wake_word", ["", "   ", "绫" * 25, "小绫AI", "xiao ling", "小 绫", "小绫!"]
)
def test_invalid_wake_word_is_rejected(wake_word):
    with pytest.raises(ValidationError):
        ProfilePayload.model_validate({**BUNDLE, "wake_word": wake_word})


def test_non_chinese_wake_word_returns_clear_422(client, tokens):
    claim_device(client, tokens)
    response = client.post(
        "/api/v1/devices/dev-alice/config",
        json={**BUNDLE, "wake_word": "小绫AI"},
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 422
    assert "非中文字符" in response.text


@pytest.mark.parametrize(
    ("converted", "message"),
    [([], "空拼音"), ([""], "空拼音"), (["xiao1"], "非法音节"), (["小"], "非法音节")],
)
def test_empty_or_invalid_pinyin_returns_clear_422(
    client, tokens, monkeypatch, converted, message
):
    claim_device(client, tokens)
    monkeypatch.setattr("app.schemas.lazy_pinyin", lambda *args, **kwargs: converted)
    response = client.post(
        "/api/v1/devices/dev-alice/config",
        json={**BUNDLE, "wake_word": "小"},
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 422
    assert message in response.text


def test_history_pull_roundtrip(client, tokens):
    device_token = claim_device(client, tokens)
    auth = bearer(tokens["alice"]["access_token"])

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        result: dict = {}

        def pull() -> None:
            result["response"] = client.post(
                "/api/v1/devices/dev-alice/history/pull", headers=auth
            )

        worker = threading.Thread(target=pull)
        worker.start()
        request = websocket.receive_json()
        assert request["t"] == "history_request"
        websocket.send_json(
            {
                "v": 1,
                "t": "history",
                "session": 0,
                "items": [
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "在的"},
                    {"role": "system", "content": "应被过滤"},
                ],
            }
        )
        worker.join(timeout=15)
        assert not worker.is_alive()
        response = result["response"]
        assert response.status_code == 200, response.text
        assert response.json()["items"] == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "在的"},
        ]


def test_history_pull_requires_online_device(client, tokens):
    claim_device(client, tokens)
    response = client.post(
        "/api/v1/devices/dev-alice/history/pull",
        headers=bearer(tokens["alice"]["access_token"]),
    )
    assert response.status_code == 409


def test_panel_account_mapping_has_no_usable_password(client):
    from app.api import _upsert_panel_user
    from app.database import SessionLocal
    from app.security import authenticate_user

    with SessionLocal() as db:
        user = _upsert_panel_user(db, "carol")
        assert user.password_hash == "!panel-only"
        # 口令只在面板，本地映射行不能用任何密码登录。
        assert authenticate_user(db, "carol", "anything") is None


def test_dialogue_from_profile_uses_user_settings(client):
    provider = dialogue_from_profile(BUNDLE, client.app.state.settings)
    assert provider is not None
    assert provider.mode == "profile"
    assert provider.llm.base_url == "https://api.example.com/v1"
    assert provider.llm.model == "gpt-4o-mini"
    assert provider.llm.system == "你是测试机器人"
    assert provider.memory_llm is provider.llm
    assert provider.asr.model == "whisper-1"
    # 音色没带 model 前缀时自动补全（与 SiliconFlowTTS 口径一致）
    assert provider.tts.voice == "tts-1:alloy"
    assert provider.tts.speed == 1.0


def test_dialogue_from_profile_rejects_incomplete_bundle(client):
    broken = {
        **BUNDLE,
        "llm": {"base_url": "https://api.example.com/v1", "model": "x", "api_key": ""},
    }
    assert dialogue_from_profile(broken, client.app.state.settings) is None


def test_dialogue_from_profile_falls_back_to_server_persona(client):
    bundle = {**BUNDLE, "persona": ""}
    provider = dialogue_from_profile(bundle, client.app.state.settings)
    assert provider is not None
    assert provider.llm.system == client.app.state.settings.llm_system
