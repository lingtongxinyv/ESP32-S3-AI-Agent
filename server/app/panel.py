"""设备屏幕与音量的下发状态缓存。

屏幕按"表情 + 一行文字"的抽象描述下发，具体怎么画由固件决定：
这样换 OLED 型号时只需要改固件的渲染层，协议与手机端都不用动。
状态只存在进程内存，设备侧另有 NVS 副本。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

EMOTIONS = ("neutral", "happy", "sad", "angry", "surprised", "thinking", "sleepy", "love")
MAX_TEXT_CHARS = 21


@dataclass
class PanelState:
    volume: int = 100
    emotion: str = "neutral"
    text: str = ""
    active: bool = False


class PanelStore:
    def __init__(self) -> None:
        self._states: dict[str, PanelState] = {}
        self._waiters: dict[str, asyncio.Future] = {}

    def get(self, device_id: str) -> PanelState:
        return self._states.get(device_id, PanelState())

    def update(
        self,
        device_id: str,
        volume: int | None = None,
        emotion: str | None = None,
        text: str | None = None,
        active: bool | None = None,
    ) -> PanelState:
        state = self.get(device_id)
        if volume is not None:
            state.volume = max(0, min(100, int(volume)))
        if emotion is not None:
            state.emotion = emotion
        if text is not None:
            state.text = text[:MAX_TEXT_CHARS]
        if active is not None:
            state.active = active
        self._states[device_id] = state
        return state

    def remember_volume(self, device_id: str, volume: int) -> None:
        self.update(device_id, volume=volume)

    # ---- 下发确认 ----

    def begin_wait(self, device_id: str) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        self._waiters[device_id] = future
        return future

    def resolve(self, device_id: str, ok: bool, volume: int | None, error: str = "") -> None:
        future = self._waiters.pop(device_id, None)
        if future is not None and not future.done():
            future.set_result({"ok": ok, "volume": volume, "error": error})

    def cancel_wait(self, device_id: str) -> None:
        future = self._waiters.pop(device_id, None)
        if future is not None and not future.done():
            future.cancel()
