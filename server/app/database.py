from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


engine: Engine | None = None
SessionLocal = sessionmaker(expire_on_commit=False, class_=Session)


def configure_database(database_url: str) -> Engine:
    global engine
    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    kwargs: dict = {"pool_pre_ping": True}
    if backend == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url.database == ":memory:":
            kwargs["poolclass"] = StaticPool

    new_engine = create_engine(database_url, **kwargs)
    if backend == "sqlite":
        @event.listens_for(new_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    old_engine = engine
    engine = new_engine
    SessionLocal.configure(bind=new_engine)
    if old_engine is not None:
        old_engine.dispose()
    return new_engine


def create_schema() -> None:
    if engine is None:
        raise RuntimeError("database is not configured")
    Base.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db
