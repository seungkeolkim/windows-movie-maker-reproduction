"""Atomic project commands and in-memory session history."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from movie_maker.project.model import (
    Clip,
    MediaReference,
    Project,
    ProjectValidationError,
    TimelineTrack,
    TrackKind,
)
from movie_maker.project.time import ProjectTime


class CommandError(RuntimeError):
    """Base class for failures at the project command boundary."""


class CommandRejected(CommandError):
    """Raised when a command is not valid for the current project state."""


class CommandExecutionError(CommandError):
    """Raised when a command fails unexpectedly before it can be committed."""


class CommandContractError(CommandError):
    """Raised when a command returns a result that violates the executor contract."""


class HistoryUnavailable(CommandError):
    """Raised when undo or redo has no command at the requested position."""


class ProjectCommand(Protocol):
    """A pure operation that produces a new project and its inverse command."""

    @property
    def label(self) -> str:
        """Return the user-facing action name stored in history."""

    def apply(self, project: Project) -> CommandApplication:
        """Return a validated next project without mutating the input project."""


@dataclass(frozen=True, slots=True)
class CommandApplication:
    """The uncommitted result of applying a project command."""

    project: Project
    inverse: ProjectCommand

    def __post_init__(self) -> None:
        if not isinstance(self.project, Project):
            raise TypeError("A command result must contain a Project value.")
        _command_label(self.inverse)


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """A successful command and the minimal command that reverses it."""

    label: str
    command: ProjectCommand
    inverse: ProjectCommand


def _command_label(command: ProjectCommand) -> str:
    try:
        label = command.label
        apply = command.apply
    except (AttributeError, TypeError) as error:
        raise CommandContractError("Project commands require label and apply members.") from error
    if not isinstance(label, str) or not label.strip() or not callable(apply):
        raise CommandContractError("Project commands require a non-blank label and callable apply.")
    return label


def _replace_track(project: Project, kind: TrackKind, clips: tuple[Clip, ...]) -> Project:
    try:
        replacement = TimelineTrack(kind, clips)
        tracks = tuple(
            replacement if track.kind is replacement.kind else track for track in project.tracks
        )
        return replace(project, tracks=tracks)
    except ProjectValidationError as error:
        raise CommandRejected(str(error)) from error


def _validate_index(index: int, *, length: int, allow_end: bool) -> None:
    if type(index) is not int:
        raise CommandRejected("Collection index must be an integer.")
    maximum = length if allow_end else length - 1
    if index < 0 or index > maximum:
        raise CommandRejected("Collection index is outside the valid range.")


@dataclass(frozen=True, slots=True)
class RenameProject:
    """Change the persistent project display name."""

    name: str

    @property
    def label(self) -> str:
        return "프로젝트 이름 변경"

    def apply(self, project: Project) -> CommandApplication:
        if self.name == project.name:
            raise CommandRejected("The project already has that name.")
        try:
            next_project = replace(project, name=self.name)
        except ProjectValidationError as error:
            raise CommandRejected(str(error)) from error
        return CommandApplication(next_project, RenameProject(project.name))


@dataclass(frozen=True, slots=True)
class InsertMediaReference:
    """Insert one source-media reference without reading or changing its file."""

    media: MediaReference
    index: int | None = None
    restore_canvas_reference: bool = False

    @property
    def label(self) -> str:
        return "미디어 참조 추가"

    def apply(self, project: Project) -> CommandApplication:
        if any(existing.asset_id == self.media.asset_id for existing in project.media):
            raise CommandRejected("A media reference with that identifier already exists.")

        index = len(project.media) if self.index is None else self.index
        _validate_index(index, length=len(project.media), allow_end=True)
        media = (*project.media[:index], self.media, *project.media[index:])
        canvas = project.canvas
        if type(self.restore_canvas_reference) is not bool:
            raise CommandRejected("restore_canvas_reference must be a boolean.")
        try:
            if self.restore_canvas_reference:
                if canvas.reference_asset_id is not None:
                    raise CommandRejected("The project canvas already has reference media.")
                canvas = replace(canvas, reference_asset_id=self.media.asset_id)
            next_project = replace(project, media=media, canvas=canvas)
        except ProjectValidationError as error:
            raise CommandRejected(str(error)) from error
        inverse = RemoveMediaReference(self.media.asset_id, expected=self.media)
        return CommandApplication(next_project, inverse)


@dataclass(frozen=True, slots=True)
class RemoveMediaReference:
    """Remove unused source media while preserving the original file."""

    asset_id: str
    expected: MediaReference | None = None

    @property
    def label(self) -> str:
        return "미디어 참조 제거"

    def apply(self, project: Project) -> CommandApplication:
        try:
            index = next(
                index
                for index, media in enumerate(project.media)
                if media.asset_id == self.asset_id
            )
        except StopIteration as error:
            raise CommandRejected("The media reference does not exist.") from error

        media = project.media[index]
        if self.expected is not None and media != self.expected:
            raise CommandRejected("The media reference changed since the command was prepared.")
        if any(clip.asset_id == self.asset_id for track in project.tracks for clip in track.clips):
            raise CommandRejected("Media that is used by timeline clips cannot be removed alone.")

        was_canvas_reference = project.canvas.reference_asset_id == self.asset_id
        canvas = project.canvas
        if was_canvas_reference:
            canvas = replace(canvas, reference_asset_id=None)
        try:
            next_project = replace(
                project,
                media=(*project.media[:index], *project.media[index + 1 :]),
                canvas=canvas,
            )
        except ProjectValidationError as error:
            raise CommandRejected(str(error)) from error
        inverse = InsertMediaReference(
            media,
            index=index,
            restore_canvas_reference=was_canvas_reference,
        )
        return CommandApplication(next_project, inverse)


@dataclass(frozen=True, slots=True)
class InsertClip:
    """Insert a clip and maintain the fixed track's ordering invariants."""

    clip: Clip
    index: int | None = None

    @property
    def label(self) -> str:
        return "클립 추가"

    def apply(self, project: Project) -> CommandApplication:
        if any(
            existing.clip_id == self.clip.clip_id
            for track in project.tracks
            for existing in track.clips
        ):
            raise CommandRejected("A clip with that identifier already exists.")

        track = project.track(self.clip.track)
        if self.index is None:
            if track.kind is TrackKind.VISUAL:
                index = len(track.clips)
            else:
                index = next(
                    (
                        current
                        for current, existing in enumerate(track.clips)
                        if existing.timeline_start > self.clip.timeline_start
                    ),
                    len(track.clips),
                )
        else:
            index = self.index
        _validate_index(index, length=len(track.clips), allow_end=True)

        clip = self.clip
        following = track.clips[index:]
        if track.kind is TrackKind.VISUAL:
            expected_start = (
                track.clips[index - 1].timeline_end if index > 0 else ProjectTime.zero()
            )
            clip = replace(clip, timeline_start=expected_start)
            following = tuple(
                replace(existing, timeline_start=existing.timeline_start + clip.duration)
                for existing in following
            )

        clips = (*track.clips[:index], clip, *following)
        next_project = _replace_track(project, track.kind, clips)
        return CommandApplication(
            next_project,
            RemoveClip(clip.clip_id, expected=clip),
        )


