from __future__ import annotations

import keyring
from keyring.errors import KeyringError, PasswordDeleteError


class KeyringTokenStore:
    """Stores tokens only in the operating system credential vault."""

    def __init__(self, base_url: str) -> None:
        self._service = ""
        self.set_base_url(base_url)

    def set_base_url(self, base_url: str) -> None:
        self._service = f"ESP32-S3-AI-Agent:{base_url.rstrip('/')}"

    @property
    def access_token(self) -> str | None:
        return keyring.get_password(self._service, "access_token")

    @property
    def refresh_token(self) -> str | None:
        return keyring.get_password(self._service, "refresh_token")

    def save(self, access_token: str, refresh_token: str) -> None:
        keyring.set_password(self._service, "access_token", access_token)
        keyring.set_password(self._service, "refresh_token", refresh_token)

    def clear(self) -> None:
        for key in ("access_token", "refresh_token"):
            try:
                keyring.delete_password(self._service, key)
            except (PasswordDeleteError, KeyringError):
                pass
