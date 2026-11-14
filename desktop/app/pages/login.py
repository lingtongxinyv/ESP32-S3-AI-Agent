from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..api import ApiClient
from ..settings import SettingsStore
from ..workers import FunctionWorker
from .base import BasePage


class LoginPage(BasePage):
    authenticated = Signal(dict)

    def __init__(self, api: ApiClient, settings: SettingsStore, parent: Any = None) -> None:
        super().__init__(parent)
        self.api = api
        self.settings = settings

        outer = QHBoxLayout(self)
        outer.addStretch(1)
        card = QFrame()
        card.setObjectName("loginCard")
        card.setMaximumWidth(460)
        outer.addWidget(card, 2)
        outer.addStretch(1)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(42, 38, 42, 38)
        layout.setSpacing(18)

        title = QLabel("ESP32-S3 AI Agent")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel("桌面管理客户端")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QFormLayout()
        self.server_url = QLineEdit(settings.base_url)
        self.server_url.setPlaceholderText("https://example.com/agent")
        self.username = QLineEdit()
        self.username.setPlaceholderText("用户名")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("密码")
        form.addRow("服务器地址", self.server_url)
        form.addRow("用户名", self.username)
        form.addRow("密码", self.password)
        layout.addLayout(form)

        self.show_password = QCheckBox("显示密码")
        self.show_password.toggled.connect(
            lambda visible: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        layout.addWidget(self.show_password)
        self.login_button = QPushButton("登录")
        self.login_button.setObjectName("primaryButton")
        self.login_button.setMinimumHeight(38)
        self.login_button.clicked.connect(self.login)
        layout.addWidget(self.login_button)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        self.password.returnPressed.connect(self.login)
        self.username.returnPressed.connect(self.login)

    def login(self) -> None:
        if self.has_running_workers():
            return
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            self.status.setText("请输入用户名和密码。")
            return
        try:
            self.api.set_base_url(self.server_url.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.settings.set_base_url(self.api.base_url)
        self.server_url.setText(self.api.base_url)
        self.login_button.setEnabled(False)
        self.status.setText("正在登录…")
        self.run_worker(
            FunctionWorker(lambda: self.api.login(username, password), self),
            self._login_succeeded,
            self._login_failed,
            lambda: self.login_button.setEnabled(True),
        )

    def _login_succeeded(self, result: dict[str, Any]) -> None:
        self.password.clear()
        self.status.setText("")
        self.authenticated.emit(result)

    def _login_failed(self, error: Exception) -> None:
        self.status.setText(str(error))

    def show_session_expired(self) -> None:
        self.password.clear()
        self.status.setText("登录已过期，请重新登录。")
