from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from datetime import datetime, timezone

from app.protocol import HEADER, pack_frame
from app.providers import DialogueProvider
from app.websocket import DeviceConnection
from .helpers import bearer, claim_device, hello, receive_dialogue, send_pcm, start


class EmotionLLM:
    def __init__(self):
        self.emotion = "happy"

    def pop_emotion(self):
        emotion = self.emotion
        self.emotion = None
        return emotion

    async def stream(self, user_text, history, extra_system=None):
        yield "第一句。"
        yield "第二句。"


def test_echo_bytes_header_auth_and_online_state(client, tokens):
    device_token = claim_device(client, tokens)
    pcm = bytes((index * 37) & 0xFF for index in range(640 * 3 + 122))
    with client.websocket_connect(
        "/ws/v1/device", headers=bearer(device_token)
    ) as websocket:
        hello(websocket)
        online = client.get(
            "/api/v1/devices", headers=bearer(tokens["alice"]["access_token"])
        ).json()["items"][0]
        assert online["online"] is True
        start(websocket, 77)
        send_pcm(websocket, 77, pcm, chunk_size=510)
        events, echoed = receive_dialogue(websocket, 77)
        assert echoed == pcm + bytes((-len(pcm)) % 640)
        assert [event["t"] for event in events] == ["audio_start", "audio_end", "done"]
        assert events[0]["sample_rate"] == 16000
        assert events[0]["format"] == "pcm_s16le"
    offline = client.get(
        "/api/v1/devices", headers=bearer(tokens["alice"]["access_token"])
    ).json()["items"][0]
    assert offline["online"] is False


def test_protocol_validation_query_auth_and_no_token_logging(client, tokens, caplog):
    device_token = claim_device(client, tokens)
    caplog.set_level(logging.INFO)
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        websocket.send_json({"v": 1, "t": "start", "session": 9, "sample_rate": 8000})
        assert websocket.receive_json()["code"] == "invalid_sample_rate"
        start(websocket, 9)

        bad_version = bytearray(pack_frame(1, 9, 0, b"\x00\x00"))
        bad_version[0] = 2
        websocket.send_bytes(bytes(bad_version))
        assert websocket.receive_json()["code"] == "invalid_version"

        bad_length = bytearray(pack_frame(1, 9, 0, b"\x00\x00"))
        bad_length[12:16] = struct.pack("<I", 4)
        websocket.send_bytes(bytes(bad_length))
        assert websocket.receive_json()["code"] == "length_mismatch"

        websocket.send_bytes(pack_frame(1, 10, 0, b"\x00\x00"))
        assert websocket.receive_json()["code"] == "session_mismatch"

        websocket.send_json({"v": 1, "t": "abort", "session": 9})
        websocket.send_bytes(pack_frame(1, 9, 1, b"old-session-audio"))
        websocket.send_json({"v": 1, "t": "ping", "session": 9})
        assert websocket.receive_json()["t"] == "pong"
    assert device_token not in caplog.text

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        websocket.send_json({"v": 1, "t": "start", "session": 1, "sample_rate": 16000})
        assert websocket.receive_json()["code"] == "hello_required"


def test_abort_cancels_processing_and_drops_late_frames(client, tokens):
    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    provider = DialogueProvider(client.app.state.settings)

    class SlowASR:
        async def transcribe(self, pcm: bytes, rate: int) -> str:
            await asyncio.sleep(0.08)
            return "不应继续"

    provider.asr = SlowASR()
    client.app.state.dialogue = provider
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        start(websocket, 123)
        websocket.send_bytes(pack_frame(1, 123, 0, b"\x00\x00" * 320))
        websocket.send_json({"v": 1, "t": "end", "session": 123})
        websocket.send_json({"v": 1, "t": "abort", "session": 123})
        websocket.send_bytes(pack_frame(1, 123, 1, b"\x01\x01" * 320))
        websocket.send_json({"v": 1, "t": "ping", "session": 123})
        event = websocket.receive_json()
        assert event["t"] == "pong", event
        time.sleep(0.12)
        websocket.send_json({"v": 1, "t": "ping", "session": 123})
        assert websocket.receive_json()["t"] == "pong"


