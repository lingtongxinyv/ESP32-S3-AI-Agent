from __future__ import annotations

import asyncio

import httpx

from .config import Settings
from .database import SessionLocal
from .models import DialogueRoute, User
from .providers import AdminCommandGuardLLM, DialogueProvider, XiaolingLLM


class XiaolingAPIError(RuntimeError):
    def __init__(self, method: str, path: str, status_code: int):
        super().__init__(f"小綾路由 API {method} {path} HTTP {status_code}")
        self.status_code = status_code


class DialogueRouter:
    """按数据库账号角色绑定小綾线程；客户端不能指定线程或凭据。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._locks: dict[int, asyncio.Lock] = {}
        self._pending_tasks: dict[int, tuple[float, str]] = {}

    def _lock(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    async def _api(
        self,
        method: str,
        path: str,
        token: str,
        payload: dict | None = None,
    ) -> dict:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
            response = await client.request(
                method,
                f"{self.settings.xiaoling_base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        if response.status_code >= 400:
            raise XiaolingAPIError(method, path, response.status_code)
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("小綾路由 API 返回格式无效")
        return data

    async def _create_public_peer(self, name: str) -> str:
        payload = {
            "name": name,
            "kind": "peer",
            "note": "ESP32-S3 按账号隔离的 public 对话身份",
            "capabilities": [
                "public",
                f"cap:{self.settings.xiaoling_public_daily_tokens}",
            ],
        }
        try:
            data = await self._api("POST", "/api/chat/peers", self.settings.xiaoling_admin_token, payload)
        except XiaolingAPIError as exc:
            if exc.status_code != 409:
                raise
            # 8781 数据库重建后面板 peer 可能仍在；只恢复同名 peer，不触碰其他身份。
            peers = await self._api("GET", "/api/chat/peers", self.settings.xiaoling_admin_token)
            peer_id = next(
                (item.get("id") for item in peers.get("peers", []) if item.get("name") == name),
                None,
            )
            if not peer_id:
                raise
            await self._api("DELETE", f"/api/chat/peers/{peer_id}", self.settings.xiaoling_admin_token)
            data = await self._api("POST", "/api/chat/peers", self.settings.xiaoling_admin_token, payload)
        token = str(data.get("token") or "")
        if not token:
            raise RuntimeError("小綾未返回 public peer 凭据")
        return token

    async def _create_thread(
        self, token: str, title: str, participants: list[str], kind: str = "peer"
    ) -> str:
        data = await self._api(
            "POST",
            "/api/chat/threads",
            token,
            {"title": title, "participants": participants, "kind": kind},
        )
        thread_id = str((data.get("thread") or {}).get("id") or "")
        if not thread_id:
            raise RuntimeError("小綾未返回对话线程 ID")
        return thread_id

    @staticmethod
    def _route_complete(route: DialogueRoute | None, is_admin: bool) -> bool:
        if route is None or not route.public_peer_token or not route.distill_thread_id:
            return False
        return bool(route.admin_thread_id if is_admin else route.public_thread_id)

    async def _ensure_route(self, user_id: int) -> tuple[DialogueRoute, bool]:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is None:
                raise RuntimeError("对话账号不存在")
            route = db.get(DialogueRoute, user_id)
            if self._route_complete(route, user.is_admin):
                return route, user.is_admin

        async with self._lock(user_id):
            with SessionLocal() as db:
                user = db.get(User, user_id)
                if user is None:
                    raise RuntimeError("对话账号不存在")
                route = db.get(DialogueRoute, user_id)
                if self._route_complete(route, user.is_admin):
                    return route, user.is_admin
                if route is None:
                    route = DialogueRoute(
                        user_id=user_id,
                        public_peer_name=f"esp32-u{user_id}",
                        public_peer_token="",
                    )
                    db.add(route)
                    db.commit()

                if not self.settings.xiaoling_admin_token:
                    raise RuntimeError("未配置 XIAOLING_ADMIN_TOKEN")
                if not route.public_peer_token:
                    route.public_peer_token = await self._create_public_peer(route.public_peer_name)
                    db.commit()

                if user.is_admin and not route.admin_thread_id:
                    route.admin_thread_id = await self._create_thread(
                        self.settings.xiaoling_admin_token,
                        f"ESP32 管理员 {user.username} · 本体对话",
                        ["主人", "小綾"],
                        kind="esp32-voice",
                    )
                    db.commit()
                if not user.is_admin and not route.public_thread_id:
                    route.public_thread_id = await self._create_thread(
                        route.public_peer_token,
                        f"ESP32 用户 {user.username} · 隔离对话",
                        [route.public_peer_name, "小綾"],
                        kind="esp32-voice",
                    )
                    db.commit()
                if not route.distill_thread_id:
                    route.distill_thread_id = await self._create_thread(
                        route.public_peer_token,
                        f"ESP32 记忆蒸馏 · u{user_id}",
                        [route.public_peer_name, "小綾"],
                    )
                    db.commit()
                return route, user.is_admin

    async def resolve(self, user_id: int, base: DialogueProvider) -> DialogueProvider:
        if base.mode != "xiaoling":
            return base
        route, is_admin = await self._ensure_route(user_id)
        if is_admin:
            chat_llm = XiaolingLLM(
                self.settings, route.admin_thread_id or "", self.settings.xiaoling_admin_token
            )
            chat_llm = AdminCommandGuardLLM(chat_llm, user_id, self._pending_tasks)
        else:
            chat_llm = XiaolingLLM(
                self.settings, route.public_thread_id or "", route.public_peer_token
            )
        memory_llm = XiaolingLLM(
            self.settings, route.distill_thread_id or "", route.public_peer_token
        )
        return base.routed(chat_llm, memory_llm)
