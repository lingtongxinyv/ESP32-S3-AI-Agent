from __future__ import annotations

import httpx
from PySide6.QtCore import QSettings

from app.api import ApiClient
from app.main_window import MainWindow
from app.pages.conversations import ConversationsPage
from app.pages.devices import DevicesPage
from app.pages.firmware import FirmwarePage
from app.pages.login import LoginPage
from app.settings import DEFAULT_BASE_URL, SettingsStore

from conftest import MemoryTokenStore


def make_client() -> ApiClient:
    return ApiClient(
        "https://unit.test/agent",
        MemoryTokenStore(),
        httpx.MockTransport(lambda request: httpx.Response(500)),
    )


def test_all_pages_create_offscreen(qapp, tmp_path) -> None:
    qt_settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings = SettingsStore(qt_settings)
    assert settings.base_url == DEFAULT_BASE_URL
    client = make_client()
    window = MainWindow(client, settings)
    assert isinstance(window.login_page, LoginPage)
    assert isinstance(window.devices_page, DevicesPage)
    assert isinstance(window.conversations_page, ConversationsPage)
    assert isinstance(window.firmware_page, FirmwarePage)
    assert window.tabs.count() == 3
    assert window.login_page.server_url.text() == DEFAULT_BASE_URL
    window.close()


def test_page_renderers_accept_contract_items(qapp) -> None:
    client = make_client()
    devices = DevicesPage(client)
    devices._devices_loaded(
        [{"id": 1, "device_id": "dev-1", "name": "桌面设备", "online": True, "fw_version": "1.2.3"}]
    )
    assert devices.table.rowCount() == 1
    assert devices.table.item(0, 2).text() == "在线"
    assert devices.selected_device()["device_id"] == "dev-1"

    conversations = ConversationsPage(client)
    conversations._devices_loaded([{"device_id": "dev-1", "name": "桌面设备"}])
    assert conversations.device_combo.currentData() == "dev-1"
    firmware = FirmwarePage(client)
    firmware._firmware_loaded([{"id": 7, "version": "1.2.3", "published": True}])
    assert firmware.selected_firmware()["id"] == 7
    firmware._ota_loaded([{"version": "1.2.3", "status": "downloading", "progress": 45}])
    assert firmware.ota_table.item(0, 2).text() == "45%"
    client.close()
