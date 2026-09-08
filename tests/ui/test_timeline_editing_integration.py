from __future__ import annotations

import os
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from PySide6.QtGui import QKeySequence

from movie_maker.media import (
    MediaAnalysisErrorCode,
    MediaAnalysisFailure,
    MediaAnalysisResult,
    MediaLibrary,
)
from movie_maker.project import (
    CommandApplication,
    CommandExecutor,
    MediaKind,
    MediaReference,
    PlaybackRate,
    Project,
    ProjectCommand,
    ProjectFileStore,
    ProjectTime,
    TrackKind,
)
from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_controller import MockController


class _UnusedAnalyzer:
    def analyze(self, source_path: str | os.PathLike[str]) -> MediaAnalysisResult:
        return MediaAnalysisFailure(
            str(source_path),
            code=MediaAnalysisErrorCode.SOURCE_NOT_FOUND,
            message="not used",
        )


def _controller_for(project: Project, *clip_ids: str) -> MockController:
    identifiers = iter(clip_ids)
    library = MediaLibrary(CommandExecutor(project), _UnusedAnalyzer())
    return MockController(library, clip_id_factory=lambda: next(identifiers))


def _project_with_sources(source_directory: Path) -> Project:
    video_path = source_directory / "source video.mp4"
    photo_path = source_directory / "source photo.jpg"
    audio_path = source_directory / "source audio.wav"
    video_path.write_bytes(b"unchanged video source")
    photo_path.write_bytes(b"unchanged photo source")
    audio_path.write_bytes(b"unchanged audio source")
    media = (
        MediaReference(
            asset_id="video",
            name=video_path.name,
            source_path=str(video_path),
            kind=MediaKind.VIDEO,
            duration=ProjectTime.from_seconds(12),
            width=1920,
            height=1080,
        ),
        MediaReference(
            asset_id="photo",
            name=photo_path.name,
            source_path=str(photo_path),
            kind=MediaKind.PHOTO,
            duration=None,
            width=1920,
            height=1080,
        ),
        MediaReference(
            asset_id="audio",
            name=audio_path.name,
            source_path=str(audio_path),
            kind=MediaKind.AUDIO,
            duration=ProjectTime.from_seconds(20),
        ),
    )
    return replace(Project.empty(project_id="project-ui-w04"), media=media)


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


def test_controller_adds_real_clips_to_all_fixed_media_tracks(tmp_path: Path) -> None:
    controller = _controller_for(
        _project_with_sources(tmp_path),
        "clip-video",
        "clip-photo",
        "clip-music",
        "clip-narration",
    )

    controller.select_asset("video")
    assert controller.add_selected_to_timeline()
    controller.select_asset("photo")
    assert controller.add_selected_to_timeline()
    controller.select_asset("audio")
    assert controller.add_selected_to_timeline()
    controller.select_asset("audio")
    assert controller.add_selected_to_timeline(narration=True)

    project = controller.media_project
    assert tuple(clip.clip_id for clip in project.track(TrackKind.VISUAL).clips) == (
        "clip-video",
        "clip-photo",
    )
    assert project.track(TrackKind.VISUAL).clips[1].timeline_start == ProjectTime.from_seconds(12)
    assert project.track(TrackKind.MUSIC).clips[0].clip_id == "clip-music"
    assert project.track(TrackKind.NARRATION).clips[0].clip_id == "clip-narration"
    assert controller.state.total_duration_ms == 17_000
    assert controller.state.is_dirty


def test_controller_move_split_delete_and_shortcuts_use_real_history(qtbot) -> None:
    controller = MockController(clip_id_factory=lambda: "clip-split-back")
    controller.load_sample_project()
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    controller.select_clip("clip-beach")
    controller.seek(4_000)

    assert window._actions["split"].shortcut() == QKeySequence("Ctrl+B")
    window._actions["split"].trigger()

    assert len(controller.media_project.track(TrackKind.VISUAL).clips) == 4
    assert controller.state.selected_clip_id == "clip-split-back"
    assert controller.media_project.clip("clip-split-back").source_in == ProjectTime.from_seconds(4)

    window._actions["undo"].trigger()
    assert len(controller.media_project.track(TrackKind.VISUAL).clips) == 3
    window._actions["redo"].trigger()
    assert len(controller.media_project.track(TrackKind.VISUAL).clips) == 4

    controller.select_clip("clip-market")
    assert controller.move_selected_visual(-1)
    assert controller.move_selected_visual(-1)
    moved = controller.media_project.track(TrackKind.VISUAL).clips
    assert tuple(clip.clip_id for clip in moved[:2]) == ("clip-market", "clip-beach")
    window._actions["delete"].trigger()
    assert "clip-market" not in {
        clip.clip_id for clip in controller.media_project.track(TrackKind.VISUAL).clips
    }
    assert controller.state.selected_clip_id is None


