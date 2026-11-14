from __future__ import annotations

import pytest

from app.guard import (
    MAX_HISTORY_CHARS,
    BudgetExceeded,
    CostGuard,
    trim_history,
)
from .helpers import bearer, claim_device, hello, receive_dialogue, send_pcm, start


KEY = "user:1"


# ---------------- CostGuard 单元 ----------------

def test_turn_rate_limit_blocks_runaway_loop():
    guard = CostGuard(max_turns_per_min=3, max_chars_per_hour=0)
    for _ in range(3):
        guard.check(KEY)
    with pytest.raises(BudgetExceeded, match="频率围栏"):
        guard.check(KEY)


def test_hourly_char_budget_blocks_overspend():
    guard = CostGuard(max_turns_per_min=0, max_chars_per_hour=1000)
    guard.check(KEY)
    guard.record_success(KEY, 900)
    guard.check(KEY)
    guard.record_success(KEY, 200)
    with pytest.raises(BudgetExceeded, match="预算围栏"):
        guard.check(KEY)


def test_failure_streak_triggers_cooldown():
    guard = CostGuard(max_turns_per_min=0, max_chars_per_hour=0, failure_threshold=3, cooldown_seconds=60)
    guard.record_failure(KEY)
    guard.record_failure(KEY)
    guard.check(KEY)  # 未达阈值，放行
    guard.record_failure(KEY)
    with pytest.raises(BudgetExceeded, match="失败熔断"):
        guard.check(KEY)


def test_success_resets_failure_streak():
    guard = CostGuard(max_turns_per_min=0, max_chars_per_hour=0, failure_threshold=3, cooldown_seconds=60)
    guard.record_failure(KEY)
    guard.record_failure(KEY)
    guard.record_success(KEY, 10)
    guard.record_failure(KEY)
    guard.record_failure(KEY)
    guard.check(KEY)  # 成功清零连击，不应冷却


def test_side_call_skipped_when_budget_tight():
    guard = CostGuard(max_turns_per_min=0, max_chars_per_hour=1000)
    guard.record_success(KEY, 900)
    assert guard.allow_side_call(KEY, 200) is False
    assert guard.allow_side_call(KEY, 100) is True
    guard.record_side_call(KEY, 100)
    assert guard.allow_side_call(KEY, 100) is False


def test_zero_threshold_disables_fence():
    guard = CostGuard(max_turns_per_min=0, max_chars_per_hour=0, failure_threshold=0)
    guard.record_failure(KEY)
    for _ in range(50):
        guard.check(KEY)


# ---------------- 上下文围栏 ----------------

def test_trim_history_keeps_newest_side():
    items = [
        {"role": "user", "content": "旧" * 600},
        {"role": "assistant", "content": "答" * 600},
        {"role": "user", "content": "新" * 600},
        {"role": "assistant", "content": "新答" * 600},
    ]
    trimmed = trim_history(items, 4, 2000)
    assert len(trimmed) == 2
    assert trimmed[0]["content"].startswith("新")
    assert trimmed[-1]["content"].startswith("新答")


def test_trim_history_limits_message_count():
    items = [{"role": "user", "content": str(index)} for index in range(30)]
    trimmed = trim_history(items, 12, MAX_HISTORY_CHARS)
    assert len(trimmed) == 12
    assert trimmed[-1]["content"] == "29"


# ---------------- 人格锚 ----------------

def test_persona_tick_and_message_composition():
    from app.providers import _compose_messages, persona_tick

    system = "你是小綾，运维站娘。"
    messages = _compose_messages(
        system,
        [{"role": "user", "content": "你好"}],
        "今天天气如何",
        "【长期记忆】\n- 主人喜欢晴天",
    )
    assert messages[0] == {"role": "system", "content": system}
    assert messages[1] == {"role": "user", "content": "你好"}
    # 锚 + 记忆段合成最后一条 system，紧贴本轮 user 消息
    assert messages[-2]["role"] == "system"
    assert messages[-2]["content"].startswith("【人格锚·每轮】")
    assert "主人喜欢晴天" in messages[-2]["content"]
    assert messages[-1] == {"role": "user", "content": "今天天气如何"}
    tick = persona_tick(system)
    assert "小綾" in tick and len(tick) < 120