@dataclass(frozen=True, slots=True)
class RemoveClip:
    """Remove one clip and close a gap in the sequential visual track."""

    clip_id: str
    expected: Clip | None = None

    @property
    def label(self) -> str:
        return "클립 제거"

    def apply(self, project: Project) -> CommandApplication:
        located: tuple[TimelineTrack, int, Clip] | None = None
        for track in project.tracks:
            for index, clip in enumerate(track.clips):
                if clip.clip_id == self.clip_id:
                    located = (track, index, clip)
                    break
            if located is not None:
                break
        if located is None:
            raise CommandRejected("The clip does not exist.")

        track, index, clip = located
        if self.expected is not None and clip != self.expected:
            raise CommandRejected("The clip changed since the command was prepared.")

        following = track.clips[index + 1 :]
        if track.kind is TrackKind.VISUAL:
            following = tuple(
                replace(existing, timeline_start=existing.timeline_start - clip.duration)
                for existing in following
            )
        clips = (*track.clips[:index], *following)
        next_project = _replace_track(project, track.kind, clips)
        return CommandApplication(next_project, InsertClip(clip, index=index))


class CommandExecutor:
    """Commit successful immutable project transitions and manage session history."""

    def __init__(self, project: Project) -> None:
        if not isinstance(project, Project):
            raise TypeError("CommandExecutor requires a Project value.")
        self._project = project
        self._history: tuple[HistoryEntry, ...] = ()
        self._position = 0

    @property
    def project(self) -> Project:
        return self._project

    @property
    def history(self) -> tuple[HistoryEntry, ...]:
        return self._history

    @property
    def history_position(self) -> int:
        return self._position

    @property
    def history_count(self) -> int:
        return len(self._history)

    @property
    def can_undo(self) -> bool:
        return self._position > 0

    @property
    def can_redo(self) -> bool:
        return self._position < len(self._history)

    @property
    def undo_label(self) -> str | None:
        return self._history[self._position - 1].label if self.can_undo else None

    @property
    def redo_label(self) -> str | None:
        return self._history[self._position].label if self.can_redo else None

    def execute(self, command: ProjectCommand) -> Project:
        """Apply and commit a new command, truncating any abandoned redo branch."""

        label = _command_label(command)
        application = self._apply(command, operation="execute")
        history = (
            *self._history[: self._position],
            HistoryEntry(label, command, application.inverse),
        )
        self._project, self._history, self._position = (
            application.project,
            history,
            len(history),
        )
        return self._project

    def undo(self) -> Project:
        """Apply the inverse at the current history position."""

        if not self.can_undo:
            raise HistoryUnavailable("There is no project command to undo.")
        entry_index = self._position - 1
        entry = self._history[entry_index]
        application = self._apply(entry.inverse, operation="undo")
        history = (
            *self._history[:entry_index],
            HistoryEntry(entry.label, application.inverse, entry.inverse),
            *self._history[entry_index + 1 :],
        )
        self._project, self._history, self._position = (
            application.project,
            history,
            entry_index,
        )
        return self._project

    def redo(self) -> Project:
        """Reapply the command at the current history position."""

        if not self.can_redo:
            raise HistoryUnavailable("There is no project command to redo.")
        entry_index = self._position
        entry = self._history[entry_index]
        application = self._apply(entry.command, operation="redo")
        history = (
            *self._history[:entry_index],
            HistoryEntry(entry.label, entry.command, application.inverse),
            *self._history[entry_index + 1 :],
        )
        self._project, self._history, self._position = (
            application.project,
            history,
            entry_index + 1,
        )
        return self._project

    def _apply(self, command: ProjectCommand, *, operation: str) -> CommandApplication:
        current = self._project
        label = _command_label(command)
        try:
            application = command.apply(current)
        except CommandError:
            raise
        except Exception as error:
            raise CommandExecutionError(f"Failed to {operation} command: {label}.") from error

        if not isinstance(application, CommandApplication):
            raise CommandContractError("A project command returned an invalid application result.")
        if application.project.project_id != current.project_id:
            raise CommandContractError("A project command cannot replace the project identity.")
        if application.project.schema_version != current.schema_version:
            raise CommandContractError("A project command cannot change the schema version.")
        if application.project == current:
            raise CommandContractError("A successful project command must change the project.")
        return application