def test_property_apply_is_one_real_command_and_save_keeps_history(tmp_path: Path) -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_clip("clip-market")
    before_count = controller.history_count

    assert controller.update_selected_clip(
        duration_ms=6_000,
        speed=1.5,
        volume=65,
        muted=True,
        source_in_ms=1_000,
        source_out_ms=7_000,
    )

    clip = controller.media_project.clip("clip-market")
    assert clip.playback_rate == PlaybackRate(3, 2)
    assert clip.duration == ProjectTime.from_seconds(4)
    assert controller.history_count == before_count + 1
    target = tmp_path / "edited.mmrproj"
    history_before_save = controller.history_count

    assert controller.save_project(str(target))
    assert controller.history_count == history_before_save
    assert ProjectFileStore().load(target) == controller.media_project
    assert not controller.state.is_dirty

    assert controller.undo()
    assert controller.state.is_dirty
    assert controller.redo()
    assert not controller.state.is_dirty


def test_failed_split_keeps_project_history_dirty_and_selection() -> None:
    controller = MockController(clip_id_factory=lambda: "clip-market")
    controller.load_sample_project()
    controller.select_clip("clip-beach")
    controller.seek(4_000)
    before = controller.media_project
    before_history = (controller.history_count, controller.history_position)
    before_selection = controller.state.selected_clip_id
    before_dirty = controller.state.is_dirty

    assert not controller.split_selected_clip()

    assert controller.media_project is before
    assert (controller.history_count, controller.history_position) == before_history
    assert controller.state.selected_clip_id == before_selection
    assert controller.state.is_dirty is before_dirty
    assert "이미 사용 중인 클립 ID" in controller.state.status_message


@dataclass(frozen=True)
class _ExplodingCommand:
    label: str

    def apply(self, project: Project) -> CommandApplication:
        raise RuntimeError("injected history failure")


@dataclass(frozen=True)
class _ChangeWithInverse:
    name: str
    inverse: ProjectCommand
    label: str = "실패 경계 준비"

    def apply(self, project: Project) -> CommandApplication:
        return CommandApplication(replace(project, name=self.name), self.inverse)


def test_controller_undo_failure_keeps_project_history_and_dirty() -> None:
    controller = MockController()
    assert controller._execute_core(
        _ChangeWithInverse("변경됨", _ExplodingCommand("실행 취소 실패")),
        "변경 성공",
    ) is not None
    before = controller.media_project
    before_history = (controller.history_count, controller.history_position)
    before_dirty = controller.state.is_dirty

    assert not controller.undo()

    assert controller.media_project is before
    assert (controller.history_count, controller.history_position) == before_history
    assert controller.state.is_dirty is before_dirty


def test_controller_redo_failure_keeps_project_history_and_dirty() -> None:
    controller = MockController()
    undo_command = _ChangeWithInverse(
        "제목 없음",
        _ExplodingCommand("다시 실행 실패"),
    )
    assert controller._execute_core(
        _ChangeWithInverse("변경됨", undo_command),
        "변경 성공",
    ) is not None
    assert controller.undo()
    before = controller.media_project
    before_history = (controller.history_count, controller.history_position)
    before_dirty = controller.state.is_dirty

    assert not controller.redo()

    assert controller.media_project is before
    assert (controller.history_count, controller.history_position) == before_history
    assert controller.state.is_dirty is before_dirty


def test_edit_save_open_round_trip_never_changes_source_files(tmp_path: Path) -> None:
    source_directory = tmp_path / "sources"
    source_directory.mkdir()
    project = _project_with_sources(source_directory)
    source_paths = tuple(Path(media.source_path) for media in project.media)
    before = {path: _fingerprint(path) for path in source_paths}
    controller = _controller_for(
        project,
        "clip-video",
        "clip-photo",
        "clip-music",
        "clip-narration",
        "clip-video-back",
    )
    for asset_id, narration in (
        ("video", False),
        ("photo", False),
        ("audio", False),
        ("audio", True),
    ):
        controller.select_asset(asset_id)
        assert controller.add_selected_to_timeline(narration=narration)
    controller.select_clip("clip-video")
    controller.seek(4_000)
    assert controller.split_selected_clip()
    controller.select_clip("clip-video-back")
    assert controller.update_selected_clip(
        duration_ms=8_000,
        speed=2.0,
        volume=100,
        muted=False,
        source_in_ms=4_000,
        source_out_ms=10_000,
    )
    edited = controller.media_project
    target = tmp_path / "edited project.mmrproj"

    assert controller.save_project(str(target))
    reader = MockController()
    assert reader.open_project(str(target))

    assert reader.media_project == edited
    assert reader.history_count == 0
    assert {path: _fingerprint(path) for path in source_paths} == before


def test_failed_save_does_not_change_timeline_history_or_dirty(tmp_path: Path) -> None:
    target = tmp_path / "existing.mmrproj"
    target.write_bytes(b"existing")

    def fail_replace(_source: str, _target: str) -> None:
        raise OSError("injected")

    controller = MockController(project_store=ProjectFileStore(replace_file=fail_replace))
    controller.load_sample_project()
    controller.select_clip("clip-market")
    assert controller.move_selected_visual(-1)
    before = controller.media_project
    before_history = (controller.history_count, controller.history_position)
    before_dirty = controller.state.is_dirty

    assert not controller.save_project(str(target))

    assert controller.media_project is before
    assert (controller.history_count, controller.history_position) == before_history
    assert controller.state.is_dirty is before_dirty
    assert target.read_bytes() == b"existing"
