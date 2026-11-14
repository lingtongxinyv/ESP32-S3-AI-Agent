from __future__ import annotations

import asyncio

from app.database import SessionLocal
from app.models import User
from app.providers import (
    AdminCommandGuardLLM,
    DialogueProvider,
    MockASR,
    MockLLM,
    MockTTS,
    XiaolingLLM,
    tts_pcm,
)

from .helpers import claim_device, hello, receive_dialogue, send_pcm, start


def _user_id(username: str) -> int:
    with SessionLocal() as db:
        return db.query(User).filter(User.username == username).one().id


def _run(coro):
    return asyncio.run(coro)


async def _collect(llm, text: str) -> str:
    pieces = []
    async for piece in llm.stream(text, []):
        pieces.append(piece)
    return "".join(pieces)


def test_role_routing_uses_dedicated_threads_and_public_peer_per_account(
    client, monkeypatch
):
    settings = client.app.state.settings
    settings.dialogue_provider = "xiaoling"
    settings.xiaoling_admin_token = "admin-token"
    settings.xiaoling_public_daily_tokens = 12345
    base = DialogueProvider(settings)
    router = client.app.state.dialogue_router
    calls = []

    async def fake_api(method, path, token, payload=None):
        calls.append({"method": method, "path": path, "token": token, "payload": payload})
        if method == "POST" and path == "/api/chat/peers":
            assert token == "admin-token"
            assert payload["kind"] == "peer"
            assert payload["capabilities"] == ["public", "cap:12345"]
            return {"token": f"public-token-{payload['name']}"}
        if method == "POST" and path == "/api/chat/threads":
            return {"thread": {"id": f"thread-{len(calls)}-{payload['title']}"}}
        raise AssertionError((method, path))

    monkeypatch.setattr(router, "_api", fake_api)
    admin = _run(router.resolve(_user_id("admin"), base))
    alice = _run(router.resolve(_user_id("alice"), base))
    bob = _run(router.resolve(_user_id("bob"), base))

    assert admin.llm.inner.token == "admin-token"
    assert admin.llm.inner.thread_id
    assert alice.llm.token == "public-token-esp32-u2"
    assert bob.llm.token == "public-token-esp32-u3"
    assert alice.llm.token != bob.llm.token
    assert len({admin.llm.inner.thread_id, alice.llm.thread_id, bob.llm.thread_id}) == 3
    assert alice.memory_llm.thread_id != alice.llm.thread_id
    assert admin.memory_llm.token != settings.xiaoling_admin_token
    assert admin.memory_llm.thread_id != admin.llm.inner.thread_id

    # 已有映射直接复用，不重复创建 peer 或线程。
    before = len(calls)
    again = _run(router.resolve(_user_id("alice"), base))
    assert again.llm.thread_id == alice.llm.thread_id
    assert len(calls) == before

    admin_thread_calls = [
        call for call in calls
        if call["path"] == "/api/chat/threads" and call["token"] == "admin-token"
    ]
    assert len(admin_thread_calls) == 1
    assert "管理员 admin" in admin_thread_calls[0]["payload"]["title"]
    assert admin_thread_calls[0]["payload"]["kind"] == "esp32-voice"
    public_chat_calls = [
        call for call in calls
        if call["path"] == "/api/chat/threads" and "隔离对话" in call["payload"]["title"]
    ]
    assert len(public_chat_calls) == 2
    assert all(call["payload"]["kind"] == "esp32-voice" for call in public_chat_calls)
    distill_calls = [
        call for call in calls
        if call["path"] == "/api/chat/threads" and "记忆蒸馏" in call["payload"]["title"]
    ]
    assert len(distill_calls) == 3
    assert all(call["payload"]["kind"] == "peer" for call in distill_calls)


def test_non_xiaoling_provider_does_not_create_routes(client):
    base = client.app.state.dialogue
    routed = _run(client.app.state.dialogue_router.resolve(_user_id("alice"), base))
    assert routed is base


def test_admin_task_requires_second_confirmation():
    class RecordingLLM:
        def __init__(self):
            self.calls = []

        async def stream(self, user_text, history, extra_system=None):
            self.calls.append(user_text)
            yield "已下发"

    inner = RecordingLLM()
    llm = AdminCommandGuardLLM(inner, 7, {})
    prompt = _run(_collect(llm, "/task 检查服务器状态"))
    assert "确认执行" in prompt
    assert inner.calls == []
    assert _run(_collect(llm, "确认执行")) == "已下发"
    assert inner.calls == ["/task 检查服务器状态"]
    assert "没有待确认" in _run(_collect(llm, "确认执行"))


def test_xiaoling_command_done_is_returned_without_delta(client, monkeypatch):
    llm = XiaolingLLM(client.app.state.settings, "thread-admin", "secret")

    class FakeResponse:
        status_code = 200

        async def aiter_lines(self):
            yield "event: done"
            yield 'data: {"post": {"text": "任务已创建"}}'

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    assert _run(_collect(llm, "/task 检查服务器状态")) == "任务已创建"


