from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


class MemoryTokenStore:
    def __init__(self, access_token: str | None = None, refresh_token: str | None = None) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self.base_url = ""

    def set_base_url(self, base_url: str) -> None:
        self.base_url = base_url

    @property
    def access_token(self) -> str | None:
        return self._access_token

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    def save(self, access_token: str, refresh_token: str) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token

    def clear(self) -> None:
        self._access_token = None
        self._refresh_token = None


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
