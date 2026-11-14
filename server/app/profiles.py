"""用户配置包的服务器内存暂存。

配置包含服务商密钥，按设计**绝不落盘**：只存在进程内存里，
过期或进程重启即消失。机器人本地另有加密副本，作为独立工作时的兜底。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class StoredProfile:
    device_id: str
    user_id: str
    bundle: dict
    version: int
    updated_at: float
    expires_at: float


class ProfileStore:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, StoredProfile] = {}
        self._versions: dict[str, int] = {}
        self._config_waiters: dict[str, asyncio.Future] = {}
        self._history_waiters: dict[str, asyncio.Future] = {}

    def put(self, device_id: str, user_id: str, bundle: dict) -> StoredProfile:
        version = self._versions.get(device_id, 0) + 1
        self._versions[device_id] = version
        now = time.time()
        profile = StoredProfile(
            device_id=device_id,
            user_id=user_id,
            bundle=bundle,
            version=version,
            updated_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._items[device_id] = profile
        return profile

    def get(self, device_id: str) -> StoredProfile | None:
        profile = self._items.get(device_id)
        if profile is None:
            return None
        if profile.expires_at <= time.time():
            self._items.pop(device_id, None)
            return None
        return profile

    def drop(self, device_id: str) -> None:
        self._items.pop(device_id, None)

    # ---- 下发确认 ----

    def begin_config_wait(self, device_id: str) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        self._config_waiters[device_id] = future
        return future

    def resolve_config(self, device_id: str, version: int, ok: bool, error: str = "") -> None:
        future = self._config_waiters.pop(device_id, None)
        if future is not None and not future.done():
            future.set_result({"version": version, "ok": ok, "error": error})

    def cancel_config_wait(self, device_id: str) -> None:
        future = self._config_waiters.pop(device_id, None)
        if future is not None and not future.done():
            future.cancel()

    # ---- 历史拉取 ----

    def begin_history_wait(self, device_id: str) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        self._history_waiters[device_id] = future
        return future

    def resolve_history(self, device_id: str, items: list[dict]) -> None:
        future = self._history_waiters.pop(device_id, None)
        if future is not None and not future.done():
            future.set_result(items)

    def cancel_history_wait(self, device_id: str) -> None:
        future = self._history_waiters.pop(device_id, None)
        if future is not None and not future.done():
            future.cancel()
