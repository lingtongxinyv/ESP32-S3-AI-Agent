from __future__ import annotations

from PySide6.QtCore import QSettings

DEFAULT_BASE_URL = "https://agent.lingtong.asia/agent"


class SettingsStore:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings()

    @property
    def base_url(self) -> str:
        return str(self._settings.value("server/base_url", DEFAULT_BASE_URL))

    def set_base_url(self, base_url: str) -> None:
        self._settings.setValue("server/base_url", base_url.strip().rstrip("/"))
        self._settings.sync()
