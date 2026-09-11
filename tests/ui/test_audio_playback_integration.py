from __future__ import annotations

from base64 import b64decode
from dataclasses import replace
from pathlib import Path
from threading import Event

from movie_maker.audio import (
    AudioDecodeCancelled,
    AudioDecodeCoordinator,
    AudioDecodeErrorCode,
    AudioDecodeFailure,
    DecodedAudio,
    build_audio_graph,
)
from movie_maker.media import MediaAnalysisErrorCode, MediaAnalysisFailure, MediaLibrary
from movie_maker.preview import DecodedFrame, PreviewDecodeCoordinator
from movie_maker.project import (
    AudioLevel,
    Canvas,
    Clip,
    CommandExecutor,
    FrameRate,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    Project,
    ProjectTime,
    TimelineTrack,
    TrackKind,
)
from movie_maker.ui.audio import AudioOutputError
from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_controller import MockController

PNG_BYTES = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YA"
    "AAAASUVORK5CYII="
)


class _UnusedAnalyzer:
    def analyze(self, source_path):
        return MediaAnalysisFailure(
            source_path,
            MediaAnalysisErrorCode.SOURCE_NOT_FOUND,
            "not used",
        )


class _FrameDecoder:
    def decode(self, target, cancelled):
        return DecodedFrame(target, PNG_BYTES)


class _AudioDecoder:
    def __init__(self) -> None:
        self.graphs = []

    def decode(self, graph, cancelled):
        self.graphs.append(graph)
        return DecodedAudio(graph, bytes(graph.output_frames * 4))


class _DelayedAudioDecoder:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def decode(self, graph, cancelled):
        self.started.set()
        self.release.wait(timeout=2)
        return DecodedAudio(graph, bytes(graph.output_frames * 4))


class _CancellableAudioDecoder:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.cancelled = None

    def decode(self, graph, cancelled):
        self.cancelled = cancelled
        self.started.set()
        self.release.wait(timeout=2)
        if cancelled.is_set():
            return AudioDecodeCancelled(graph)
        return DecodedAudio(graph, bytes(graph.output_frames * 4))


class _FailingAudioDecoder:
    def decode(self, graph, cancelled):
        return AudioDecodeFailure(
            graph,
            AudioDecodeErrorCode.SOURCE_NOT_FOUND,
            "오디오 원본 파일을 찾을 수 없습니다. 누락 미디어에서 다시 연결하세요.",
        )


class _AudioOutput:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.played: list[tuple[bytes, int, int]] = []
        self.stop_count = 0
        self.closed = False

    def play(self, pcm_bytes, *, sample_rate, channels):
        if self.fail:
            raise AudioOutputError("시험 출력 장치를 열 수 없습니다.")
        self.played.append((pcm_bytes, sample_rate, channels))

    def stop(self) -> None:
        self.stop_count += 1

    def close(self) -> None:
        self.closed = True


