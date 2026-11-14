from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .credentials import KeyringTokenStore
from .settings import DEFAULT_BASE_URL


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SessionExpired(ApiError):
    pass


class ApiClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token_store: Any | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = self._validate_base_url(base_url)
        self.token_store = token_store or KeyringTokenStore(self.base_url)
        self._client = httpx.Client(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
        )
        self._lock = threading.RLock()

    @staticmethod
    def _validate_base_url(value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("服务器地址必须是有效的 http:// 或 https:// 地址")
        return value

    def set_base_url(self, base_url: str) -> None:
        with self._lock:
            self.base_url = self._validate_base_url(base_url)
            self._client.base_url = f"{self.base_url}/"
            self.token_store.set_base_url(self.base_url)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
            detail = body.get("detail", body.get("message")) if isinstance(body, dict) else body
            if detail:
                if not isinstance(detail, str):
                    detail = json.dumps(detail, ensure_ascii=False)
                return f"服务器返回 {response.status_code}：{detail}"
        except (ValueError, TypeError):
            pass
        text = response.text.strip()
        suffix = f"：{text[:300]}" if text else ""
        return f"服务器返回 {response.status_code}{suffix}"

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError("服务器返回了无法解析的 JSON 数据", response.status_code) from exc

    @staticmethod
    def _rewind_files(kwargs: dict[str, Any]) -> None:
        files = kwargs.get("files") or {}
        for value in files.values():
            candidates = value if isinstance(value, tuple) else (value,)
            for candidate in candidates:
                if hasattr(candidate, "seek"):
                    candidate.seek(0)

    def _send(self, method: str, path: str, access_token: str | None, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return self._client.request(method, f"api/v1/{path}", headers=headers, **kwargs)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        with self._lock:
            try:
                response = self._send(method, path, self.token_store.access_token, **kwargs)
                if response.status_code == 401:
                    refresh_token = self.token_store.refresh_token
                    if not refresh_token:
                        self.token_store.clear()
                        raise SessionExpired("登录已过期，请重新登录", 401)
                    refreshed = self._client.post(
                        "api/v1/auth/refresh", json={"refresh_token": refresh_token}
                    )
                    if refreshed.status_code != 200:
                        self.token_store.clear()
                        raise SessionExpired("登录已过期，请重新登录", refreshed.status_code)
                    tokens = self._decode(refreshed)
                    access_token = tokens.get("access_token")
                    new_refresh = tokens.get("refresh_token") or refresh_token
                    if not access_token:
                        self.token_store.clear()
                        raise SessionExpired("刷新登录状态失败，请重新登录", refreshed.status_code)
                    self.token_store.save(access_token, new_refresh)
                    self._rewind_files(kwargs)
                    response = self._send(method, path, access_token, **kwargs)
                    if response.status_code == 401:
                        self.token_store.clear()
                        raise SessionExpired("登录已过期，请重新登录", 401)
                if not response.is_success:
                    raise ApiError(self._error_message(response), response.status_code)
                return self._decode(response)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ApiError(f"无法连接服务器：{exc}") from exc

    def login(self, username: str, password: str) -> dict[str, Any]:
        with self._lock:
            try:
                response = self._client.post(
                    "api/v1/auth/login", json={"username": username, "password": password}
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ApiError(f"无法连接服务器：{exc}") from exc
            if not response.is_success:
                raise ApiError(self._error_message(response), response.status_code)
            data = self._decode(response)
            access_token = data.get("access_token")
            refresh_token = data.get("refresh_token")
            if not access_token or not refresh_token:
                raise ApiError("登录响应缺少访问令牌或刷新令牌")
            self.token_store.save(access_token, refresh_token)
            return data

    def logout(self) -> None:
        self.token_store.clear()

    def list_devices(self) -> list[dict[str, Any]]:
        return (self._request("GET", "devices") or {}).get("items", [])

    def delete_device(self, device_id: str) -> None:
        self._request("DELETE", f"devices/{quote(device_id, safe='')}")

    def create_pairing(self, device_id: str) -> dict[str, Any]:
        return self._request(
            "POST", "pairings", json={"device_id": device_id}
        )

    def list_conversations(self, device_id: str, date: str) -> list[dict[str, Any]]:
        return (
            self._request(
                "GET", "conversations", params={"device_id": device_id, "date": date}
            )
            or {}
        ).get("items", [])

    def list_messages(self, conversation_id: Any) -> list[dict[str, Any]]:
        return (
            self._request(
                "GET", f"conversations/{quote(str(conversation_id), safe='')}/messages"
            )
            or {}
        ).get("items", [])

    def list_firmware(self) -> list[dict[str, Any]]:
        return (self._request("GET", "firmware") or {}).get("items", [])

    def upload_firmware(
        self,
        file_path: str | Path,
        version: str,
        hardware: str = "esp32-s3",
        protocol: str = "1",
        notes: str = "",
    ) -> Any:
        path = Path(file_path)
        with path.open("rb") as stream:
            return self._request(
                "POST",
                "firmware",
                data={
                    "version": version,
                    "hardware": hardware,
                    "protocol": protocol,
                    "notes": notes,
                },
                files={"file": (path.name, stream, "application/octet-stream")},
            )

    def publish_firmware(self, firmware_id: Any) -> Any:
        return self._request("POST", f"firmware/{quote(str(firmware_id), safe='')}/publish")

    def start_ota(self, device_id: str, firmware_id: Any) -> Any:
        return self._request(
            "POST",
            f"devices/{quote(device_id, safe='')}/ota",
            json={"firmware_id": firmware_id},
        )

    def ota_history(self, device_id: str) -> list[dict[str, Any]]:
        return (
            self._request("GET", f"devices/{quote(device_id, safe='')}/ota") or {}
        ).get("items", [])

    def close(self) -> None:
        self._client.close()
