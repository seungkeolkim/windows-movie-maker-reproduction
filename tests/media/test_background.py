from pathlib import Path
from threading import Event

from movie_maker.media import (
    CacheKey,
    CacheKind,
    JobPriority,
    JobState,
    MediaCache,
    PriorityMediaQueue,
)


def _key(tmp_path: Path, number: int) -> CacheKey:
    source = tmp_path / f"source-{number}.mp4"
    source.write_bytes(str(number).encode())
    return CacheKey.create(CacheKind.THUMBNAIL, source, {"number": number})


def test_queue_prioritizes_waiting_jobs_and_deduplicates(tmp_path: Path) -> None:
    queue = PriorityMediaQueue(MediaCache(tmp_path / "cache"), workers=1)
    blocker_started = Event()
    release = Event()
    order: list[str] = []

    def blocker(_cancel: Event) -> Path:
        blocker_started.set()
        release.wait(2)
        order.append("blocker")
        return tmp_path / "blocker"

    def job(name: str):
        def run(_cancel: Event) -> Path:
            order.append(name)
            return tmp_path / name

        return run

    queue.submit(_key(tmp_path, 0), JobPriority.VISIBLE_THUMBNAIL, blocker)
    assert blocker_started.wait(1)
    low = queue.submit(_key(tmp_path, 1), JobPriority.PROXY, job("low"))
    duplicate = queue.submit(low.key, JobPriority.VISIBLE_THUMBNAIL, job("duplicate"))
    high = queue.submit(_key(tmp_path, 2), JobPriority.VISIBLE_THUMBNAIL, job("high"))
    assert duplicate is low
    release.set()
    assert high.wait(2) is not None
    assert low.wait(2) is not None
    assert order == ["blocker", "high", "low"]
    queue.shutdown()


def test_queue_isolates_failure_and_suppresses_stale_callbacks(tmp_path: Path) -> None:
    queue = PriorityMediaQueue(MediaCache(tmp_path / "cache"), workers=1)
    blocker_started = Event()
    release = Event()
    callbacks = []

    def blocker(_cancel: Event) -> Path:
        blocker_started.set()
        release.wait(2)
        return tmp_path / "blocker"

    def fail(_cancel: Event) -> Path:
        raise RuntimeError("isolated failure")

    queue.submit(_key(tmp_path, 0), JobPriority.VISIBLE_THUMBNAIL, blocker)
    assert blocker_started.wait(1)
    failed = queue.submit(
        _key(tmp_path, 1),
        JobPriority.VISIBLE_THUMBNAIL,
        fail,
        callback=callbacks.append,
    )
    ready = queue.submit(
        _key(tmp_path, 2),
        JobPriority.PROXY,
        lambda _cancel: tmp_path / "ready",
        callback=callbacks.append,
    )
    queue.replace_project()
    release.set()
    assert failed.wait(2).state is JobState.CANCELLED
    assert ready.wait(2).state is JobState.CANCELLED
    assert callbacks == []

    current = queue.submit(
        _key(tmp_path, 3),
        JobPriority.VISIBLE_THUMBNAIL,
        lambda _cancel: tmp_path / "current",
        callback=callbacks.append,
    )
    assert current.wait(2).state is JobState.READY
    assert [item.state for item in callbacks] == [JobState.READY]
    queue.shutdown()


def test_one_failed_job_does_not_stop_following_job(tmp_path: Path) -> None:
    queue = PriorityMediaQueue(MediaCache(tmp_path / "cache"), workers=1)
    failed = queue.submit(
        _key(tmp_path, 1),
        JobPriority.VISIBLE_THUMBNAIL,
        lambda _cancel: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    ready = queue.submit(
        _key(tmp_path, 2),
        JobPriority.PROXY,
        lambda _cancel: tmp_path / "ready",
    )
    assert failed.wait(2).state is JobState.FAILED
    assert ready.wait(2).state is JobState.READY
    queue.shutdown()
