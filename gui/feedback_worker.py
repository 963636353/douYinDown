"""One-shot background work; never owns or reads GUI controls."""

import threading

from PyQt5.QtCore import QObject, pyqtSignal

from core.feedback import FeedbackError


class FeedbackTask(QObject):
    completed = pyqtSignal(object, object)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self._operation = operation

    def start(self):
        # Daemon threads allow normal application shutdown during a slow request.
        threading.Thread(target=self._run, name='feedback-request', daemon=True).start()

    def _run(self):
        result, error = None, None
        try:
            result = self._operation()
        except FeedbackError as exc:
            error = exc
        except Exception:
            error = FeedbackError('反馈处理发生异常，内容已保留，请稍后重试。')
        try:
            self.completed.emit(result, error)
        except RuntimeError:
            # The page/application can be destroyed before a network request ends.
            pass
