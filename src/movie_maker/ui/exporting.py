"""Qt-thread bridge for one cancellable background export."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from movie_maker.exporting import (
    ExportBusyError,
    ExportCancelled,
    ExportCoordinator,
    ExportFailed,
    ExportPlan,
    ExportProgress,
    ExportSucceeded,
)


class ExportBridge(QObject):
    """Deliver coordinator callbacks on the owning Qt thread."""

    progress_changed = Signal(object)
    export_finished = Signal(object)
    _worker_progress = Signal(int, object)
    _worker_result = Signal(int, object)

    def __init__(
        self,
        coordinator: ExportCoordinator | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = coordinator or ExportCoordinator()
        self._active_job_id: int | None = None
        self._closed = False
        self._worker_progress.connect(self._deliver_progress)
        self._worker_result.connect(self._deliver_result)

    @property
    def active(self) -> bool:
        return self._active_job_id is not None

    def start(self, plan: ExportPlan) -> int:
        if self._closed:
            raise RuntimeError("Export bridge is closed.")
        if self._active_job_id is not None:
            raise ExportBusyError("An export is already running.")
        job_id = self._coordinator.start(
            plan,
            self._worker_progress.emit,
            self._worker_result.emit,
        )
        self._active_job_id = job_id
        return job_id

    @Slot(int, object)
    def _deliver_progress(self, job_id: int, progress: object) -> None:
        if (
            self._closed
            or job_id != self._active_job_id
            or not isinstance(progress, ExportProgress)
        ):
            return
        self.progress_changed.emit(progress)

    @Slot(int, object)
    def _deliver_result(self, job_id: int, result: object) -> None:
        if self._closed or job_id != self._active_job_id:
            return
        self._active_job_id = None
        if isinstance(result, (ExportSucceeded, ExportFailed, ExportCancelled)):
            self.export_finished.emit(result)

    def cancel(self) -> bool:
        return self._coordinator.cancel()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._active_job_id = None
        self._coordinator.close()
