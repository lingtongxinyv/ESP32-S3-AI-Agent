from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


SERVER_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = SERVER_DIR.parent
for path in (str(SERVER_DIR), str(ROOT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.database import SessionLocal  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402
from .helpers import PASSWORD, login  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'agent-test.db'}")
    monkeypatch.setenv("FIRMWARE_DIR", str(tmp_path / "firmware"))
    monkeypatch.setenv("DIALOGUE_PROVIDER", "echo")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("VOICE_SERVICE_TOKEN", "test-voice-token")
    # 测试不访问面板：空地址表示跳过面板校验，直接走本地密码。
    monkeypatch.setenv("PANEL_BASE_URL", "")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    app = create_app()
    with TestClient(app) as test_client:
        with SessionLocal() as db:
            db.add_all(
                [
                    User(username="admin", password_hash=hash_password(PASSWORD), is_admin=True),
                    User(username="alice", password_hash=hash_password(PASSWORD)),
                    User(username="bob", password_hash=hash_password(PASSWORD)),
                ]
            )
            db.commit()
        yield test_client


@pytest.fixture
def tokens(client: TestClient) -> dict[str, dict]:
    return {username: login(client, username) for username in ("admin", "alice", "bob")}