def test_compose_messages_without_extra_keeps_persona_only():
    from app.providers import _compose_messages

    messages = _compose_messages("你是助手。", [], "嗨", None)
    assert [m["role"] for m in messages] == ["system", "user"]


# ---------------- 集成：REST ----------------

def test_chat_text_blocked_by_turn_fence(client, tokens):
    from app.guard import CostGuard

    client.app.state.dialogue.mode = "cloud"  # mock 组件 + cloud 模式 = 进围栏
    client.app.state.guard = CostGuard(max_turns_per_min=1, max_chars_per_hour=0)
    headers = bearer(tokens["alice"]["access_token"])
    first = client.post("/api/v1/chat/text", json={"text": "你好"}, headers=headers)
    assert first.status_code == 200
    second = client.post("/api/v1/chat/text", json={"text": "你好"}, headers=headers)
    assert second.status_code == 429
    assert "频率围栏" in second.json()["detail"]


def test_chat_text_echo_mode_unlimited(client, tokens):
    from app.guard import CostGuard

    client.app.state.guard = CostGuard(max_turns_per_min=1, max_chars_per_hour=0)
    headers = bearer(tokens["alice"]["access_token"])
    for _ in range(5):
        assert client.post("/api/v1/chat/text", json={"text": "你好"}, headers=headers).status_code == 200


# ---------------- 集成：WS 语音 ----------------

def _mock_dialogue(client):
    from app.providers import DialogueProvider

    client.app.state.settings.dialogue_provider = "mock"
    client.app.state.dialogue = DialogueProvider(client.app.state.settings)


def test_ws_dialogue_blocked_by_budget_fence(client, tokens):
    from app.guard import CostGuard

    _mock_dialogue(client)
    client.app.state.dialogue.mode = "cloud"
    client.app.state.guard = CostGuard(max_turns_per_min=1, max_chars_per_hour=0)
    device_token = claim_device(client, tokens)
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        start(websocket, 1)
        send_pcm(websocket, 1, b"\x00\x00" * 320)
        events, _ = receive_dialogue(websocket, 1)
        assert events[-1]["t"] == "done"
        # 第二轮：频率围栏拦截，不产生任何模型调用
        start(websocket, 2)
        send_pcm(websocket, 2, b"\x00\x00" * 320)
        event = websocket.receive_json()
        assert event["t"] == "error"
        assert event["code"] == "budget_exceeded"
        assert "频率围栏" in event["message"]


def test_ws_start_history_is_trimmed_by_fence(client, tokens):
    from app.providers import DialogueProvider

    _mock_dialogue(client)
    provider = DialogueProvider(client.app.state.settings)
    seen: dict = {}

    class RecordingLLM:
        async def stream(self, user_text, history, extra_system=None):
            if history:
                seen["history"] = list(history)
            yield "收到。"

    provider.llm = RecordingLLM()
    client.app.state.dialogue = provider

    device_token = claim_device(client, tokens)
    # 30 条 × 500 字符历史：围栏应裁到 12 条以内且总字符 ≤4000，保留最新
    carried = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"m{index}" * 100}
        for index in range(30)
    ]
    with client.websocket_connect(f"/ws/v1/device?token={device_token}") as websocket:
        hello(websocket)
        websocket.send_json(
            {
                "v": 1,
                "t": "start",
                "session": 7,
                "sample_rate": 16000,
                "history": carried,
            }
        )
        send_pcm(websocket, 7, b"\x00\x00" * 320)
        events, _ = receive_dialogue(websocket, 7)
        assert events[-1]["t"] == "done"
    history = seen["history"]
    assert len(history) <= 12
    assert sum(len(item["content"]) for item in history) <= MAX_HISTORY_CHARS
    assert history[-1]["content"].startswith("m29")
