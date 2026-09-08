"""Qt-thread adapter for background preview-frame decoding."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from movie_maker.preview import (
    DecodedFrame,
    FrameTarget,
    PreviewDecodeCoordinator,
    PreviewDecodeFailure,
)


class PreviewBridge(QObject):
    """Keep worker callbacks away from widgets and coalesce playback requests."""

    frame_ready = Signal(object)
    frame_failed = Signal(object)
    _worker_result = Signal(object)

    def __init__(
        self,
        coordinator: PreviewDecodeCoordinator | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = coordinator or PreviewDecodeCoordinator()
        self._busy = False
        self._closed = False
        self._active_key: tuple[str, str, str, int, int] | None = None
        self._pending: FrameTarget | None = None
        self._worker_result.connect(self._deliver)

    def request(self, target: FrameTarget, *, replace: bool = False) -> None:
        """Request a frame, coalescing normal playback and replacing explicit seeks."""

        if self._closed:
            return
        if target.cache_key == self._active_key:
            return
        if self._busy and not replace:
            self._pending = target
            return
        self._submit(target)

    def _submit(self, target: FrameTarget) -> None:
        self._busy = True
        self._active_key = target.cache_key
        self._pending = None
        try:
            self._coordinator.request(target, self._worker_result.emit)
        except RuntimeError:
            self._busy = False
            self._active_key = None

    @Slot(object)
    def _deliver(self, result: object) -> None:
        if self._closed:
            return
        self._busy = False
        self._active_key = None
        if isinstance(result, DecodedFrame):
            self.frame_ready.emit(result)
        elif isinstance(result, PreviewDecodeFailure):
            self.frame_failed.emit(result)
        pending = self._pending
        self._pending = None
        if pending is not None:
            result_target = result.target if isinstance(
                result, (DecodedFrame, PreviewDecodeFailure)
            ) else None
            if result_target is None or pending.cache_key != result_target.cache_key:
                self._submit(pending)

    def cancel(self, *, clear_cache: bool = False) -> None:
        self._pending = None
        self._busy = False
        self._active_key = None
        self._coordinator.cancel()
        if clear_cache:
            self._coordinator.clear_cache()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending = None
        self._coordinator.close()
