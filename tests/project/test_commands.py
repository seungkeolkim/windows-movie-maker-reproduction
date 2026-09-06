from __future__ import annotations

import ast
from dataclasses import dataclass, fields, replace
from pathlib import Path

import pytest

import movie_maker.project
from movie_maker.project import (
    Canvas,
    Clip,
    CommandApplication,
    CommandContractError,
    CommandExecutionError,
    CommandExecutor,
    CommandRejected,
    HistoryEntry,
    HistoryUnavailable,
    InsertClip,
    InsertMediaReference,
    MediaKind,
    MediaReference,
    Project,
    ProjectCommand,
    ProjectTime,
    RemoveClip,
    RemoveMediaReference,
    RenameProject,
    TrackKind,
)


def _video() -> MediaReference:
    return MediaReference(
        asset_id="media-video",
        name="바다.mp4",
        source_path="D:/Media/바다.mp4",
        kind=MediaKind.VIDEO,
        duration=ProjectTime.from_seconds(20),
        width=1920,
        height=1080,
    )


def _audio() -> MediaReference:
    return MediaReference(
        asset_id="media-audio",
        name="음악.wav",
        source_path="D:/Media/음악.wav",
        kind=MediaKind.AUDIO,
        duration=ProjectTime.from_seconds(60),
    )


def _clip(
    clip_id: str,
    *,
    start: int,
    duration: int,
    track: TrackKind = TrackKind.VISUAL,
    asset_id: str = "media-video",
) -> Clip:
    return Clip(
        clip_id=clip_id,
        track=track,
        asset_id=asset_id,
        label=clip_id,
        timeline_start=ProjectTime.from_seconds(start),
        duration=ProjectTime.from_seconds(duration),
        source_in=ProjectTime.zero(),
        source_out=ProjectTime.from_seconds(duration),
    )


def test_execute_undo_and_redo_round_trip_project_value() -> None:
    initial = Project.empty(project_id="project-1")
    executor = CommandExecutor(initial)

    edited = executor.execute(RenameProject("여행"))

    assert edited.name == "여행"
    assert executor.history_position == 1
    assert executor.history_count == 1
    assert executor.undo_label == "프로젝트 이름 변경"
    assert executor.undo() == initial
    assert executor.can_redo
    assert executor.redo() == edited
    assert executor.history_position == 1


def test_new_command_after_undo_discards_redo_branch() -> None:
    executor = CommandExecutor(Project.empty(project_id="project-1"))
    executor.execute(RenameProject("첫 이름"))
    executor.execute(RenameProject("두 번째 이름"))
    executor.undo()

    executor.execute(RenameProject("분기 이름"))

    assert executor.project.name == "분기 이름"
    assert executor.history_count == 2
    assert executor.history_position == 2
    assert not executor.can_redo
    assert tuple(entry.label for entry in executor.history) == (
        "프로젝트 이름 변경",
        "프로젝트 이름 변경",
    )


def test_history_records_commands_and_inverses_without_project_snapshots() -> None:
    executor = CommandExecutor(Project.empty(project_id="project-1"))

    executor.execute(RenameProject("여행"))

    history_fields = fields(HistoryEntry)
    entry = executor.history[0]
    assert {field.name for field in history_fields} == {"label", "command", "inverse"}
    assert not any(isinstance(getattr(entry, field.name), Project) for field in history_fields)


def test_media_commands_restore_order_and_canvas_reference() -> None:
    initial = Project.empty(project_id="project-1")
    executor = CommandExecutor(initial)
    executor.execute(InsertMediaReference(_video()))
    with_canvas = replace(
        executor.project,
        canvas=Canvas(width=1920, height=1080, reference_asset_id="media-video"),
    )
    executor = CommandExecutor(with_canvas)

    removed = executor.execute(RemoveMediaReference("media-video"))

    assert removed.media == ()
    assert removed.canvas == Canvas(width=1920, height=1080)
    assert executor.undo() == with_canvas


def test_media_in_use_cannot_be_removed_by_reference_only_command() -> None:
    executor = CommandExecutor(Project.empty(project_id="project-1"))
    executor.execute(InsertMediaReference(_video()))
    executor.execute(InsertClip(_clip("clip-1", start=0, duration=5)))
    before = executor.project
    history = executor.history

    with pytest.raises(CommandRejected, match="used by timeline clips"):
        executor.execute(RemoveMediaReference("media-video"))

    assert executor.project is before
    assert executor.history == history
    assert executor.history_position == 2


def test_visual_clip_insert_and_remove_reflow_following_clips() -> None:
    executor = CommandExecutor(Project.empty(project_id="project-1"))
    executor.execute(InsertMediaReference(_video()))
    executor.execute(InsertClip(_clip("clip-a", start=0, duration=4)))
    executor.execute(InsertClip(_clip("clip-c", start=0, duration=5)))
    before_insert = executor.project

    inserted = executor.execute(InsertClip(_clip("clip-b", start=99, duration=3), index=1))

    visual = inserted.track(TrackKind.VISUAL).clips
    assert tuple(clip.clip_id for clip in visual) == ("clip-a", "clip-b", "clip-c")
    assert tuple(clip.timeline_start for clip in visual) == tuple(
        ProjectTime.from_seconds(value) for value in (0, 4, 7)
    )
    assert executor.undo() == before_insert
    assert executor.redo() == inserted

    removed = executor.execute(RemoveClip("clip-b"))
    assert tuple(clip.timeline_start for clip in removed.track(TrackKind.VISUAL).clips) == (
        ProjectTime.from_seconds(0),
        ProjectTime.from_seconds(4),
    )


