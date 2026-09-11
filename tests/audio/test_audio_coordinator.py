from threading import Event

from movie_maker.audio import (
    AudioDecodeCancelled,
    AudioDecodeCoordinator,
    AudioGraph,
    DecodedAudio,
)
from movie_maker.project import ProjectTime


def _graph(start_ms: int) -> AudioGraph:
    return AudioGraph(
        "project",
        ProjectTime.from_milliseconds(start_ms),
        ProjectTime.from_milliseconds(1),
        (),
    )


class _ControlledDecoder:
    def __init__(self) -> None:
        self.started = {value: Event() for value in (0, 1, 2)}
        self.release = {value: Event() for value in (0, 1, 2)}
        self.cancelled: dict[int, Event] = {}

    def decode(self, graph, cancelled):
        key = graph.start.to_milliseconds()
        self.cancelled[key] = cancelled
        self.started[key].set()
        self.release[key].wait(timeout=2)
        if cancelled.is_set():
            return AudioDecodeCancelled(graph)
        return DecodedAudio(graph, bytes(graph.output_frames * 4))


def test_only_latest_audio_result_is_published_and_old_work_is_cancelled() -> None:
    decoder = _ControlledDecoder()
    coordinator = AudioDecodeCoordinator(decoder)
    delivered = Event()
    results = []

    coordinator.request(_graph(0), results.append)
    assert decoder.started[0].wait(timeout=1)
    coordinator.request(_graph(1), results.append)
    coordinator.request(_graph(2), lambda result: (results.append(result), delivered.set()))
    decoder.release[0].set()
    assert decoder.started[2].wait(timeout=1)
    decoder.release[2].set()

    assert delivered.wait(timeout=1)
    assert [result.graph.start.to_milliseconds() for result in results] == [2]
    assert decoder.cancelled[0].is_set()
    coordinator.close()


def test_close_cancels_active_audio_and_suppresses_callback() -> None:
    decoder = _ControlledDecoder()
    coordinator = AudioDecodeCoordinator(decoder)
    results = []

    coordinator.request(_graph(0), results.append)
    assert decoder.started[0].wait(timeout=1)
    decoder.release[0].set()
    coordinator.close()

    assert decoder.cancelled[0].is_set()
    assert results == []
