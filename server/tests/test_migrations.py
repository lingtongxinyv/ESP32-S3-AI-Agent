from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_initial_upgrade_and_downgrade(tmp_path: Path, monkeypatch):
    server_dir = Path(__file__).resolve().parents[1]
    database = tmp_path / "migrated.db"
    monkeypatch.chdir(server_dir)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    config = Config(str(server_dir / "alembic.ini"))

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "users",
        "user_tokens",
        "devices",
        "pairings",
        "conversations",
        "messages",
        "firmware_releases",
        "ota_events",
        "alembic_version",
    } <= tables

    command.downgrade(config, "base")
    assert "users" not in inspect(engine).get_table_names()
    engine.dispose()
