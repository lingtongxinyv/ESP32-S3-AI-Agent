from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .api import ApiClient
from .pages.conversations import ConversationsPage
from .pages.devices import DevicesPage
from .pages.firmware import FirmwarePage
from .pages.login import LoginPage
from .settings import SettingsStore


class MainWindow(QMainWindow):
    def __init__(self, api: ApiClient, settings: SettingsStore) -> None:
        super().__init__()
        self.api = api
        self.settings = settings
        self.current_user: dict[str, Any] | None = None
        self.setWindowTitle("ESP32-S3 AI Agent 桌面客户端")
        self.resize(1180, 780)
        self.setMinimumSize(900, 650)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.login_page = LoginPage(api, settings)
        self.login_page.authenticated.connect(self._authenticated)
        self.stack.addWidget(self.login_page)

        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        header = QHBoxLayout()
        heading = QLabel("ESP32-S3 AI Agent")
        heading.setObjectName("sectionTitle")
        header.addWidget(heading)
        header.addStretch(1)
        self.user_label = QLabel()
        header.addWidget(self.user_label)
        self.logout_button = QPushButton("退出登录")
        self.logout_button.clicked.connect(self.logout)
        header.addWidget(self.logout_button)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.devices_page = DevicesPage(api)
        self.conversations_page = ConversationsPage(api)
        self.firmware_page = FirmwarePage(api)
        self.tabs.addTab(self.devices_page, "设备与 USB 配网")
        self.tabs.addTab(self.conversations_page, "对话记录")
        self.tabs.addTab(self.firmware_page, "固件与 OTA")
        layout.addWidget(self.tabs, 1)
        self.stack.addWidget(workspace)
        self.workspace = workspace
        for page in (self.devices_page, self.conversations_page, self.firmware_page):
            page.session_expired.connect(self._session_expired)

    def _authenticated(self, result: dict[str, Any]) -> None:
        self.current_user = result.get("user") or {}
        username = self.current_user.get("username", "已登录")
        role = "管理员" if self.current_user.get("is_admin") else "用户"
        self.user_label.setText(f"{username}（{role}）")
        self.devices_page.server_url.setText(self.api.base_url)
        self.stack.setCurrentWidget(self.workspace)
        self.devices_page.refresh()
        self.conversations_page.refresh_devices()
        self.firmware_page.refresh_all()

    def logout(self) -> None:
        self.firmware_page.stop_polling()
        self.api.logout()
        self.current_user = None
        self.login_page.status.clear()
        self.stack.setCurrentWidget(self.login_page)

    def _session_expired(self) -> None:
        self.firmware_page.stop_polling()
        self.api.logout()
        self.current_user = None
        self.login_page.show_session_expired()
        self.stack.setCurrentWidget(self.login_page)

    def closeEvent(self, event: Any) -> None:
        pages = (self.login_page, self.devices_page, self.conversations_page, self.firmware_page)
        if any(page.has_running_workers() for page in pages):
            QMessageBox.information(self, "后台任务运行中", "请等待当前网络或串口任务结束后再退出。")
            event.ignore()
            return
        self.firmware_page.stop_polling()
        self.api.close()
        event.accept()
