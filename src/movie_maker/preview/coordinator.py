"""Latest-request-wins background coordination for preview decoding."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Condition, Event, Lock
from typing import Protocol, cast

from movie_maker.preview.decoder import (
    DecodedFrame,
    FfmpegFrameDecoder,
    PreviewDecodeCancelled,
    PreviewDecodeErrorCode,
    PreviewDecodeFailure,
    PreviewDecodeResult,
    PreviewStreamResult,
)
from movie_maker.preview.timeline import FrameTarget

type PreviewResultCallback = Callable[[DecodedFrame | PreviewDecodeFailure], None]


class PreviewDecoder(Protocol):
    def decode(self, target: FrameTarget, cancelled: Event) -> PreviewDecodeResult: ...


class StreamingPreviewDecoder(PreviewDecoder, Protocol):
    def can_stream(self, target: FrameTarget) -> bool: ...

    def stream(
        self,
        target: FrameTarget,
        cancelled: Event,
        publish: Callable[[DecodedFrame], bool],
    ) -> PreviewStreamResult: ...


class PreviewDecodeCoordinator:
    """Run cancellable decodes and publish only the current generation."""

    def __init__(
        self,
        decoder: PreviewDecoder | None = None,
        *,
        max_workers: int = 2,
        cache_size: int = 24,
    ) -> None:
        self._decoder = decoder or FfmpegFrameDecoder()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="movie-maker-preview",
        )
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple[object, ...], DecodedFrame] = OrderedDict()
        self._lock = Lock()
        self._playback_changed = Condition(self._lock)
        self._generation = 0
        self._closed = False
        self._jobs: dict[int, tuple[Event, Future[PreviewDecodeResult]]] = {}
        self._playback_job: tuple[
            int,
            tuple[object, ...],
            Event,
            Future[PreviewStreamResult],
        ] | None = None
        self._playback_position_ns = 0

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def request(self, target: FrameTarget, callback: PreviewResultCallback) -> int:
        """Cancel older work and schedule one current target."""

        with self._lock:
            if self._closed:
                raise RuntimeError("Preview decode coordinator is closed.")
            self._generation += 1
            generation = self._generation
            if self._playback_job is not None:
                self._playback_job[2].set()
                self._playback_changed.notify_all()
            old_futures = tuple(job_future for _, job_future in self._jobs.values())
            for event, job_future in self._jobs.values():
                event.set()
            cached = self._cache.get(target.cache_key)
            if cached is not None:
                self._cache.move_to_end(target.cache_key)
            cancelled = Event()
            future: Future[PreviewDecodeResult] | None
            if cached is None:
                future = self._executor.submit(self._decoder.decode, target, cancelled)
                self._jobs[generation] = (cancelled, future)
            else:
                future = None

        for old_future in old_futures:
            old_future.cancel()
        if cached is not None:
            callback(cached)
            return generation
        if future is not None:
            future.add_done_callback(
                lambda completed: self._complete(generation, target, completed, callback)
            )
        return generation

    def play(self, target: FrameTarget, callback: PreviewResultCallback) -> bool:
        """Start or advance one persistent decoder for the current simple clip."""

        can_stream = getattr(self._decoder, "can_stream", None)
        stream_method = getattr(self._decoder, "stream", None)
        if not callable(can_stream) or not callable(stream_method) or not can_stream(target):
            return False
        position = target.project_position
        if position is None:
            return False
        stream_key = (hash(target.project), target.clip_id, target.source_path, target.stream_index)
        decoder = cast(StreamingPreviewDecoder, self._decoder)
        with self._playback_changed:
            if self._closed:
                raise RuntimeError("Preview decode coordinator is closed.")
            current = self._playback_job
            if current is not None and current[1] == stream_key and not current[3].done():
                self._playback_position_ns = position.nanoseconds
                self._playback_changed.notify_all()
                return True
            if current is not None:
                current[2].set()
            for event, _ in self._jobs.values():
                event.set()
            self._generation += 1
            generation = self._generation
            cancelled = Event()
            self._playback_position_ns = position.nanoseconds

            def publish(frame: DecodedFrame) -> bool:
                frame_position = frame.target.project_position
                if frame_position is None:
                    return False
                with self._playback_changed:
                    while (
                        not cancelled.is_set()
                        and not self._closed
                        and generation == self._generation
                        and frame_position.nanoseconds > self._playback_position_ns
                    ):
                        self._playback_changed.wait(timeout=0.1)
                    current_generation = generation == self._generation and not self._closed
                if not current_generation or cancelled.is_set():
                    return False
                callback(frame)
                return True

            future = self._executor.submit(decoder.stream, target, cancelled, publish)
            self._playback_job = (generation, stream_key, cancelled, future)
        future.add_done_callback(
            lambda completed: self._complete_playback(generation, target, completed, callback)
        )
        return True

    def _complete_playback(
        self,
        generation: int,
        target: FrameTarget,
        future: Future[PreviewStreamResult],
        callback: PreviewResultCallback,
    ) -> None:
        try:
            result = future.result()
        except Exception as error:  # noqa: BLE001 - convert decoder boundary failures
            result = PreviewDecodeFailure(
                target,
                code=PreviewDecodeErrorCode.INTERNAL_ERROR,
                message="지속형 미리 보기 작업이 예기치 않게 실패했습니다.",
                detail=str(error),
            )
        with self._playback_changed:
            current = generation == self._generation and not self._closed
            if self._playback_job is not None and self._playback_job[0] == generation:
                self._playback_job = None
            self._playback_changed.notify_all()
        if current and isinstance(result, PreviewDecodeFailure):
            callback(result)

    def stop_playback(self) -> None:
        with self._playback_changed:
            if self._playback_job is None:
                return
            self._generation += 1
            self._playback_job[2].set()
            self._playback_changed.notify_all()

    def _complete(
        self,
        generation: int,
        target: FrameTarget,
        future: Future[PreviewDecodeResult],
        callback: PreviewResultCallback,
    ) -> None:
        try:
            result = future.result()
        except Exception as error:  # noqa: BLE001 - convert decoder boundary failures
            with self._lock:
                current = generation == self._generation and not self._closed
                self._jobs.pop(generation, None)
            if current:
                callback(
                    PreviewDecodeFailure(
                        target,
                        code=PreviewDecodeErrorCode.INTERNAL_ERROR,
                        message="미리 보기 작업이 예기치 않게 실패했습니다. 다시 시도하세요.",
                        detail=str(error),
                    )
                )
            return

        with self._lock:
            self._jobs.pop(generation, None)
            current = generation == self._generation and not self._closed
            if current and isinstance(result, DecodedFrame):
                self._cache[result.target.cache_key] = result
                self._cache.move_to_end(result.target.cache_key)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        if current and not isinstance(result, PreviewDecodeCancelled):
            callback(result)

    def cancel(self) -> None:
        """Invalidate results and request termination of every active process."""

        with self._playback_changed:
            self._generation += 1
            if self._playback_job is not None:
                self._playback_job[2].set()
            futures = tuple(job_future for _, job_future in self._jobs.values())
            for event, job_future in self._jobs.values():
                event.set()
            self._playback_changed.notify_all()
        for future in futures:
            future.cancel()

    def clear_cache(self) -> None:
        with self._playback_changed:
            self._cache.clear()

    def close(self) -> None:
        """Cancel work and wait for bounded decoder cleanup."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            playback_future = None
            if self._playback_job is not None:
                self._playback_job[2].set()
                playback_future = self._playback_job[3]
            futures = tuple(job_future for _, job_future in self._jobs.values())
            for event, job_future in self._jobs.values():
                event.set()
            self._playback_changed.notify_all()
        for future in futures:
            future.cancel()
        if playback_future is not None:
            playback_future.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._jobs.clear()
            self._playback_job = None
            self._cache.clear()
