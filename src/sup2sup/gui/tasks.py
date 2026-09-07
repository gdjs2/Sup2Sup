"""Cooperative background jobs with throttled updates and immediate stage/final counts."""

from time import monotonic

from PySide6.QtCore import QThread, Signal

from sup2sup.progress import OperationCancelled, Progress


class Task(QThread):
    progress = Signal(object)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.value = None
        self.error = None
        self.cancelled = False
        self._last_update = 0.0
        self._last_stage = None
        self._last_progress = None

    def report(self, update: Progress):
        if self.isInterruptionRequested():
            raise OperationCancelled()
        now = monotonic()
        boundary = update.completed == update.total
        if (update.stage != self._last_stage or now - self._last_update >= 0.05
                or boundary and update != self._last_progress):
            self._last_update, self._last_stage = now, update.stage
            self._last_progress = update
            self.progress.emit(update)

    def run(self):
        try:
            if self.isInterruptionRequested():
                raise OperationCancelled()
            self.value = self.function(self.report)
            if self.isInterruptionRequested():
                self.value = None
                self.cancelled = True
        except OperationCancelled:
            self.cancelled = True
        except Exception as exc:
            self.error = str(exc)
