from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..api import ApiClient
from ..serial_protocol import available_ports
from ..workers import FunctionWorker, ProvisioningWorker
from .base import BasePage


class DevicesPage(BasePage):
    def __init__(self, api: ApiClient, parent: Any = None) -> None:
        super().__init__(parent)
        self.api = api
        self.usb_info: dict[str, Any] | None = None
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        title = QLabel("设备列表")
        title.setObjectName("sectionTitle")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        self.refresh_button = QPushButton("刷新设备")
        self.refresh_button.clicked.connect(self.refresh)
        self.unbind_button = QPushButton("解绑选中设备")
        self.unbind_button.clicked.connect(self.unbind_selected)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.unbind_button)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["设备 ID", "名称", "在线状态", "固件版本", "最后在线"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 3)

        usb_group = QGroupBox("USB 配网与绑定（115200 8N1）")
        usb_layout = QVBoxLayout(usb_group)
        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(330)
        scan_button = QPushButton("扫描串口")
        scan_button.clicked.connect(self.refresh_ports)
        self.info_button = QPushButton("读取设备信息")
        self.info_button.clicked.connect(self.read_info)
        port_row.addWidget(QLabel("串口"))
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(scan_button)
        port_row.addWidget(self.info_button)
        usb_layout.addLayout(port_row)

        form = QFormLayout()
        self.wifi_ssid = QLineEdit()
        self.wifi_password = QLineEdit()
        self.wifi_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.server_url = QLineEdit(api.base_url)
        self.server_url.setReadOnly(True)
        form.addRow("Wi-Fi SSID", self.wifi_ssid)
        form.addRow("Wi-Fi 密码", self.wifi_password)
        form.addRow("写入的服务器", self.server_url)
        usb_layout.addLayout(form)
        self.provision_button = QPushButton("配网并绑定")
        self.provision_button.setObjectName("primaryButton")
        self.provision_button.clicked.connect(self.provision)
        usb_layout.addWidget(self.provision_button)
        self.usb_status = QLabel("请先扫描串口；配网时会重新读取设备信息并获取一次性绑定令牌。")
        self.usb_status.setWordWrap(True)
        usb_layout.addWidget(self.usb_status)
        layout.addWidget(usb_group, 2)
        self.refresh_ports()

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None else str(value)

    def refresh(self) -> None:
        if not self.refresh_button.isEnabled():
            return
        self.refresh_button.setEnabled(False)
        self.run_worker(
            FunctionWorker(self.api.list_devices, self),
            self._devices_loaded,
            on_finished=lambda: self.refresh_button.setEnabled(True),
        )

    def _devices_loaded(self, devices: list[dict[str, Any]]) -> None:
        self.table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            values = [
                device.get("device_id"),
                device.get("name"),
                "在线" if device.get("online") else "离线",
                device.get("fw_version"),
                self._text(device.get("last_seen_at")).replace("T", " "),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(self._text(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, device)
                self.table.setItem(row, column, item)
        if devices:
            self.table.selectRow(0)

    def selected_device(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def unbind_selected(self) -> None:
        device = self.selected_device()
        if not device:
            QMessageBox.information(self, "请选择设备", "请先在设备列表中选择一台设备。")
            return
        device_id = str(device.get("device_id", ""))
        answer = QMessageBox.question(
            self,
            "确认解绑",
            f"确定解绑设备 {device_id} 吗？此操作不会清除设备中的 Wi-Fi 配置。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.unbind_button.setEnabled(False)
        self.run_worker(
            FunctionWorker(lambda: self.api.delete_device(device_id), self),
            lambda _: self.refresh(),
            on_finished=lambda: self.unbind_button.setEnabled(True),
        )

    def refresh_ports(self) -> None:
        previous = self.port_combo.currentData()
        self.port_combo.clear()
        try:
            ports = available_ports()
        except Exception as exc:
            self.usb_status.setText(f"扫描串口失败：{exc}")
            return
        for port, label in ports:
            self.port_combo.addItem(label, port)
        index = self.port_combo.findData(previous)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)
        if not ports:
            self.usb_status.setText("未发现串口。请连接设备；Windows 会枚举可用 COM 口。")

    def _selected_port(self) -> str | None:
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.information(self, "未选择串口", "请连接设备并扫描串口。")
            return None
        return str(port)

    def _set_usb_busy(self, busy: bool) -> None:
        self.info_button.setEnabled(not busy)
        self.provision_button.setEnabled(not busy)
        self.port_combo.setEnabled(not busy)

    def read_info(self) -> None:
        port = self._selected_port()
        if not port or not self.info_button.isEnabled():
            return
        self._set_usb_busy(True)
        worker = ProvisioningWorker(self.api, port, inspect_only=True, parent=self)
        worker.progress.connect(self.usb_status.setText)
        self.run_worker(
            worker,
            self._info_loaded,
            self._usb_failed,
            lambda: self._set_usb_busy(False),
        )

    def _info_loaded(self, info: dict[str, Any]) -> None:
        self.usb_info = info
        self.usb_status.setText(
            f"设备：{info.get('device_id', '未知')}    固件：{info.get('fw_version', '未知')}"
            f"    协议：{info.get('protocol', '未知')}"
        )

    def provision(self) -> None:
        port = self._selected_port()
        if not port or not self.info_button.isEnabled():
            return
        ssid = self.wifi_ssid.text().strip()
        if not ssid:
            QMessageBox.information(self, "缺少 Wi-Fi SSID", "请输入设备要连接的 Wi-Fi SSID。")
            return
        self.server_url.setText(self.api.base_url)
        self._set_usb_busy(True)
        worker = ProvisioningWorker(
            self.api,
            port,
            ssid,
            self.wifi_password.text(),
            parent=self,
        )
        worker.progress.connect(self.usb_status.setText)
        self.run_worker(
            worker,
            self._provisioned,
            self._usb_failed,
            lambda: self._set_usb_busy(False),
        )

    def _provisioned(self, result: dict[str, Any]) -> None:
        self.wifi_password.clear()
        self.usb_info = result["info"]
        device_id = self.usb_info.get("device_id", "未知")
        expiry = result.get("expires_at") or "由服务器决定"
        self.usb_status.setText(f"设备 {device_id} 已收到配网与绑定配置（ACK）。令牌有效期：{expiry}")
        self.refresh()

    def _usb_failed(self, error: Exception) -> None:
        self.usb_status.setText(str(error))
        self.handle_error(error)
