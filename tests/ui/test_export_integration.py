from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton

from movie_maker.exporting import (
    ExportCancelled,
    ExportCoordinator,
    ExportErrorCode,
    ExportFailed,
    ExportProgress,
    ExportProgressStage,
    ExportSucceeded,
    ExportVerification,
)
from movie_maker.media import FfprobeAnalyzer, MediaLibrary
from movie_maker.project import (
    Canvas,
    Clip,
    CommandExecutor,
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
from movie_maker.ui.mock_model import ExportState


def _export_project(video_path: str, photo_path: str, music_path: str) -> Project:
    video = MediaReference(
        "video",
        "video.mp4",
        video_path,
        MediaKind.VIDEO,
        ProjectTime.from_seconds(6),
        640,
        480,
        0,
        (
            MediaStream(0, MediaStreamKind.VIDEO, "h264", MediaTimeBase(1, 30)),
            MediaStream(
                1,
                MediaStreamKind.AUDIO,
                "aac",
                MediaTimeBase(1, 48_000),
                sample_rate=48_000,
            ),
        ),
    )
    photo = MediaReference(
        "photo",
        "photo.png",
        photo_path,
        MediaKind.PHOTO,
        None,
        800,
        600,
        0,
        (MediaStream(0, MediaStreamKind.VIDEO, "png", MediaTimeBase(1, 25)),),
    )
    music = MediaReference(
        "music",
        "music.wav",
        music_path,
        MediaKind.AUDIO,
        ProjectTime.from_seconds(5),
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
    empty = Project.empty(project_id="export-ui-project")
    return replace(
        empty,
        canvas=Canvas(640, 480, video.asset_id),
        media=(video, photo, music),
        tracks=(
            TimelineTrack(
                TrackKind.VISUAL,
                (
                    Clip(
                        "video-clip",
                        TrackKind.VISUAL,
                        video.asset_id,
                        "video",
                        ProjectTime.zero(),
                        ProjectTime.from_seconds(2),
                        source_out=ProjectTime.from_seconds(2),
                    ),
                    Clip(
                        "photo-clip",
                        TrackKind.VISUAL,
                        photo.asset_id,
                        "photo",
                        ProjectTime.from_seconds(2),
                        ProjectTime.from_seconds(1),
                    ),
                ),
            ),
            TimelineTrack(
                TrackKind.MUSIC,
                (
                    Clip(
                        "music-clip",
                        TrackKind.MUSIC,
                        music.asset_id,
                        "music",
                        ProjectTime.zero(),
                        ProjectTime.from_seconds(2),
                        source_out=ProjectTime.from_seconds(2),
                    ),
                ),
            ),
            empty.track(TrackKind.NARRATION),
            empty.track(TrackKind.TEXT),
        ),
    )


class _ControlledRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.started = Event()
        self.release = Event()
        self.plan = None
        self.cancelled: Event | None = None
        self.start_count = 0

    def run(self, plan, cancelled: Event, progress_callback):
        self.plan = plan
        self.cancelled = cancelled
        self.start_count += 1
        self.started.set()
        progress_callback(
            ExportProgress(plan, 25, 1.0, 3.0, ExportProgressStage.ENCODING)
        )
        self.release.wait(2.0)
        if cancelled.is_set():
            return ExportCancelled(plan)
        if self.fail:
            return ExportFailed(
                plan,
                ExportErrorCode.PROCESS_FAILED,
                "FFmpeg가 동영상 출력을 완료하지 못했습니다.",
                "limited diagnostics",
                1,
            )
        progress_callback(
            ExportProgress(plan, 99, 2.0, 0.1, ExportProgressStage.VERIFYING)
        )
        return ExportSucceeded(
            plan,
            plan.target_path,
            ExportVerification(
                plan.width,
                plan.height,
                plan.duration,
                1234,
            ),
            2.5,
        )


def _window(tmp_path: Path, qtbot, runner: _ControlledRunner, *, opener=None) -> MainWindow:
    paths = []
    for name in ("video.mp4", "photo.png", "music.wav"):
        path = tmp_path / name
        path.write_bytes(b"source")
        paths.append(str(path))
    project = _export_project(*paths)
    library = MediaLibrary(CommandExecutor(project), FfprobeAnalyzer())
    controller = MockController(library)
    window = MainWindow(
        controller,
        export_coordinator=ExportCoordinator(runner),
        export_result_opener=opener,
    )
    qtbot.addWidget(window)
    window.show()
    return window


def test_real_export_bridge_uses_snapshot_allows_editing_and_opens_result(
    tmp_path: Path,
    qtbot,
) -> None:
    runner = _ControlledRunner()
    opened = []
    window = _window(
        tmp_path,
        qtbot,
        runner,
        opener=lambda path: opened.append(path) is None,
    )
    project_at_start = window.controller.media_project
    target = tmp_path / "result.mp4"

    window._begin_export(str(target), "원본 유지", "30 fps", "권장")
    assert runner.started.wait(1.0)
    qtbot.waitUntil(lambda: window.controller.state.export_progress == 25)
    assert window.controller.state.export_state is ExportState.RUNNING
    assert runner.plan.project is project_at_start
    assert window._actions["export"].isEnabled() is False
    assert window._actions["save"].isEnabled()
    assert window._actions["import"].isEnabled()
    assert window._actions["play_pause"].isEnabled()

    window.controller.rename_project("출력 중 편집한 이름")
    assert window.controller.media_project is not project_at_start
    assert runner.plan.project is project_at_start
    runner.release.set()
    qtbot.waitUntil(lambda: window.controller.state.export_state is ExportState.COMPLETE)

    assert window.export_progress.value() == 100
    assert "동영상 저장 완료" in window.export_stage.text()
    window._handle_export_result()
    assert opened == [str(target.resolve())]
    assert window.controller.state.export_state is ExportState.CLOSED


def test_duplicate_start_is_rejected_without_replacing_active_settings(
    tmp_path: Path,
    qtbot,
) -> None:
    runner = _ControlledRunner()
    window = _window(tmp_path, qtbot, runner)
    first_target = tmp_path / "first.mp4"

    window._begin_export(str(first_target), "720p", "30 fps", "권장")
    assert runner.started.wait(1.0)
    window._begin_export(str(tmp_path / "second.mp4"), "1080p", "30 fps", "권장")

    assert runner.start_count == 1
    assert window.controller.state.export_path == str(first_target)
    assert "이미" in window.controller.state.status_message
    runner.release.set()
    qtbot.waitUntil(lambda: window.controller.state.export_state is ExportState.COMPLETE)


def test_cancel_completes_cleanup_before_deferred_project_action(
    tmp_path: Path,
    qtbot,
) -> None:
    runner = _ControlledRunner()
    window = _window(tmp_path, qtbot, runner)
    continued = Event()
    window._begin_export(str(tmp_path / "cancel.mp4"), "원본 유지", "30 fps", "권장")
    assert runner.started.wait(1.0)

    window._cancel_export_then(continued.set)
    runner.release.set()
    qtbot.waitUntil(lambda: continued.is_set())

    assert runner.cancelled is not None and runner.cancelled.is_set()
    assert window.controller.state.export_state is ExportState.CANCELLED


def test_failure_keeps_settings_project_history_and_dirty_state(
    tmp_path: Path,
    qtbot,
) -> None:
    runner = _ControlledRunner(fail=True)
    window = _window(tmp_path, qtbot, runner)
    project_before = window.controller.media_project
    history_before = window.controller.history_position
    dirty_before = window.controller.state.is_dirty
    target = tmp_path / "failed.mp4"

    window._begin_export(str(target), "1080p", "30 fps", "권장")
    assert runner.started.wait(1.0)
    runner.release.set()
    qtbot.waitUntil(lambda: window.controller.state.export_state is ExportState.FAILED)

    assert window.controller.state.export_path == str(target)
    assert window.controller.state.export_preset == "1080p"
    assert window.controller.media_project is project_before
    assert window.controller.history_position == history_before
    assert window.controller.state.is_dirty is dirty_before
    assert window.controller.state.export_error_detail == "limited diagnostics"
    assert "limited diagnostics" not in window.export_stage.text()

    window._handle_export_result()
    settings = window._dialogs[-1]
    assert settings.findChild(QLineEdit, "E-EXPORT-PATH").text() == str(target)
    assert settings.findChild(QComboBox, "E-EXPORT-PRESET").currentText() == "1080p"


def test_existing_target_requires_confirmation_before_worker_starts(
    tmp_path: Path,
    qtbot,
) -> None:
    runner = _ControlledRunner()
    window = _window(tmp_path, qtbot, runner)
    target = tmp_path / "existing.mp4"
    target.write_bytes(b"existing-good")

    window._begin_export(str(target), "원본 유지", "30 fps", "권장")

    assert not runner.started.is_set()
    confirmation = window._dialogs[-1]
    button = next(
        child
        for child in confirmation.findChildren(QPushButton)
        if child.text() == "기존 파일 교체"
    )
    button.click()
    assert runner.started.wait(1.0)
    assert runner.plan.overwrite_existing
    runner.release.set()
    qtbot.waitUntil(lambda: window.controller.state.export_state is ExportState.COMPLETE)


def test_close_waits_for_export_cancellation_cleanup(tmp_path: Path, qtbot) -> None:
    runner = _ControlledRunner()
    window = _window(tmp_path, qtbot, runner)
    window._begin_export(str(tmp_path / "close.mp4"), "원본 유지", "30 fps", "권장")
    assert runner.started.wait(1.0)

    window.close()
    confirmation = window._dialogs[-1]
    button = next(
        child
        for child in confirmation.findChildren(QPushButton)
        if child.text() == "출력 취소 후 닫기"
    )
    button.click()
    assert window.isVisible()
    runner.release.set()
    qtbot.waitUntil(lambda: not window.isVisible())

    assert runner.cancelled is not None and runner.cancelled.is_set()
