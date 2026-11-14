from __future__ import annotations

from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..api import ApiClient
from ..workers import FunctionWorker
from .base import BasePage


class ConversationsPage(BasePage):
    def __init__(self, api: ApiClient, parent: Any = None) -> None:
        super().__init__(parent)
        self.api = api
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("设备"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(220)
        filters.addWidget(self.device_combo)
        self.refresh_devices_button = QPushButton("刷新设备")
        self.refresh_devices_button.clicked.connect(self.refresh_devices)
        filters.addWidget(self.refresh_devices_button)
        filters.addSpacing(16)
        filters.addWidget(QLabel("日期"))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        filters.addWidget(self.date_edit)
        self.query_button = QPushButton("查询对话")
        self.query_button.setObjectName("primaryButton")
        self.query_button.clicked.connect(self.load_conversations)
        filters.addWidget(self.query_button)
        filters.addStretch(1)
        layout.addLayout(filters)

        splitter = QSplitter()
        self.conversations = QTableWidget(0, 2)
        self.conversations.setHorizontalHeaderLabels(["时间", "对话"])
        self.conversations.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.conversations.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.conversations.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.conversations.verticalHeader().setVisible(False)
        self.conversations.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.conversations.itemSelectionChanged.connect(self.load_selected_messages)
        splitter.addWidget(self.conversations)
        self.messages = QTableWidget(0, 3)
        self.messages.setHorizontalHeaderLabels(["时间", "角色", "内容"])
        self.messages.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.messages.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.messages.verticalHeader().setVisible(False)
        self.messages.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.messages.setWordWrap(True)
        splitter.addWidget(self.messages)
        splitter.setSizes([350, 750])
        layout.addWidget(splitter, 1)
        self.status = QLabel("请选择设备和日期。")
        layout.addWidget(self.status)

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None else str(value)

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
        previous = self.device_combo.currentData()
        self.device_combo.clear()
        for device in devices:
            label = f"{device.get('name') or device.get('device_id')} ({device.get('device_id')})"
            self.device_combo.addItem(label, device.get("device_id"))
        index = self.device_combo.findData(previous)
        if index >= 0:
            self.device_combo.setCurrentIndex(index)

    def load_conversations(self) -> None:
        device_id = self.device_combo.currentData()
        if not device_id:
            QMessageBox.information(self, "请选择设备", "请先刷新并选择一台设备。")
            return
        if not self.query_button.isEnabled():
            return
        date = self.date_edit.date().toString("yyyy-MM-dd")
        self.query_button.setEnabled(False)
        self.status.setText("正在查询对话…")
        self.run_worker(
            FunctionWorker(lambda: self.api.list_conversations(str(device_id), date), self),
            self._conversations_loaded,
            on_finished=lambda: self.query_button.setEnabled(True),
        )

    def _conversations_loaded(self, items: list[dict[str, Any]]) -> None:
        self.messages.setRowCount(0)
        self.conversations.setRowCount(len(items))
        for row, conversation in enumerate(items):
            conversation_id = conversation.get("id")
            timestamp = conversation.get("created_at") or conversation.get("started_at")
            title = conversation.get("title") or f"对话 {conversation_id}"
            time_item = QTableWidgetItem(self._text(timestamp).replace("T", " "))
            time_item.setData(Qt.ItemDataRole.UserRole, conversation_id)
            self.conversations.setItem(row, 0, time_item)
            self.conversations.setItem(row, 1, QTableWidgetItem(self._text(title)))
        self.status.setText(f"共 {len(items)} 个对话。选择左侧对话查看消息。")
        if items:
            self.conversations.selectRow(0)

    def load_selected_messages(self) -> None:
        selected = self.conversations.selectedItems()
        if not selected:
            return
        conversation_id = self.conversations.item(selected[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if conversation_id is None:
            return
        self.status.setText("正在加载消息…")
        self.run_worker(
            FunctionWorker(lambda: self.api.list_messages(conversation_id), self),
            self._messages_loaded,
        )

    def _messages_loaded(self, items: list[dict[str, Any]]) -> None:
        roles = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}
        self.messages.setRowCount(len(items))
        for row, message in enumerate(items):
            values = [
                self._text(message.get("created_at")).replace("T", " "),
                roles.get(str(message.get("role")), self._text(message.get("role"))),
                self._text(message.get("content", message.get("text"))),
            ]
            for column, value in enumerate(values):
                self.messages.setItem(row, column, QTableWidgetItem(value))
        self.messages.resizeRowsToContents()
        self.status.setText(f"共 {len(items)} 条消息。")
