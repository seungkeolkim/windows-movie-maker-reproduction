"""Priority-limited media work queue with deduplication and stale-result suppression."""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from threading import Condition, Event, Lock, Thread
from typing import Protocol

from movie_maker.media.cache import CacheKey, MediaCache


class JobPriority(IntEnum):
    VISIBLE_THUMBNAIL = 10
    VISIBLE_WAVEFORM = 20
    BACKGROUND_THUMBNAIL = 30
    PROXY = 40


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    CANCELLED = "cancelled"
    FAILED = "failed"


class JobFunction(Protocol):
    def __call__(self, cancel: Event) -> Path: ...


@dataclass(frozen=True, slots=True)
class JobResult:
    key: CacheKey
    project_generation: int
    state: JobState
    path: Path | None = None
    error: str | None = None


@dataclass(slots=True)
class JobHandle:
    key: CacheKey
    project_generation: int
    state: JobState
    cancel_event: Event
    result: JobResult | None = None
    done: Event = field(default_factory=Event)

    def cancel(self) -> None:
        self.cancel_event.set()

    def wait(self, timeout: float | None = None) -> JobResult | None:
        self.done.wait(timeout)
        return self.result


@dataclass(order=True, slots=True)
class _QueuedJob:
    priority: int
    sequence: int
    handle: JobHandle = field(compare=False)
    function: JobFunction = field(compare=False)
    callback: Callable[[JobResult], None] | None = field(compare=False)


class PriorityMediaQueue:
    """Run independent media jobs while keeping UI callbacks generation-safe."""

    def __init__(self, cache: MediaCache, *, workers: int = 2) -> None:
        if workers <= 0:
            raise ValueError("Background worker count must be positive.")
        self.cache = cache
        self._condition = Condition(Lock())
        self._jobs: list[_QueuedJob] = []
        self._handles: dict[str, JobHandle] = {}
        self._sequence = 0
        self._project_generation = 0
        self._closed = False
        self._threads = tuple(
            Thread(target=self._worker, name=f"media-cache-{index}", daemon=True)
            for index in range(workers)
        )
        for thread in self._threads:
            thread.start()

    @property
    def project_generation(self) -> int:
        with self._condition:
            return self._project_generation

    def replace_project(self) -> int:
        with self._condition:
            self._project_generation += 1
            for handle in self._handles.values():
                if handle.project_generation != self._project_generation:
                    handle.cancel()
            self._condition.notify_all()
            return self._project_generation

    def submit(
        self,
        key: CacheKey,
        priority: JobPriority,
        function: JobFunction,
        *,
        project_generation: int | None = None,
        callback: Callable[[JobResult], None] | None = None,
    ) -> JobHandle:
        generation = self.project_generation if project_generation is None else project_generation
        with self._condition:
            if self._closed:
                raise RuntimeError("The background media queue is closed.")
            existing = self._handles.get(key.digest)
            if existing is not None and existing.state in {JobState.QUEUED, JobState.RUNNING}:
                return existing
            cached = self.cache.get(key)
            if cached is not None:
                result = JobResult(key, generation, JobState.READY, cached)
                handle = JobHandle(key, generation, JobState.READY, Event(), result)
                handle.done.set()
                if callback is not None and generation == self._project_generation:
                    callback(result)
                return handle
            handle = JobHandle(key, generation, JobState.QUEUED, Event())
            self._sequence += 1
            heapq.heappush(
                self._jobs,
                _QueuedJob(int(priority), self._sequence, handle, function, callback),
            )
            self._handles[key.digest] = handle
            self._condition.notify()
            return handle

    def cancel(self, key: CacheKey) -> bool:
        with self._condition:
            handle = self._handles.get(key.digest)
            if handle is None or handle.done.is_set():
                return False
            handle.cancel()
            self._condition.notify_all()
            return True

    def state(self, key: CacheKey) -> JobState | None:
        with self._condition:
            handle = self._handles.get(key.digest)
            return handle.state if handle is not None else None

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._condition:
            self._closed = True
            for handle in self._handles.values():
                handle.cancel()
            self._condition.notify_all()
        for thread in self._threads:
            thread.join(timeout)
        self.cache.cleanup_partials()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._jobs and not self._closed:
                    self._condition.wait()
                if self._closed and not self._jobs:
                    return
                job = heapq.heappop(self._jobs)
                handle = job.handle
                if handle.cancel_event.is_set():
                    cancelled = True
                else:
                    cancelled = False
                    handle.state = JobState.RUNNING
            if cancelled:
                self._finish(job, JobState.CANCELLED)
                continue
            try:
                path = job.function(handle.cancel_event)
                if handle.cancel_event.is_set():
                    self._finish(job, JobState.CANCELLED)
                else:
                    self._finish(job, JobState.READY, path=path)
            except Exception as error:  # noqa: BLE001 - one job must not stop the queue
                state = JobState.CANCELLED if handle.cancel_event.is_set() else JobState.FAILED
                self._finish(job, state, error=str(error))

    def _finish(
        self,
        job: _QueuedJob,
        state: JobState,
        *,
        path: Path | None = None,
        error: str | None = None,
    ) -> None:
        callback: Callable[[JobResult], None] | None = None
        with self._condition:
            handle = job.handle
            result = JobResult(handle.key, handle.project_generation, state, path, error)
            handle.state = state
            handle.result = result
            handle.done.set()
            current = self._handles.get(handle.key.digest)
            if current is handle:
                self._handles.pop(handle.key.digest, None)
            if handle.project_generation == self._project_generation:
                callback = job.callback
        if callback is not None:
            callback(result)