def test_multiple_history_steps_round_trip_in_order() -> None:
    initial = Project.empty(project_id="project-1")
    executor = CommandExecutor(initial)
    states = [initial]
    for name in ("첫 이름", "두 번째 이름", "마지막 이름"):
        states.append(executor.execute(RenameProject(name)))

    assert [executor.undo() for _ in range(3)] == list(reversed(states[:-1]))
    assert [executor.redo() for _ in range(3)] == states[1:]


def test_absolute_track_insertion_uses_timeline_order_without_ripple() -> None:
    executor = CommandExecutor(Project.empty(project_id="project-1"))
    executor.execute(InsertMediaReference(_audio()))
    later = _clip(
        "music-later",
        start=10,
        duration=5,
        track=TrackKind.MUSIC,
        asset_id="media-audio",
    )
    earlier = replace(
        later,
        clip_id="music-earlier",
        timeline_start=ProjectTime.from_seconds(2),
    )

    executor.execute(InsertClip(later))
    result = executor.execute(InsertClip(earlier))

    assert tuple(clip.clip_id for clip in result.track(TrackKind.MUSIC).clips) == (
        "music-earlier",
        "music-later",
    )
    assert result.track(TrackKind.MUSIC).clips[1].timeline_start == ProjectTime.from_seconds(10)


@dataclass(frozen=True)
class _ExplodingCommand:
    label: str = "실패 명령"

    def apply(self, project: Project) -> CommandApplication:
        raise RuntimeError("simulated failure")


@dataclass(frozen=True)
class _ChangeWithInverse:
    name: str
    inverse: ProjectCommand
    label: str = "테스트 변경"

    def apply(self, project: Project) -> CommandApplication:
        return CommandApplication(replace(project, name=self.name), self.inverse)


@dataclass(frozen=True)
class _ChangeIdentity:
    label: str = "잘못된 명령"

    def apply(self, project: Project) -> CommandApplication:
        return CommandApplication(replace(project, project_id="other-project"), self)


def test_execute_failure_leaves_project_and_history_unchanged() -> None:
    executor = CommandExecutor(Project.empty(project_id="project-1"))
    executor.execute(RenameProject("안전한 이름"))
    before = executor.project
    history = executor.history
    position = executor.history_position

    with pytest.raises(CommandExecutionError) as caught:
        executor.execute(_ExplodingCommand())

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert executor.project is before
    assert executor.history == history
    assert executor.history_position == position


def test_invalid_command_result_leaves_project_and_history_unchanged() -> None:
    executor = CommandExecutor(Project.empty(project_id="project-1"))
    before = executor.project

    with pytest.raises(CommandContractError, match="identity"):
        executor.execute(_ChangeIdentity())

    assert executor.project is before
    assert executor.history == ()
    assert executor.history_position == 0


def test_undo_and_redo_failures_are_atomic() -> None:
    initial = Project.empty(project_id="project-1")
    executor = CommandExecutor(initial)
    executor.execute(_ChangeWithInverse("변경됨", _ExplodingCommand("실행 취소 실패")))
    edited = executor.project
    history = executor.history

    with pytest.raises(CommandExecutionError):
        executor.undo()

    assert executor.project is edited
    assert executor.history == history
    assert executor.history_position == 1

    undo_to_initial = _ChangeWithInverse("원래 이름", _ExplodingCommand("다시 실행 실패"))
    executor = CommandExecutor(initial)
    executor.execute(_ChangeWithInverse("변경됨", undo_to_initial))
    executor.undo()
    before_redo = executor.project
    history = executor.history

    with pytest.raises(CommandExecutionError):
        executor.redo()

    assert executor.project is before_redo
    assert executor.history == history
    assert executor.history_position == 0


def test_unavailable_history_actions_do_not_change_state() -> None:
    executor = CommandExecutor(Project.empty(project_id="project-1"))

    with pytest.raises(HistoryUnavailable):
        executor.undo()
    with pytest.raises(HistoryUnavailable):
        executor.redo()

    assert executor.history_position == 0


def test_project_core_does_not_import_ui_media_storage_or_database_modules() -> None:
    package_root = Path(movie_maker.project.__file__).parent
    forbidden = {"PySide6", "sqlite3", "duckdb", "ffmpeg", "movie_maker.ui"}
    imported: set[str] = set()

    for source_path in package_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)

    assert not any(
        module == forbidden_name or module.startswith(f"{forbidden_name}.")
        for module in imported
        for forbidden_name in forbidden
    )
