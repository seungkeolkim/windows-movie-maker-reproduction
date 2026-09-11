from __future__ import annotations

from base64 import b64decode
from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtGui import QKeySequence

from movie_maker.media import MediaAnalysisErrorCode, MediaAnalysisFailure, MediaLibrary
from movie_maker.preview import (
    DecodedFrame,
    PreviewDecodeCoordinator,
    PreviewDecodeErrorCode,
    PreviewDecodeFailure,
    frame_at_project_time,
)
from movie_maker.project import (
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


class _SuccessfulDecoder:
    def __init__(self) -> None:
        self.targets = []

    def decode(self, target, cancelled):
        self.targets.append(target)
        return DecodedFrame(target, PNG_BYTES)


class _FailingDecoder:
    def decode(self, target, cancelled):
        return PreviewDecodeFailure(
            target,
            PreviewDecodeErrorCode.PROCESS_FAILED,
            "손상된 미디어입니다. 다른 파일을 사용하세요.",
        )


class _BlockingDecoder:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.cancelled = None

    def decode(self, target, cancelled):
        self.cancelled = cancelled
        self.started.set()
        self.release.wait(timeout=2)
        return DecodedFrame(target, PNG_BYTES)


def _project(tmp_path: Path) -> Project:
    source = tmp_path / "preview.mp4"
    source.write_bytes(b"source")
    stream = MediaStream(
        index=0,
        kind=MediaStreamKind.VIDEO,
        codec_name="h264",
        time_base=MediaTimeBase(1, 90_000),
        start_pts=9_000,
        duration_ts=270_000,
        average_frame_rate=FrameRate(30),
    )
    media = MediaReference(
        asset_id="video",
        name="preview.mp4",
        source_path=str(source),
        kind=MediaKind.VIDEO,
        duration=ProjectTime.from_seconds(3),
        width=640,
        height=360,
        primary_stream_index=0,
        streams=(stream,),
    )
    clip = Clip(
        clip_id="clip",
        track=TrackKind.VISUAL,
        asset_id="video",
        label="preview",
        timeline_start=ProjectTime.zero(),
        duration=ProjectTime.from_seconds(3),
        source_out=ProjectTime.from_seconds(3),
    )
    empty = Project.empty(project_id="preview-ui")
    tracks = tuple(
        TimelineTrack(track.kind, (clip,) if track.kind is TrackKind.VISUAL else ())
        for track in empty.tracks
    )
    return replace(
        empty,
        canvas=Canvas(width=640, height=360, reference_asset_id="video"),
        media=(media,),
        tracks=tracks,
    )


def _controller(project: Project, *, clip_id="split") -> MockController:
    library = MediaLibrary(CommandExecutor(project), _UnusedAnalyzer())
    return MockController(library, clip_id_factory=lambda: clip_id)


def test_qt_preview_renders_decoded_frame_and_space_controls_real_clock(qtbot, tmp_path) -> None:
    project = _project(tmp_path)
    controller = _controller(project)
    decoder = _SuccessfulDecoder()
    coordinator = PreviewDecodeCoordinator(decoder)
    window = MainWindow(controller, preview_coordinator=coordinator)
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(
        lambda: window.preview_canvas.pixmap() is not None
        and not window.preview_canvas.pixmap().isNull(),
        timeout=2_000,
    )
    assert window.preview_badge.text() == "실제 프레임·오디오"
    assert window._actions["play_pause"].shortcut() == QKeySequence("Space")
    before = (controller.media_project, controller.history_position, controller.state.is_dirty)

    window._actions["play_pause"].trigger()
    assert controller.state.is_playing
    window._actions["play_pause"].trigger()
    assert not controller.state.is_playing
    controller.seek(500)
    qtbot.waitUntil(lambda: decoder.targets[-1].source_time == ProjectTime.from_milliseconds(500))

    assert (controller.media_project, controller.history_position, controller.state.is_dirty) == before
    window.close()
    assert coordinator.closed


def test_decode_failure_shows_next_action_without_changing_project_state(qtbot, tmp_path) -> None:
    controller = _controller(_project(tmp_path))
    before = (controller.media_project, controller.history_position, controller.state.is_dirty)
    window = MainWindow(
        controller,
        preview_coordinator=PreviewDecodeCoordinator(_FailingDecoder()),
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: "다른 파일을 사용하세요" in window.preview_canvas.text())

    assert (controller.media_project, controller.history_position, controller.state.is_dirty) == before


def test_edit_undo_redo_and_save_open_keep_preview_source_mapping(tmp_path) -> None:
    controller = _controller(_project(tmp_path), clip_id="split-back")
    controller.select_clip("clip")
    controller.seek(1_000)
    before = frame_at_project_time(
        controller.media_project, controller.preview_position
    ).target

    assert controller.split_selected_clip()
    after_split = frame_at_project_time(
        controller.media_project, controller.preview_position
    ).target
    assert controller.undo()
    after_undo = frame_at_project_time(
        controller.media_project, controller.preview_position
    ).target
    assert controller.redo()
    after_redo = frame_at_project_time(
        controller.media_project, controller.preview_position
    ).target

    assert before is not None and after_split is not None
    assert after_undo is not None and after_redo is not None
    assert {target.source_time for target in (before, after_split, after_undo, after_redo)} == {
        ProjectTime.from_seconds(1)
    }

    path = tmp_path / "preview.mmrproj"
    assert controller.save_project(str(path))
    reopened = MockController()
    assert reopened.open_project(str(path))
    reopened.seek(1_000)
    reopened_target = frame_at_project_time(
        reopened.media_project, reopened.preview_position
    ).target
    assert reopened_target is not None
    assert reopened_target.source_time == after_redo.source_time


def test_new_project_cancels_decode_and_discards_its_late_frame(qtbot, tmp_path) -> None:
    controller = _controller(_project(tmp_path))
    decoder = _BlockingDecoder()
    coordinator = PreviewDecodeCoordinator(decoder)
    window = MainWindow(controller, preview_coordinator=coordinator)
    qtbot.addWidget(window)
    window.show()
    assert decoder.started.wait(timeout=1)

    controller.new_project()

    assert decoder.cancelled is not None
    assert decoder.cancelled.is_set()
    decoder.release.set()
    qtbot.waitUntil(lambda: "타임라인에 영상이나 사진을 추가" in window.preview_canvas.text())
    pixmap = window.preview_canvas.pixmap()
    assert pixmap is None or pixmap.isNull()
