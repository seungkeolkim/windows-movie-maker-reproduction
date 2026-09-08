from __future__ import annotations

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
