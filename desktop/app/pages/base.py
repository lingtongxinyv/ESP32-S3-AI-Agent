from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from ..api import SessionExpired


class BasePage(QWidget):
    session_expired = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._workers: set[QThread] = set()

    def run_worker(
        self,
        worker: QThread,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self._workers.add(worker)
        if on_success is not None and hasattr(worker, "succeeded"):
            worker.succeeded.connect(on_success)
        if hasattr(worker, "failed"):
            worker.failed.connect(on_error or self.handle_error)
        if on_finished is not None:
            worker.finished.connect(on_finished)
        worker.finished.connect(lambda: self._workers.discard(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def handle_error(self, error: Exception) -> None:
        if isinstance(error, SessionExpired):
            self.session_expired.emit()
            return
        QMessageBox.critical(self, "操作失败", str(error))

    def has_running_workers(self) -> bool:
        return any(worker.isRunning() for worker in self._workers)
