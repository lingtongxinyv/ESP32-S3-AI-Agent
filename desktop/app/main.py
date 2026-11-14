from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from app.api import ApiClient
from app.main_window import MainWindow
from app.settings import SettingsStore

_STYLE = """
QWidget { font-size: 14px; }
QMainWindow, QWidget#centralwidget { background: #f5f7fb; }
QFrame#loginCard { background: white; border: 1px solid #dfe4ee; border-radius: 12px; }
QLabel#titleLabel { font-size: 26px; font-weight: 700; color: #172b4d; }
QLabel#sectionTitle { font-size: 20px; font-weight: 650; color: #172b4d; }
QPushButton { min-height: 30px; padding: 2px 12px; border: 1px solid #c8d0df; border-radius: 5px; background: white; }
QPushButton:hover { background: #eef3ff; }
QPushButton:disabled { color: #98a2b3; background: #edf0f5; }
QPushButton#primaryButton { color: white; background: #356ae6; border-color: #356ae6; font-weight: 600; }
QPushButton#primaryButton:hover { background: #2857c5; }
QLineEdit, QComboBox, QDateEdit { min-height: 30px; padding: 0 8px; border: 1px solid #c8d0df; border-radius: 5px; background: white; }
QTableWidget { background: white; alternate-background-color: #f7f9fc; gridline-color: #e5e9f0; }
QHeaderView::section { padding: 7px; border: none; background: #eaf0fa; font-weight: 600; }
QGroupBox { margin-top: 12px; padding-top: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTabWidget::pane { border: 1px solid #dfe4ee; }
QTabBar::tab { padding: 10px 20px; }
QTabBar::tab:selected { color: #2857c5; font-weight: 600; }
"""


def main() -> int:
    QCoreApplication.setOrganizationName("Lingtong")
    QCoreApplication.setApplicationName("ESP32-S3-AI-Agent")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(_STYLE)
    settings = SettingsStore()
    api = ApiClient(settings.base_url)
    window = MainWindow(api, settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
