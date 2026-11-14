from __future__ import annotations

import json

import httpx
import pytest

from app.api import ApiClient, SessionExpired

from conftest import MemoryTokenStore

BASE_URL = "https://unit.test/agent"


def test_login_uses_agent_prefix_and_saves_tokens() -> None:
    store = MemoryTokenStore()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/api/v1/auth/login"
        assert json.loads(request.content) == {"username": "alice", "password": "secret"}
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "user": {"id": 1, "username": "alice", "is_admin": True},
            },
        )

    client = ApiClient(BASE_URL, store, httpx.MockTransport(handler))
    result = client.login("alice", "secret")
    assert result["user"]["username"] == "alice"
    assert store.access_token == "access"
    assert store.refresh_token == "refresh"
    client.close()


def test_401_refreshes_once_and_retries_request_with_new_token() -> None:
    store = MemoryTokenStore("old-access", "old-refresh")
    device_calls: list[str | None] = []
    refresh_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_calls
        if request.url.path.endswith("/auth/refresh"):
            refresh_calls += 1
            assert json.loads(request.content) == {"refresh_token": "old-refresh"}
            return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh"})
        if request.url.path.endswith("/devices"):
            authorization = request.headers.get("Authorization")
            device_calls.append(authorization)
            if authorization == "Bearer old-access":
                return httpx.Response(401, json={"detail": "expired"})
            if authorization == "Bearer new-access":
                return httpx.Response(200, json={"items": [{"device_id": "dev-1"}]})
        return httpx.Response(500)

    client = ApiClient(BASE_URL, store, httpx.MockTransport(handler))
    assert client.list_devices() == [{"device_id": "dev-1"}]
    assert device_calls == ["Bearer old-access", "Bearer new-access"]
    assert refresh_calls == 1
    assert store.access_token == "new-access"
    assert store.refresh_token == "new-refresh"
    client.close()


def test_failed_refresh_clears_tokens_and_returns_to_login_flow() -> None:
    store = MemoryTokenStore("old-access", "bad-refresh")
    device_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal device_calls
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(401, json={"detail": "invalid refresh"})
        device_calls += 1
        return httpx.Response(401)

    client = ApiClient(BASE_URL, store, httpx.MockTransport(handler))
    with pytest.raises(SessionExpired):
        client.list_devices()
    assert device_calls == 1
    assert store.access_token is None
    assert store.refresh_token is None
    client.close()


def test_retried_401_does_not_refresh_twice() -> None:
    store = MemoryTokenStore("old-access", "refresh")
    refresh_calls = 0
    device_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_calls, device_calls
        if request.url.path.endswith("/auth/refresh"):
            refresh_calls += 1
            return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh"})
        device_calls += 1
        return httpx.Response(401)

    client = ApiClient(BASE_URL, store, httpx.MockTransport(handler))
    with pytest.raises(SessionExpired):
        client.list_devices()
    assert refresh_calls == 1
    assert device_calls == 2
    assert store.access_token is None
    client.close()