def test_mock_dialogue_history_and_user_isolation(client, tokens):
    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    client.app.state.dialogue = DialogueProvider(client.app.state.settings)
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        start(websocket, 42)
        send_pcm(websocket, 42, b"\x00\x00" * 320)
        events, audio = receive_dialogue(websocket, 42)
    assert audio
    assert [event["t"] for event in events].count("llm_delta") == 2
    asr = next(event for event in events if event["t"] == "asr_result")
    assert asr["text"] == "你好，介绍一下你自己"
    assert events[-1]["text"] == "你好，我是 ESP32-S3 AI Agent。"

    access = tokens["alice"]["access_token"]
    today = datetime.now(timezone.utc).date().isoformat()
    response = client.get(
        f"/api/v1/conversations?device_id=dev-alice&date={today}", headers=bearer(access)
    )
    assert response.status_code == 200
    conversations = response.json()["items"]
    assert len(conversations) == 1
    assert conversations[0]["session"] == 42
    assert conversations[0]["ended_at"] is not None
    messages = client.get(
        f"/api/v1/conversations/{conversations[0]['id']}/messages", headers=bearer(access)
    ).json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "你好，我是 ESP32-S3 AI Agent。"
    assert client.get(
        "/api/v1/conversations?device_id=dev-alice&date=2000-01-01", headers=bearer(access)
    ).json() == {"items": []}

    bob_access = tokens["bob"]["access_token"]
    assert client.get(
        "/api/v1/conversations?device_id=dev-alice", headers=bearer(bob_access)
    ).status_code == 404
    assert client.get(
        f"/api/v1/conversations/{conversations[0]['id']}/messages", headers=bearer(bob_access)
    ).status_code == 404


def test_empty_asr_returns_retry_prompt_without_calling_llm(client, tokens):
    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    provider = DialogueProvider(client.app.state.settings)

    class EmptyASR:
        async def transcribe(self, pcm: bytes, rate: int) -> str:
            return "  \n"

    class UnexpectedLLM:
        async def stream(self, user_text, history, extra_system=None):
            raise AssertionError("empty ASR must not reach LLM")
            yield ""

    provider.asr = EmptyASR()
    provider.llm = UnexpectedLLM()
    client.app.state.dialogue = provider
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        start(websocket, 43)
        send_pcm(websocket, 43, b"\x00\x00" * 320)
        events, audio = receive_dialogue(websocket, 43)

    assert audio
    assert next(event for event in events if event["t"] == "asr_result")["text"] == ""
    assert events[-1]["t"] == "done"
    assert events[-1]["text"] == "没有听清，请再说一次。"
    assert not any(event["t"] == "error" for event in events)


def test_two_sentence_tts_is_prefetched_concurrently_in_order(client, tokens):
    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    provider = DialogueProvider(client.app.state.settings)

    class TwoSentenceLLM:
        async def stream(self, user_text, history, extra_system=None):
            yield "第一句。第二句。"

    class DelayedTTS:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.inputs = []

        async def synth(self, text, speed=None):
            self.inputs.append(text)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.03)
            self.active -= 1
            return b"\x00\x00" * 320

    provider.llm = TwoSentenceLLM()
    provider.memory_llm = provider.llm
    provider.tts = DelayedTTS()
    client.app.state.dialogue = provider
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        start(websocket, 43)
        send_pcm(websocket, 43, b"\x00\x00" * 320)
        events, audio = receive_dialogue(websocket, 43)
    assert events[-1]["text"] == "第一句。第二句。"
    assert provider.tts.max_active == 2
    assert provider.tts.inputs == ["第一句。", "第二句。"]
    assert audio


def test_auto_emotion_precedes_text_and_does_not_touch_manual_panel(client, tokens):
    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    provider = DialogueProvider(client.app.state.settings)
    provider.llm = EmotionLLM()
    provider.memory_llm = provider.llm
    client.app.state.dialogue = provider

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket, panel_auto=True)
        start(websocket, 44)
        send_pcm(websocket, 44, b"\x00\x00" * 320)
        events, audio = receive_dialogue(websocket, 44)

    panels = [event for event in events if event["t"] == "panel"]
    assert len(panels) == 1
    assert panels[0]["session"] == 44
    assert panels[0]["emotion"] == "happy"
    assert panels[0]["auto"] is True
    assert panels[0]["ack"] is False
    assert panels[0]["ttl_ms"] == 90000
    assert events.index(panels[0]) < next(
        index for index, event in enumerate(events) if event["t"] == "llm_delta"
    )
    assert events[-1]["text"] == "第一句。第二句。"
    assert audio
    state = client.get(
        "/api/v1/devices/dev-alice/panel", headers=bearer(tokens["alice"]["access_token"])
    ).json()
    assert state["active"] is False