def _project(tmp_path: Path) -> Project:
    video_path = tmp_path / "video.mp4"
    music_path = tmp_path / "music.wav"
    video_path.write_bytes(b"video")
    music_path.write_bytes(b"music")
    video = MediaReference(
        "video",
        video_path.name,
        str(video_path),
        MediaKind.VIDEO,
        ProjectTime.from_seconds(3),
        640,
        360,
        0,
        (
            MediaStream(
                0,
                MediaStreamKind.VIDEO,
                "h264",
                MediaTimeBase(1, 30),
                average_frame_rate=FrameRate(30),
            ),
            MediaStream(
                1,
                MediaStreamKind.AUDIO,
                "aac",
                MediaTimeBase(1, 48_000),
                sample_rate=48_000,
            ),
        ),
    )
    music = MediaReference(
        "music",
        music_path.name,
        str(music_path),
        MediaKind.AUDIO,
        ProjectTime.from_seconds(3),
        primary_stream_index=0,
        streams=(
            MediaStream(
                0,
                MediaStreamKind.AUDIO,
                "pcm_s16le",
                MediaTimeBase(1, 32_000),
                sample_rate=32_000,
            ),
        ),
    )
    visual_clip = Clip(
        "video-clip",
        TrackKind.VISUAL,
        video.asset_id,
        "video",
        ProjectTime.zero(),
        ProjectTime.from_seconds(3),
        source_out=ProjectTime.from_seconds(3),
    )
    music_clip = Clip(
        "music-clip",
        TrackKind.MUSIC,
        music.asset_id,
        "music",
        ProjectTime.from_milliseconds(500),
        ProjectTime.from_seconds(2),
        source_in=ProjectTime.from_milliseconds(250),
        source_out=ProjectTime.from_milliseconds(2_250),
    )
    empty = Project.empty(project_id="audio-ui")
    return replace(
        empty,
        canvas=Canvas(640, 360, video.asset_id),
        media=(video, music),
        tracks=(
            TimelineTrack(TrackKind.VISUAL, (visual_clip,)),
            TimelineTrack(TrackKind.MUSIC, (music_clip,)),
            empty.track(TrackKind.NARRATION),
            empty.track(TrackKind.TEXT),
        ),
    )


def _controller(project: Project) -> MockController:
    return MockController(MediaLibrary(CommandExecutor(project), _UnusedAnalyzer()))


def _window(controller, decoder, output):
    return MainWindow(
        controller,
        preview_coordinator=PreviewDecodeCoordinator(_FrameDecoder()),
        audio_coordinator=AudioDecodeCoordinator(decoder),
        audio_output=output,
    )


def test_play_pause_and_preview_mute_control_real_injected_audio_without_project_edits(
    qtbot, tmp_path
) -> None:
    controller = _controller(_project(tmp_path))
    decoder = _AudioDecoder()
    output = _AudioOutput()
    window = _window(controller, decoder, output)
    qtbot.addWidget(window)
    window.show()
    before = (controller.media_project, controller.history_count, controller.state.is_dirty)

    window._actions["play_pause"].trigger()
    qtbot.waitUntil(lambda: bool(output.played), timeout=2_000)

    assert decoder.graphs[0].start == ProjectTime.zero()
    assert output.played[0][1:] == (48_000, 2)
    controller.toggle_preview_mute()
    assert output.stop_count > 0
    assert (controller.media_project, controller.history_count, controller.state.is_dirty) == before
    controller.toggle_preview_mute()
    qtbot.waitUntil(lambda: len(output.played) >= 2, timeout=2_000)
    window._actions["play_pause"].trigger()
    assert not controller.state.is_playing
    assert output.stop_count > 1

    window.close()
    assert output.closed


def test_video_and_music_property_panels_commit_audio_values_and_save_round_trip(
    tmp_path: Path,
) -> None:
    controller = _controller(_project(tmp_path))

    controller.select_clip("video-clip")
    assert controller.update_selected_clip(
        duration_ms=3_000,
        speed=1.0,
        volume=35,
        muted=True,
        source_in_ms=0,
        source_out_ms=3_000,
    )
    controller.select_clip("music-clip")
    assert controller.update_selected_audio(
        start_ms=750,
        source_in_ms=500,
        source_out_ms=2_500,
        volume=60,
        muted=False,
        fade_in_ms=0,
        fade_out_ms=0,
        ducking="꺼짐",
    )

    assert controller.history_count == 2
    video = controller.media_project.clip("video-clip")
    music = controller.media_project.clip("music-clip")
    assert (video.audio_level, video.audio_muted) == (AudioLevel(35), True)
    assert (music.audio_level, music.audio_muted) == (AudioLevel(60), False)
    assert (music.timeline_start, music.source_in, music.source_out) == (
        ProjectTime.from_milliseconds(750),
        ProjectTime.from_milliseconds(500),
        ProjectTime.from_milliseconds(2_500),
    )
    graph = build_audio_graph(controller.media_project)
    assert [source.level.percent for source in graph.sources] == [35, 60]

    target = tmp_path / "audio-project.mmrproj"
    assert controller.save_project(str(target))
    reopened = MockController()
    assert reopened.open_project(str(target))
    assert reopened.media_project.clip("video-clip").audio_level == AudioLevel(35)
    assert reopened.media_project.clip("music-clip").audio_level == AudioLevel(60)


