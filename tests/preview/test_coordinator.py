from __future__ import annotations

from dataclasses import replace
from threading import Event

from movie_maker.preview import (
    DecodedFrame,
    FrameTarget,
    PreviewDecodeCancelled,
    PreviewDecodeCoordinator,
)
from movie_maker.project import MediaKind, ProjectTime

PNG_BYTES = b"\x89PNG\r\n\x1a\nframe"


def _target(name: str) -> FrameTarget:
    return FrameTarget(
        project_id="project",
        clip_id=name,
        clip_label=name,
        asset_id=name,
        source_path=f"C:/{name}.mp4",
        media_kind=MediaKind.VIDEO,
        stream_index=0,
        source_time=ProjectTime.zero(),
        source_pts=0,
        time_base_numerator=1,
        time_base_denominator=1_000,
        start_pts=0,
    )


class _ControlledDecoder:
    def __init__(self) -> None:
        names = ("old", "new", "latest", "close")
        self.started = {name: Event() for name in names}
        self.release = {name: Event() for name in names}
        self.cancelled: dict[str, Event] = {}

    def decode(self, target, cancelled):
        self.cancelled[target.clip_id] = cancelled
        self.started[target.clip_id].set()
        self.release[target.clip_id].wait(timeout=2)
        if cancelled.is_set():
            return PreviewDecodeCancelled(target)
        return DecodedFrame(target, PNG_BYTES)


class _StreamingDecoder:
    def __init__(self) -> None:
        self.calls = 0
        self.started = Event()
        self.cancelled: Event | None = None

    def can_stream(self, target) -> bool:
        return True

    def decode(self, target, cancelled):
        raise AssertionError("single-frame decoding should not be used")

    def stream(self, target, cancelled, publish):
        self.calls += 1
        self.cancelled = cancelled
        self.started.set()
        assert publish(DecodedFrame(target, PNG_BYTES))
        later = replace(target, project_position=ProjectTime.from_milliseconds(100))
        publish(DecodedFrame(later, PNG_BYTES))
        cancelled.wait(timeout=2)
        return PreviewDecodeCancelled(target)


def test_only_latest_decode_result_is_published_and_old_work_is_cancelled() -> None:
    decoder = _ControlledDecoder()
    coordinator = PreviewDecodeCoordinator(decoder, max_workers=2)
    results = []
    delivered = Event()

    coordinator.request(_target("old"), results.append)
    assert decoder.started["old"].wait(timeout=1)
    coordinator.request(_target("new"), lambda result: (results.append(result), delivered.set()))
    assert decoder.started["new"].wait(timeout=1)
    decoder.release["old"].set()
    decoder.release["new"].set()

    assert delivered.wait(timeout=1)
    assert [result.target.clip_id for result in results] == ["new"]
    assert decoder.cancelled["old"].is_set()
    coordinator.close()


def test_close_cancels_active_decode_and_suppresses_callback() -> None:
    decoder = _ControlledDecoder()
    coordinator = PreviewDecodeCoordinator(decoder, max_workers=1)
    results = []

    coordinator.request(_target("close"), results.append)
    assert decoder.started["close"].wait(timeout=1)
    decoder.release["close"].set()
    coordinator.close()

    assert decoder.cancelled["close"].is_set()
    assert results == []


def test_replacing_a_queued_future_does_not_deadlock_its_done_callback() -> None:
    decoder = _ControlledDecoder()
    coordinator = PreviewDecodeCoordinator(decoder, max_workers=1)
    delivered = Event()
    results = []

    coordinator.request(_target("old"), results.append)
    assert decoder.started["old"].wait(timeout=1)
    coordinator.request(_target("new"), results.append)
    coordinator.request(_target("latest"), lambda result: (results.append(result), delivered.set()))
    decoder.release["old"].set()
    assert decoder.started["latest"].wait(timeout=1)
    decoder.release["latest"].set()

    assert delivered.wait(timeout=1)
    assert [result.target.clip_id for result in results] == ["latest"]
    coordinator.close()


def test_playback_reuses_one_stream_and_releases_next_frame_at_clock_position() -> None:
    decoder = _StreamingDecoder()
    coordinator = PreviewDecodeCoordinator(decoder)
    results = []
    first_delivered = Event()
    delivered = Event()
    first = replace(_target("old"), project_position=ProjectTime.zero())
    later = replace(first, project_position=ProjectTime.from_milliseconds(100))

    def receive(frame):
        results.append(frame)
        first_delivered.set()
        if len(results) == 2:
            delivered.set()

    assert coordinator.play(first, receive)
    assert decoder.started.wait(timeout=1)
    assert first_delivered.wait(timeout=1)
    assert len(results) == 1
    assert coordinator.play(later, receive)

    assert delivered.wait(timeout=1)
    assert decoder.calls == 1
    coordinator.stop_playback()
    assert decoder.cancelled is not None
    assert decoder.cancelled.is_set()
    coordinator.close()
