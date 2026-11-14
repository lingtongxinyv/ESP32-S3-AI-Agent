from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..api import ApiClient
from ..workers import FunctionWorker
from .base import BasePage


class FirmwarePage(BasePage):
    def __init__(self, api: ApiClient, parent: Any = None) -> None:
        super().__init__(parent)
        self.api = api
        self._history_loading = False
        layout = QVBoxLayout(self)

        upload_group = QGroupBox("上传固件")
        upload_layout = QFormLayout(upload_group)
        path_row = QHBoxLayout()
        self.file_path = QLineEdit()
        self.file_path.setPlaceholderText("选择要上传的固件二进制文件")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self.browse_firmware)
        path_row.addWidget(self.file_path, 1)
        path_row.addWidget(browse)
        path_widget = QWidget()
        path_widget.setLayout(path_row)
        self.version = QLineEdit()
        self.version.setPlaceholderText("例如 1.0.0")
        self.hardware = QLineEdit("esp32-s3")
        self.hardware.setPlaceholderText("例如 esp32-s3")
        self.protocol = QLineEdit("1")
        self.protocol.setPlaceholderText("协议版本，例如 1")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("版本说明（可选）")
        upload_layout.addRow("固件文件", path_widget)
        upload_layout.addRow("版本", self.version)
        upload_layout.addRow("硬件", self.hardware)
        upload_layout.addRow("协议", self.protocol)
        upload_layout.addRow("说明", self.notes)
        self.upload_button = QPushButton("上传")
        self.upload_button.setObjectName("primaryButton")
        self.upload_button.clicked.connect(self.upload)
        upload_layout.addRow("", self.upload_button)
        layout.addWidget(upload_group)

        firmware_toolbar = QHBoxLayout()
        firmware_toolbar.addWidget(QLabel("固件版本"))
        firmware_toolbar.addStretch(1)
        self.refresh_button = QPushButton("刷新固件")
        self.refresh_button.clicked.connect(self.refresh_firmware)
        self.publish_button = QPushButton("发布选中版本")
        self.publish_button.clicked.connect(self.publish_selected)
        firmware_toolbar.addWidget(self.refresh_button)
        firmware_toolbar.addWidget(self.publish_button)
        layout.addLayout(firmware_toolbar)
        self.firmware_table = QTableWidget(0, 5)
        self.firmware_table.setHorizontalHeaderLabels(["版本", "硬件", "大小", "发布时间", "状态"])
        self.firmware_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.firmware_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.firmware_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.firmware_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.firmware_table.verticalHeader().setVisible(False)
        layout.addWidget(self.firmware_table, 2)

        ota_group = QGroupBox("设备 OTA")
        ota_layout = QVBoxLayout(ota_group)
        ota_toolbar = QHBoxLayout()
        self.ota_device = QComboBox()
        self.ota_device.setMinimumWidth(250)
        self.ota_device.currentIndexChanged.connect(self.load_ota_history)
        self.refresh_devices_button = QPushButton("刷新设备")
        self.refresh_devices_button.clicked.connect(self.refresh_devices)
        self.ota_button = QPushButton("向设备推送选中固件")
        self.ota_button.setObjectName("primaryButton")
        self.ota_button.clicked.connect(self.start_ota)
        self.refresh_ota_button = QPushButton("刷新进度")
        self.refresh_ota_button.clicked.connect(self.load_ota_history)
        ota_toolbar.addWidget(QLabel("目标设备"))
        ota_toolbar.addWidget(self.ota_device, 1)
        ota_toolbar.addWidget(self.refresh_devices_button)
        ota_toolbar.addWidget(self.ota_button)
        ota_toolbar.addWidget(self.refresh_ota_button)
        ota_layout.addLayout(ota_toolbar)
        self.ota_table = QTableWidget(0, 5)
        self.ota_table.setHorizontalHeaderLabels(["固件版本", "状态", "进度", "更新时间", "错误"])
        self.ota_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ota_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ota_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ota_table.verticalHeader().setVisible(False)
        ota_layout.addWidget(self.ota_table)
        layout.addWidget(ota_group, 2)
        self.status = QLabel("上传并发布固件后，可选择目标设备开始 OTA。")
        layout.addWidget(self.status)

        self.ota_timer = QTimer(self)
        self.ota_timer.setInterval(5000)
        self.ota_timer.timeout.connect(self.load_ota_history)

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None else str(value)

    def refresh_all(self) -> None:
        self.refresh_firmware()
        self.refresh_devices()

    def browse_firmware(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择固件", "", "固件二进制 (*.bin);;所有文件 (*)"
        )
        if path:
            self.file_path.setText(path)

    def upload(self) -> None:
        path = self.file_path.text().strip()
        version = self.version.text().strip()
        hardware = self.hardware.text().strip() or "esp32-s3"
        protocol = self.protocol.text().strip() or "1"
        if not path or not version:
            QMessageBox.information(self, "信息不完整", "请选择固件文件并填写版本号。")
            return
        if not Path(path).is_file():
            QMessageBox.warning(self, "文件不存在", "选择的固件文件不存在。")
            return
        self.upload_button.setEnabled(False)
        self.status.setText("正在上传固件…")
        self.run_worker(
            FunctionWorker(
                lambda: self.api.upload_firmware(
                    path, version, hardware, protocol, self.notes.text()
                ),
                self,
            ),
            self._uploaded,
            on_finished=lambda: self.upload_button.setEnabled(True),
        )

    def _uploaded(self, _: Any) -> None:
        self.status.setText("固件上传完成。")
        self.file_path.clear()
        self.refresh_firmware()

    def refresh_firmware(self) -> None:
        if not self.refresh_button.isEnabled():
            return
        self.refresh_button.setEnabled(False)
        self.run_worker(
            FunctionWorker(self.api.list_firmware, self),
            self._firmware_loaded,
            on_finished=lambda: self.refresh_button.setEnabled(True),
        )

    def _firmware_loaded(self, items: list[dict[str, Any]]) -> None:
        self.firmware_table.setRowCount(len(items))
        for row, firmware in enumerate(items):
            size = firmware.get("size", firmware.get("file_size"))
            published = firmware.get("published") or firmware.get("is_published") or firmware.get("published_at")
            status = "已发布" if published else "未发布"
            values = [
                firmware.get("version"),
                firmware.get("hardware"),
                size,
                firmware.get("published_at", firmware.get("created_at")),
                status,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(self._text(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, firmware)
                self.firmware_table.setItem(row, column, item)
        if items:
            self.firmware_table.selectRow(0)

    def selected_firmware(self) -> dict[str, Any] | None:
        row = self.firmware_table.currentRow()
        item = self.firmware_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def publish_selected(self) -> None:
        firmware = self.selected_firmware()
        if not firmware or firmware.get("id") is None:
            QMessageBox.information(self, "请选择固件", "请先选择要发布的固件版本。")
            return
        firmware_id = firmware["id"]
        self.publish_button.setEnabled(False)
        self.run_worker(
            FunctionWorker(lambda: self.api.publish_firmware(firmware_id), self),
            lambda _: self.refresh_firmware(),
            on_finished=lambda: self.publish_button.setEnabled(True),
        )

    def refresh_devices(self) -> None:
        if not self.refresh_devices_button.isEnabled():
            return
        self.refresh_devices_button.setEnabled(False)
        self.run_worker(
            FunctionWorker(self.api.list_devices, self),
            self._devices_loaded,
            on_finished=lambda: self.refresh_devices_button.setEnabled(True),
        )

    def _devices_loaded(self, devices: list[dict[str, Any]]) -> None:
        previous = self.ota_device.currentData()
        self.ota_device.blockSignals(True)
        self.ota_device.clear()
        for device in devices:
            online = "在线" if device.get("online") else "离线"
            label = f"{device.get('name') or device.get('device_id')} · {online}"
            self.ota_device.addItem(label, device.get("device_id"))
        index = self.ota_device.findData(previous)
        if index >= 0:
            self.ota_device.setCurrentIndex(index)
        self.ota_device.blockSignals(False)
        self.load_ota_history()

    def start_ota(self) -> None:
        firmware = self.selected_firmware()
        device_id = self.ota_device.currentData()
        if not firmware or firmware.get("id") is None or not device_id:
            QMessageBox.information(self, "信息不完整", "请选择已上传的固件和目标设备。")
            return
        firmware_id = firmware["id"]
        self.ota_button.setEnabled(False)
        self.status.setText("正在创建 OTA 任务…")
        self.run_worker(
            FunctionWorker(lambda: self.api.start_ota(str(device_id), firmware_id), self),
            self._ota_started,
            on_finished=lambda: self.ota_button.setEnabled(True),
        )

    def _ota_started(self, _: Any) -> None:
        self.status.setText("OTA 任务已创建，将每 5 秒刷新进度。")
        self.ota_timer.start()
        self.load_ota_history()

    def load_ota_history(self) -> None:
        device_id = self.ota_device.currentData()
        if not device_id or self._history_loading:
            if not device_id:
                self.ota_table.setRowCount(0)
            return
        self._history_loading = True
        self.run_worker(
            FunctionWorker(lambda: self.api.ota_history(str(device_id)), self),
            self._ota_loaded,
            self._ota_failed,
            lambda: setattr(self, "_history_loading", False),
        )

    def _ota_loaded(self, items: list[dict[str, Any]]) -> None:
        self.ota_table.setRowCount(len(items))
        for row, item in enumerate(items):
            progress = item.get("progress", item.get("progress_percent"))
            if isinstance(progress, (int, float)):
                progress = f"{progress:g}%"
            values = [
                item.get("fw_version", item.get("version")),
                item.get("status"),
                progress,
                item.get("updated_at", item.get("created_at")),
                item.get("error", item.get("error_message")),
            ]
            for column, value in enumerate(values):
                self.ota_table.setItem(row, column, QTableWidgetItem(self._text(value)))
        if items:
            latest = items[0]
            progress = latest.get("progress", latest.get("progress_percent", ""))
            self.status.setText(f"最新 OTA 状态：{latest.get('status', '未知')} {progress}")
        else:
            self.status.setText("该设备暂无 OTA 记录。")

    def _ota_failed(self, error: Exception) -> None:
        self.ota_timer.stop()
        self.handle_error(error)

    def stop_polling(self) -> None:
        self.ota_timer.stop()