def test_auto_emotion_after_final_delta_is_sent(client, tokens):
    class LateEmotionLLM(EmotionLLM):
        def __init__(self):
            super().__init__()
            self.emotion = None

        async def stream(self, user_text, history, extra_system=None):
            yield "你好。"
            self.emotion = "neutral"

    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    provider = DialogueProvider(client.app.state.settings)
    provider.llm = LateEmotionLLM()
    provider.memory_llm = provider.llm
    client.app.state.dialogue = provider

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket, panel_auto=True)
        start(websocket, 47)
        send_pcm(websocket, 47, b"\x00\x00" * 320)
        events, _ = receive_dialogue(websocket, 47)

    panels = [event for event in events if event["t"] == "panel"]
    assert len(panels) == 1
    assert panels[0]["emotion"] == "neutral"
    assert panels[0]["auto"] is True


def test_auto_emotion_requires_firmware_capability(client, tokens):
    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    provider = DialogueProvider(client.app.state.settings)
    provider.llm = EmotionLLM()
    provider.memory_llm = provider.llm
    client.app.state.dialogue = provider

    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        start(websocket, 45)
        send_pcm(websocket, 45, b"\x00\x00" * 320)
        events, _ = receive_dialogue(websocket, 45)
    assert not any(event["t"] == "panel" for event in events)
    assert events[-1]["t"] == "done"


def test_auto_emotion_send_failure_does_not_interrupt_dialogue(client, tokens, monkeypatch):
    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    provider = DialogueProvider(client.app.state.settings)
    provider.llm = EmotionLLM()
    provider.memory_llm = provider.llm
    client.app.state.dialogue = provider
    original = DeviceConnection.send_json

    async def fail_auto_panel(self, session, event_type, state=None, **payload):
        if event_type == "panel" and payload.get("auto"):
            raise RuntimeError("simulated panel send failure")
        return await original(self, session, event_type, state, **payload)

    monkeypatch.setattr(DeviceConnection, "send_json", fail_auto_panel)
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket, panel_auto=True)
        start(websocket, 46)
        send_pcm(websocket, 46, b"\x00\x00" * 320)
        events, audio = receive_dialogue(websocket, 46)
    assert events[-1]["t"] == "done"
    assert events[-1]["text"] == "第一句。第二句。"
    assert audio


def test_start_history_and_reused_session_are_supported(client, tokens):
    device_token = claim_device(client, tokens)
    client.app.state.settings.dialogue_provider = "mock"
    provider = DialogueProvider(client.app.state.settings)
    seen: dict = {}

    class RecordingLLM:
        async def stream(self, user_text, history, extra_system=None):
            if history:
                seen["history"] = list(history)
            yield "收到。"

    provider.llm = RecordingLLM()
    client.app.state.dialogue = provider

    carried = [
        {"role": "user", "content": "上一句"},
        {"role": "assistant", "content": "上一答"},
    ]
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        websocket.send_json(
            {"v": 1, "t": "start", "session": 5, "sample_rate": 16000, "history": "bad"}
        )
        assert websocket.receive_json()["code"] == "invalid_history"
        # 设备重启后 session 会从头计数，同一 session 重复出现不能导致整轮失败。
        for _ in range(2):
            websocket.send_json(
                {
                    "v": 1,
                    "t": "start",
                    "session": 5,
                    "sample_rate": 16000,
                    "history": carried,
                }
            )
            send_pcm(websocket, 5, b"\x00\x00" * 320)
            events, audio = receive_dialogue(websocket, 5)
            assert events[-1]["t"] == "done"
            assert audio
    assert seen["history"] == carried

    items = client.get(
        "/api/v1/conversations?device_id=dev-alice",
        headers=bearer(tokens["alice"]["access_token"]),
    ).json()["items"]
    assert [item["session"] for item in items] == [5, 5]


def test_100_echo_rounds_have_no_session_crosstalk(client, tokens):
    device_token = claim_device(client, tokens)
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        for round_number in range(100):
            session = round_number + 1
            samples = (
                round_number,
                -round_number,
                round_number * 2,
                32767 - round_number,
                -32768 + round_number,
                round_number ^ 0x5555,
                round_number * 17,
                round_number * 31,
            )
            pcm = struct.pack("<8h", *samples) * 40
            start(websocket, session)
            send_pcm(websocket, session, pcm)
            events, echoed = receive_dialogue(websocket, session)
            assert echoed == pcm, f"round {session} audio mismatch"
            assert events[-1]["session"] == session
            assert all(
                event["session"] == session for event in events
            ), f"round {session} control crosstalk"
