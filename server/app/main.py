from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router as api_router
from .chat import router as chat_router
from .config import Settings
from .database import SessionLocal, configure_database, create_schema
from .dialogue_routing import DialogueRouter
from .guard import CostGuard
from .memory_router import router as memory_router
from .models import Device
from .panel import PanelStore
from .profiles import ProfileStore
from .providers import DialogueProvider
from .voice import router as voice_router
from .websocket import OnlineDevices, device_websocket


def reset_online_flags() -> None:
    """进程刚起来时不可能有设备连着，清掉上次遗留的 online 标记。"""
    with SessionLocal() as db:
        db.query(Device).filter(Device.online.is_(True)).update({Device.online: False})
        db.commit()


def create_app() -> FastAPI:
    settings = Settings.from_env()
    configure_database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        create_schema()
        reset_online_flags()
        settings.firmware_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="ESP32-S3 AI Agent Server", lifespan=lifespan)
    app.state.settings = settings
    app.state.dialogue = DialogueProvider(settings)
    app.state.dialogue_router = DialogueRouter(settings)
    app.state.guard = CostGuard(
        max_turns_per_min=settings.guard_max_turns_per_min,
        max_chars_per_hour=settings.guard_max_chars_per_hour,
        failure_threshold=settings.guard_failure_threshold,
        cooldown_seconds=settings.guard_cooldown_seconds,
    )
    app.state.online_devices = OnlineDevices()
    app.state.profiles = ProfileStore()
    app.state.panels = PanelStore()
    app.include_router(api_router)
    app.include_router(chat_router)
    app.include_router(memory_router)
    app.include_router(voice_router)
    app.websocket("/ws/v1/device")(device_websocket)
    return app
