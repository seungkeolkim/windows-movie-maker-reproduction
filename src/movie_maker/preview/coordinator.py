"""Latest-request-wins background coordination for preview decoding."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Protocol

from movie_maker.preview.decoder import (
    DecodedFrame,
    FfmpegFrameDecoder,
    PreviewDecodeCancelled,
    PreviewDecodeErrorCode,
    PreviewDecodeFailure,
    PreviewDecodeResult,
)
from movie_maker.preview.timeline import FrameTarget

type PreviewResultCallback = Callable[[DecodedFrame | PreviewDecodeFailure], None]


class PreviewDecoder(Protocol):
    def decode(self, target: FrameTarget, cancelled: Event) -> PreviewDecodeResult: ...


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
        self._cache: OrderedDict[tuple[str, str, str, int, int], DecodedFrame] = OrderedDict()
        self._lock = Lock()
        self._generation = 0
        self._closed = False
        self._jobs: dict[int, tuple[Event, Future[PreviewDecodeResult]]] = {}

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

        with self._lock:
            self._generation += 1
            futures = tuple(job_future for _, job_future in self._jobs.values())
            for event, job_future in self._jobs.values():
                event.set()
        for future in futures:
            future.cancel()

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def close(self) -> None:
        """Cancel work and wait for bounded decoder cleanup."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            futures = tuple(job_future for _, job_future in self._jobs.values())
            for event, job_future in self._jobs.values():
                event.set()
        for future in futures:
            future.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._jobs.clear()
            self._cache.clear()