def test_xiaoling_emotion_event_is_validated_and_popped_once(client, monkeypatch):
    llm = XiaolingLLM(client.app.state.settings, "thread-voice", "secret")

    class FakeResponse:
        status_code = 200

        async def aiter_lines(self):
            yield "event: emotion"
            yield 'data: {"emotion": "invalid"}'
            yield "event: emotion"
            yield 'data: {"emotion": "happy"}'
            yield "event: delta"
            yield 'data: {"text": "[[ha"}'
            yield "event: delta"
            yield 'data: {"text": "ppy]]\\n你"}'
            yield "event: delta"
            yield 'data: {"text": "好[[sad]]"}'
            yield "event: emotion"
            yield 'data: {"emotion": "sad"}'
            yield "event: done"
            yield 'data: {"post": {"text": "你好"}}'

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    assert _run(_collect(llm, "你好")) == "\n你好"
    assert llm.pop_emotion() == "happy"
    assert llm.pop_emotion() is None


def test_xiaoling_single_bracket_fragments_are_sanitized_and_infer_emotion(client, monkeypatch):
    llm = XiaolingLLM(client.app.state.settings, "thread-voice", "secret")

    class FakeResponse:
        status_code = 200

        async def aiter_lines(self):
            for text in ["[", "[happy", "]]\\n你", "好[[sad]]", "。"]:
                yield "event: delta"
                yield f'data: {{"text": "{text}"}}'
            yield "event: done"
            yield 'data: {"post": {"text": "[[happy]]\\n你好。"}}'

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    assert _run(_collect(llm, "你好")) == "\n你好。"
    assert llm.pop_emotion() == "happy"
    assert llm.pop_emotion() is None


def test_tts_pcm_strips_markers_at_final_input_boundary(client):
    class FakeTTS:
        def __init__(self):
            self.inputs = []

        async def synth(self, text):
            self.inputs.append(text)
            return b"\x00\x00" * 320

    provider = DialogueProvider(client.app.state.settings)
    provider.tts = FakeTTS()
    pcm = _run(tts_pcm(provider, "[[happy]]\n你好。[[neutral]]"))
    assert provider.tts.inputs == ["你好。"]
    assert len(pcm) == 640


def test_websocket_routes_by_device_owner(client, tokens, monkeypatch):
    device_token = claim_device(client, tokens)
    settings = client.app.state.settings
    settings.dialogue_provider = "xiaoling"
    settings.xiaoling_admin_token = "admin-token"
    client.app.state.dialogue = DialogueProvider(settings)
    routed = DialogueProvider(settings)
    routed.mode = "mock"
    routed.asr = MockASR()
    routed.llm = MockLLM()
    routed.memory_llm = routed.llm
    routed.tts = MockTTS()
    seen = []

    async def fake_resolve(user_id, base):
        seen.append(user_id)
        return routed

    monkeypatch.setattr(client.app.state.dialogue_router, "resolve", fake_resolve)
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        start(websocket, 88)
        send_pcm(websocket, 88, b"\x00\x00" * 320)
        events, audio = receive_dialogue(websocket, 88)
    assert seen == [_user_id("alice")]
    assert events[-1]["t"] == "done"
    assert audio


def test_admin_profile_does_not_override_xiaoling_route(client, tokens, monkeypatch):
    device_token = claim_device(client, tokens, device_id="dev-admin", user="admin")
    settings = client.app.state.settings
    settings.dialogue_provider = "xiaoling"
    settings.xiaoling_admin_token = "admin-token"
    client.app.state.dialogue = DialogueProvider(settings)
    routed = DialogueProvider(settings)
    routed.mode = "mock"
    routed.asr = MockASR()
    routed.llm = MockLLM()
    routed.memory_llm = routed.llm
    routed.tts = MockTTS()
    seen = []

    async def fake_resolve(user_id, base):
        seen.append(user_id)
        return routed

    monkeypatch.setattr(client.app.state.dialogue_router, "resolve", fake_resolve)
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket, device_id="dev-admin")
        client.app.state.profiles.put(
            "dev-admin",
            _user_id("admin"),
            {
                "persona": "你是普通机器人",
                "llm": {"base_url": "https://api.example.com/v1", "model": "gpt", "api_key": "sk-test"},
                "asr": {"base_url": "https://api.example.com/v1", "model": "asr", "api_key": "sk-test"},
                "tts": {"base_url": "https://api.example.com/v1", "model": "tts", "api_key": "sk-test", "voice": "alloy"},
            },
        )
        start(websocket, 89)
        send_pcm(websocket, 89, b"\x00\x00" * 320)
        events, audio = receive_dialogue(websocket, 89)
    assert seen == [_user_id("admin")]
    assert events[-1]["t"] == "done"
    assert audio
