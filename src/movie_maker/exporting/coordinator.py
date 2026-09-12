"""Single-job worker coordination for non-blocking MP4 export."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Protocol

from movie_maker.exporting.contracts import (
    ExportErrorCode,
    ExportFailed,
    ExportProgress,
    ExportResult,
)
from movie_maker.exporting.plan import ExportPlan
from movie_maker.exporting.runner import FfmpegExportRunner

type ExportProgressCallback = Callable[[int, ExportProgress], None]
type ExportResultCallback = Callable[[int, ExportResult], None]


class ExportRunner(Protocol):
    def run(
        self,
        plan: ExportPlan,
        cancelled: Event,
        progress_callback: Callable[[ExportProgress], None],
    ) -> ExportResult: ...


class ExportBusyError(RuntimeError):
    """Raised when a second export is requested while one is active."""


class ExportCoordinator:
    """Own exactly one active export and suppress callbacks after shutdown."""

    def __init__(self, runner: ExportRunner | None = None) -> None:
        self._runner = runner or FfmpegExportRunner()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="movie-maker-export")
        self._lock = Lock()
        self._next_job_id = 0
        self._active_job_id: int | None = None
        self._cancelled: Event | None = None
        self._future: Future[ExportResult] | None = None
        self._closed = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active_job_id is not None

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def start(
        self,
        plan: ExportPlan,
        progress_callback: ExportProgressCallback,
        result_callback: ExportResultCallback,
    ) -> int:
        with self._lock:
            if self._closed:
                raise RuntimeError("Export coordinator is closed.")
            if self._active_job_id is not None:
                raise ExportBusyError("An export is already running.")
            self._next_job_id += 1
            job_id = self._next_job_id
            cancelled = Event()
            self._active_job_id = job_id
            self._cancelled = cancelled
            future = self._executor.submit(
                self._runner.run,
                plan,
                cancelled,
                lambda progress: self._publish_progress(
                    job_id,
                    progress,
                    progress_callback,
                ),
            )
            self._future = future
        future.add_done_callback(
            lambda completed: self._complete(job_id, plan, completed, result_callback)
        )
        return job_id

    def _publish_progress(
        self,
        job_id: int,
        progress: ExportProgress,
        callback: ExportProgressCallback,
    ) -> None:
        with self._lock:
            current = not self._closed and self._active_job_id == job_id
        if current:
            callback(job_id, progress)

    def _complete(
        self,
        job_id: int,
        plan: ExportPlan,
        future: Future[ExportResult],
        callback: ExportResultCallback,
    ) -> None:
        try:
            result = future.result()
        except Exception as error:  # noqa: BLE001 - worker failure becomes typed result
            result = ExportFailed(
                plan,
                ExportErrorCode.INTERNAL_ERROR,
                "동영상 출력 작업이 예기치 않게 중단됐습니다.",
                str(error),
            )
        with self._lock:
            current = not self._closed and self._active_job_id == job_id
            if current:
                self._active_job_id = None
                self._cancelled = None
                self._future = None
        if current:
            callback(job_id, result)

    def cancel(self) -> bool:
        with self._lock:
            if self._active_job_id is None or self._cancelled is None:
                return False
            self._cancelled.set()
            return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._cancelled is not None:
                self._cancelled.set()
            future = self._future
        if future is not None:
            future.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._active_job_id = None
            self._cancelled = None
            self._future = None
