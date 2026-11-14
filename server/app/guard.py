"""成本围栏：防 bug 失控循环与上下文过长的额度消耗（参考 dsh-suite 小綾成本护栏）。

四道闸（进程内存态，阈值 ≤0 表示关闭该道闸）：
g1 频率熔断  每分钟模型对话轮数上限 —— 治失控循环、设备端重试风暴
g2 小时预算  每小时累计 prompt+reply 字符（≈token）上限 —— 治上下文滚雪球
g3 失败熔断  连续失败 N 次后冷却一段时间 —— 治坏配置反复烧 ASR/LLM/TTS
g4 旁路预算  蒸馏等附加调用在预算吃紧时直接跳过

告警参考小綾模式：同一 key 每分钟最多打一次日志，避免告警本身刷屏。
"""

from __future__ import annotations

import logging
import time
from collections import deque

log = logging.getLogger(__name__)

# 上下文围栏：REST 与 WS 共用的历史上限口径。
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 4000


class BudgetExceeded(RuntimeError):
    """触发成本围栏：本轮对话被拒绝，不会产生任何模型调用。"""


class CostGuard:
    def __init__(
        self,
        max_turns_per_min: int = 12,
        max_chars_per_hour: int = 400_000,
        failure_threshold: int = 5,
        cooldown_seconds: int = 60,
    ) -> None:
        self.max_turns = max_turns_per_min
        self.max_chars = max_chars_per_hour
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._turns: dict[str, deque[float]] = {}
        self._chars: dict[str, deque[tuple[float, int]]] = {}
        self._failures: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        self._alerted_at: dict[str, float] = {}

    # ---- 内部工具 ----

    def _prune_chars(self, key: str, now: float) -> int:
        window = self._chars.setdefault(key, deque())
        while window and now - window[0][0] >= 3600:
            window.popleft()
        return sum(size for _, size in window)

    def _alert(self, key: str, reason: str) -> None:
        now = time.time()
        if now - self._alerted_at.get(key, 0.0) < 60:
            return
        self._alerted_at[key] = now
        log.warning("成本围栏触发（%s）：%s", key, reason)

    # ---- 主对话闸门（g1/g2/g3） ----

    def check(self, key: str) -> None:
        """放行返回 None；拦截抛 BudgetExceeded，调用方必须拒绝本轮、不发起任何调用。"""
        now = time.time()
        if self.cooldown_seconds > 0:
            until = self._cooldown_until.get(key, 0.0)
            if until > now:
                reason = f"失败熔断冷却中，剩余 {int(until - now) + 1} 秒"
                self._alert(key, reason)
                raise BudgetExceeded(f"失败熔断：连续失败后进入冷却（{reason}），本轮已拦截")
        if self.max_turns > 0:
            hits = self._turns.setdefault(key, deque())
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.max_turns:
                reason = f"60 秒内已发起 {len(hits)} 轮对话（上限 {self.max_turns}）"
                self._alert(key, reason)
                raise BudgetExceeded(f"频率围栏：{reason}，本轮已拦截")
            # 按尝试计：失败轮也占频率名额，才能拦住重试风暴。
            hits.append(now)
        if self.max_chars > 0:
            used = self._prune_chars(key, now)
            if used >= self.max_chars:
                reason = f"本小时已累计 {used} 字符（上限 {self.max_chars}）"
                self._alert(key, reason)
                raise BudgetExceeded(f"预算围栏：{reason}，本轮已拦截")

    def record_success(self, key: str, chars: int) -> None:
        """记一次成功对话的实际字符消耗（prompt+reply 近似），并清零失败连击。"""
        self._failures[key] = 0
        if self.max_chars <= 0:
            return
        self._chars.setdefault(key, deque()).append((time.time(), max(0, int(chars))))

    def record_failure(self, key: str) -> None:
        """记一次失败；连续失败达到阈值进入冷却（治坏配置反复烧钱）。"""
        if self.failure_threshold <= 0:
            return
        streak = self._failures.get(key, 0) + 1
        self._failures[key] = streak
        if streak >= self.failure_threshold and self.cooldown_seconds > 0:
            self._cooldown_until[key] = time.time() + self.cooldown_seconds
            self._failures[key] = 0
            self._alert(key, f"连续失败 {streak} 次，进入 {self.cooldown_seconds} 秒冷却")

    # ---- 旁路调用（g4：蒸馏等附加 LLM 消耗） ----

    def allow_side_call(self, key: str, reserved_chars: int) -> bool:
        """预算剩余不足以覆盖旁路调用时返回 False（跳过而非拦截对话）。"""
        if self.max_chars <= 0:
            return True
        return self._prune_chars(key, time.time()) + max(0, reserved_chars) <= self.max_chars

    def record_side_call(self, key: str, chars: int) -> None:
        if self.max_chars <= 0:
            return
        self._chars.setdefault(key, deque()).append((time.time(), max(0, int(chars))))


def trim_history(
    items: list[dict[str, str]], max_messages: int, max_chars: int
) -> list[dict[str, str]]:
    """上下文围栏：超出条数或字符预算时，从最旧的一侧丢弃（保留最新上下文）。"""
    trimmed = items[-max_messages:] if max_messages > 0 else list(items)
    total = sum(len(item["content"]) for item in trimmed)
    index = 0
    while total > max_chars and index < len(trimmed):
        total -= len(trimmed[index]["content"])
        index += 1
    return trimmed[index:]
