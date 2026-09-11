"""Latest-request-wins background coordination for audio graph decoding."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Protocol

from movie_maker.audio.decoder import (
    AudioDecodeCancelled,
    AudioDecodeErrorCode,
    AudioDecodeFailure,
    AudioDecodeResult,
    DecodedAudio,
    FfmpegAudioDecoder,
)
from movie_maker.audio.graph import AudioGraph

type AudioResultCallback = Callable[[DecodedAudio | AudioDecodeFailure], None]


class AudioDecoder(Protocol):
    def decode(self, graph: AudioGraph, cancelled: Event) -> AudioDecodeResult: ...


class AudioDecodeCoordinator:
    """Decode in a worker and publish only the newest graph generation."""

    def __init__(self, decoder: AudioDecoder | None = None) -> None:
        self._decoder = decoder or FfmpegAudioDecoder()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="movie-maker-audio")
        self._lock = Lock()
        self._generation = 0
        self._closed = False
        self._jobs: dict[int, tuple[Event, Future[AudioDecodeResult]]] = {}

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def request(self, graph: AudioGraph, callback: AudioResultCallback) -> int:
        with self._lock:
            if self._closed:
                raise RuntimeError("Audio decode coordinator is closed.")
            self._generation += 1
            generation = self._generation
            old_futures = tuple(future for _, future in self._jobs.values())
            for event, _ in self._jobs.values():
                event.set()
            cancelled = Event()
            future = self._executor.submit(self._decoder.decode, graph, cancelled)
            self._jobs[generation] = (cancelled, future)
        for old_future in old_futures:
            old_future.cancel()
        future.add_done_callback(
            lambda completed: self._complete(generation, graph, completed, callback)
        )
        return generation

    def _complete(
        self,
        generation: int,
        graph: AudioGraph,
        future: Future[AudioDecodeResult],
        callback: AudioResultCallback,
    ) -> None:
        try:
            result = future.result()
        except Exception as error:  # noqa: BLE001 - convert worker boundary failures
            with self._lock:
                current = generation == self._generation and not self._closed
                self._jobs.pop(generation, None)
            if current:
                callback(
                    AudioDecodeFailure(
                        graph,
                        AudioDecodeErrorCode.INTERNAL_ERROR,
                        "오디오 작업이 예기치 않게 실패했습니다. 다시 시도하세요.",
                        str(error),
                    )
                )
            return
        with self._lock:
            self._jobs.pop(generation, None)
            current = generation == self._generation and not self._closed
        if current and not isinstance(result, AudioDecodeCancelled):
            callback(result)

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
            futures = tuple(future for _, future in self._jobs.values())
            for event, _ in self._jobs.values():
                event.set()
        for future in futures:
            future.cancel()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            futures = tuple(future for _, future in self._jobs.values())
            for event, _ in self._jobs.values():
                event.set()
        for future in futures:
            future.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._jobs.clear()