def test_delayed_audio_is_trimmed_to_the_shared_monotonic_playback_position(
    qtbot, tmp_path
) -> None:
    controller = _controller(_project(tmp_path))
    decoder = _DelayedAudioDecoder()
    output = _AudioOutput()
    window = _window(controller, decoder, output)
    qtbot.addWidget(window)
    window.show()

    window._actions["play_pause"].trigger()
    assert decoder.started.wait(timeout=1)
    window._preview_timer.stop()
    controller.advance_playback(500)
    window._preview_timer.stop()
    decoder.release.set()
    qtbot.waitUntil(lambda: bool(output.played), timeout=2_000)

    expected_frames = 3 * 48_000 - 500 * 48
    assert len(output.played[0][0]) == expected_frames * 4


def test_project_edit_cancels_stale_audio_and_builds_from_the_new_project(
    qtbot, tmp_path
) -> None:
    controller = _controller(_project(tmp_path))
    controller.select_clip("video-clip")
    decoder = _CancellableAudioDecoder()
    output = _AudioOutput()
    window = _window(controller, decoder, output)
    qtbot.addWidget(window)
    window.show()

    window._actions["play_pause"].trigger()
    assert decoder.started.wait(timeout=1)
    assert controller.update_selected_clip(
        duration_ms=3_000,
        speed=1.0,
        volume=25,
        muted=False,
        source_in_ms=0,
        source_out_ms=3_000,
    )

    assert decoder.cancelled is not None
    assert decoder.cancelled.is_set()
    assert controller.media_project.clip("video-clip").audio_level == AudioLevel(25)
    decoder.release.set()


def test_seek_pauses_and_cancels_inflight_audio_without_publishing_old_samples(
    qtbot, tmp_path
) -> None:
    controller = _controller(_project(tmp_path))
    decoder = _CancellableAudioDecoder()
    output = _AudioOutput()
    window = _window(controller, decoder, output)
    qtbot.addWidget(window)
    window.show()

    window._actions["play_pause"].trigger()
    assert decoder.started.wait(timeout=1)
    controller.seek(1_000)

    assert decoder.cancelled is not None
    assert decoder.cancelled.is_set()
    assert not controller.state.is_playing
    decoder.release.set()
    qtbot.wait(50)
    assert output.played == []


def test_audio_output_device_failure_is_distinct_and_keeps_project_unchanged(
    qtbot, tmp_path
) -> None:
    controller = _controller(_project(tmp_path))
    before = (controller.media_project, controller.history_count, controller.state.is_dirty)
    window = _window(controller, _AudioDecoder(), _AudioOutput(fail=True))
    qtbot.addWidget(window)
    window.show()

    window._actions["play_pause"].trigger()
    qtbot.waitUntil(
        lambda: "오디오 출력 장치 오류" in controller.state.status_message,
        timeout=2_000,
    )

    assert "시험 출력 장치" in controller.state.status_message
    assert (controller.media_project, controller.history_count, controller.state.is_dirty) == before


def test_audio_decode_failure_gives_next_action_without_changing_project_state(
    qtbot, tmp_path
) -> None:
    controller = _controller(_project(tmp_path))
    before = (controller.media_project, controller.history_count, controller.state.is_dirty)
    window = _window(controller, _FailingAudioDecoder(), _AudioOutput())
    qtbot.addWidget(window)
    window.show()

    window._actions["play_pause"].trigger()
    qtbot.waitUntil(
        lambda: "누락 미디어에서 다시 연결" in controller.state.status_message,
        timeout=2_000,
    )

    assert (controller.media_project, controller.history_count, controller.state.is_dirty) == before
