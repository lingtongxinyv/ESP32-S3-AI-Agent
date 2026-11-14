from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

from .api import ApiClient
from .serial_protocol import SerialLink, open_serial


class FunctionWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, function: Callable[[], Any], parent: Any = None) -> None:
        super().__init__(parent)
        self._function = function

    def run(self) -> None:
        try:
            self.succeeded.emit(self._function())
        except Exception as exc:
            self.failed.emit(exc)


class ProvisioningWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)
    progress = Signal(str)

    def __init__(
        self,
        api: ApiClient,
        port: str,
        wifi_ssid: str = "",
        wifi_password: str = "",
        inspect_only: bool = False,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.port = port
        self.wifi_ssid = wifi_ssid
        self.wifi_password = wifi_password
        self.inspect_only = inspect_only

    def run(self) -> None:
        try:
            self.progress.emit(f"正在打开串口 {self.port}…")
            with open_serial(self.port) as serial_port:
                link = SerialLink(serial_port)
                self.progress.emit("正在读取设备信息（GET_INFO）…")
                info = link.get_info()
                if self.inspect_only:
                    self.succeeded.emit(info)
                    return
                if not self.wifi_ssid:
                    raise ValueError("Wi-Fi SSID 不能为空")
                device_id = str(info["device_id"])
                self.progress.emit(f"正在为 {device_id} 创建绑定令牌…")
                pairing = self.api.create_pairing(device_id)
                token = pairing.get("pairing_token")
                if not token:
                    raise RuntimeError("服务器配对响应缺少 pairing_token")
                self.progress.emit("正在写入 Wi-Fi、服务器和绑定配置（SET_CONFIG）…")
                link.set_config(
                    {
                        "wifi_ssid": self.wifi_ssid,
                        "wifi_password": self.wifi_password,
                        "server_url": self.api.base_url,
                        "pairing_token": token,
                    }
                )
                self.succeeded.emit(
                    {
                        "info": info,
                        "pairing_token": token,
                        "expires_at": pairing.get("expires_at"),
                    }
                )
        except Exception as exc:
            self.failed.emit(exc)
