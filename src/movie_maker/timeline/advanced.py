"""Atomic commands for compatible multi-clip timeline editing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from movie_maker.project.commands import CommandApplication, CommandRejected
from movie_maker.project.model import Clip, Project, TrackKind
from movie_maker.project.time import ProjectTime
from movie_maker.timeline.editing import _replace_track


class TimelineEditErrorCode(str, Enum):
    """Stable failure categories exposed by advanced timeline commands."""

    EMPTY_SELECTION = "empty_selection"
    CLIP_NOT_FOUND = "clip_not_found"
    MIXED_TRACKS = "mixed_tracks"
    INCOMPATIBLE_SELECTION = "incompatible_selection"
    OUT_OF_RANGE = "out_of_range"
    ID_COLLISION = "id_collision"
    NO_CHANGE = "no_change"
    STALE_STATE = "stale_state"


class TimelineEditRejected(CommandRejected):
    """A rejected advanced edit with a machine-readable reason."""

    def __init__(self, code: TimelineEditErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _reject(code: TimelineEditErrorCode, message: str) -> TimelineEditRejected:
    return TimelineEditRejected(code, message)


def _validate_clip_ids(clip_ids: tuple[str, ...]) -> None:
    if not clip_ids:
        raise _reject(TimelineEditErrorCode.EMPTY_SELECTION, "선택한 클립이 없습니다.")
    if any(not isinstance(clip_id, str) or not clip_id.strip() for clip_id in clip_ids):
        raise _reject(
            TimelineEditErrorCode.INCOMPATIBLE_SELECTION,
            "클립 ID는 비어 있지 않은 문자열이어야 합니다.",
        )
    if len(set(clip_ids)) != len(clip_ids):
        raise _reject(
            TimelineEditErrorCode.INCOMPATIBLE_SELECTION,
            "선택에 같은 클립 ID가 두 번 들어 있습니다.",
        )


def _locate_group(
    project: Project,
    clip_ids: tuple[str, ...],
) -> tuple[TrackKind, tuple[tuple[int, Clip], ...]]:
    _validate_clip_ids(clip_ids)
    requested = set(clip_ids)
    located: list[tuple[TrackKind, int, Clip]] = []
    for track in project.tracks:
        located.extend(
            (track.kind, index, clip)
            for index, clip in enumerate(track.clips)
            if clip.clip_id in requested
        )
    found = {clip.clip_id for _, _, clip in located}
    missing = requested - found
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise _reject(
            TimelineEditErrorCode.CLIP_NOT_FOUND,
            f"타임라인에서 클립을 찾을 수 없습니다: {missing_text}",
        )
    kinds = {kind for kind, _, _ in located}
    if len(kinds) != 1:
        raise _reject(
            TimelineEditErrorCode.MIXED_TRACKS,
            "서로 다른 트랙의 클립은 한 그룹으로 편집할 수 없습니다.",
        )
    kind = located[0][0]
    ordered = tuple(
        (index, clip)
        for _, index, clip in sorted(located, key=lambda item: item[1])
    )
    return kind, ordered


def _ensure_new_clip_id(project: Project, clip_id: str) -> None:
    if not isinstance(clip_id, str) or not clip_id.strip():
        raise _reject(
            TimelineEditErrorCode.INCOMPATIBLE_SELECTION,
            "새 클립 ID는 비어 있지 않은 문자열이어야 합니다.",
        )
    try:
        project.clip(clip_id)
    except KeyError:
        return
    raise _reject(
        TimelineEditErrorCode.ID_COLLISION,
        f"이미 사용 중인 클립 ID입니다: {clip_id}",
    )


@dataclass(frozen=True, slots=True)
class _RestoreTrackOrder:
    track: TrackKind
    expected_ids: tuple[str, ...]
    restored_ids: tuple[str, ...]
    history_label: str

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        track = project.track(self.track)
        current_ids = tuple(clip.clip_id for clip in track.clips)
        if current_ids != self.expected_ids:
            raise _reject(
                TimelineEditErrorCode.STALE_STATE,
                "그룹 이동 이후 트랙 순서가 변경되어 되돌릴 수 없습니다.",
            )
        by_id = {clip.clip_id: clip for clip in track.clips}
        if set(by_id) != set(self.restored_ids):
            raise _reject(
                TimelineEditErrorCode.STALE_STATE,
                "복원할 트랙 순서와 현재 클립 집합이 일치하지 않습니다.",
            )
        next_project = _replace_track(
            project,
            self.track,
            tuple(by_id[clip_id] for clip_id in self.restored_ids),
        )
        return CommandApplication(
            next_project,
            _RestoreTrackOrder(
                self.track,
                self.restored_ids,
                self.expected_ids,
                self.history_label,
            ),
        )


@dataclass(frozen=True, slots=True)
class _RestoreDeletedClips:
    track: TrackKind
    entries: tuple[tuple[int, Clip], ...]
    expected_remaining_ids: tuple[str, ...]
    history_label: str

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        track = project.track(self.track)
        current_ids = tuple(clip.clip_id for clip in track.clips)
        if current_ids != self.expected_remaining_ids:
            raise _reject(
                TimelineEditErrorCode.STALE_STATE,
                "그룹 삭제 이후 트랙이 변경되어 되돌릴 수 없습니다.",
            )
        restored = list(track.clips)
        for index, clip in self.entries:
            if any(existing.clip_id == clip.clip_id for existing in restored):
                raise _reject(
                    TimelineEditErrorCode.ID_COLLISION,
                    f"복원할 클립 ID가 이미 존재합니다: {clip.clip_id}",
                )
            if not 0 <= index <= len(restored):
                raise _reject(
                    TimelineEditErrorCode.OUT_OF_RANGE,
                    "삭제된 클립의 원래 위치를 복원할 수 없습니다.",
                )
            restored.insert(index, clip)
        next_project = _replace_track(project, self.track, tuple(restored))
        return CommandApplication(
            next_project,
            DeleteClipGroup(
                tuple(clip.clip_id for _, clip in self.entries),
                history_label=self.history_label,
            ),
        )


@dataclass(frozen=True, slots=True)
class DuplicateTimelineClip:
    """Duplicate one clip immediately after its source with a caller-supplied ID."""

    clip_id: str
    duplicate_clip_id: str
    history_label: str = "클립 복제"

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        kind, located = _locate_group(project, (self.clip_id,))
        _ensure_new_clip_id(project, self.duplicate_clip_id)
        index, source = located[0]
        duplicate = replace(
            source,
            clip_id=self.duplicate_clip_id,
            label=f"{source.label} 복제본",
        )
        track = project.track(kind)
        clips = (*track.clips[: index + 1], duplicate, *track.clips[index + 1 :])
        next_project = _replace_track(project, kind, clips)
        return CommandApplication(
            next_project,
            DeleteClipGroup((self.duplicate_clip_id,), history_label=self.history_label),
        )


@dataclass(frozen=True, slots=True)
class DeleteClipGroup:
    """Validate and delete a compatible clip group as one atomic edit."""

    clip_ids: tuple[str, ...]
    history_label: str = "클립 그룹 삭제"

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        kind, entries = _locate_group(project, self.clip_ids)
        selected = {clip.clip_id for _, clip in entries}
        track = project.track(kind)
        remaining = tuple(clip for clip in track.clips if clip.clip_id not in selected)
        next_project = _replace_track(project, kind, remaining)
        return CommandApplication(
            next_project,
            _RestoreDeletedClips(
                kind,
                entries,
                tuple(clip.clip_id for clip in remaining),
                self.history_label,
            ),
        )


@dataclass(frozen=True, slots=True)
class MoveVisualClipGroup:
    """Move visual clips to an insertion index while preserving their track order."""

    clip_ids: tuple[str, ...]
    target_index: int
    history_label: str = "시각 클립 그룹 이동"

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        kind, entries = _locate_group(project, self.clip_ids)
        if kind is not TrackKind.VISUAL:
            raise _reject(
                TimelineEditErrorCode.INCOMPATIBLE_SELECTION,
                "삽입 위치 이동은 영상·사진 트랙에서만 사용할 수 있습니다.",
            )
        track = project.track(kind)
        selected_ids = {clip.clip_id for _, clip in entries}
        selected = tuple(clip for _, clip in entries)
        remaining = tuple(clip for clip in track.clips if clip.clip_id not in selected_ids)
        if type(self.target_index) is not int or not 0 <= self.target_index <= len(remaining):
            raise _reject(
                TimelineEditErrorCode.OUT_OF_RANGE,
                "그룹을 놓을 삽입 위치가 범위를 벗어났습니다.",
            )
        reordered = (
            *remaining[: self.target_index],
            *selected,
            *remaining[self.target_index :],
        )
        before_ids = tuple(clip.clip_id for clip in track.clips)
        after_ids = tuple(clip.clip_id for clip in reordered)
        if after_ids == before_ids:
            raise _reject(
                TimelineEditErrorCode.NO_CHANGE,
                "선택한 클립이 이미 요청한 위치에 있습니다.",
            )
        next_project = _replace_track(project, kind, reordered)
        return CommandApplication(
            next_project,
            _RestoreTrackOrder(kind, after_ids, before_ids, self.history_label),
        )


@dataclass(frozen=True, slots=True)
class MoveAbsoluteClipGroup:
    """Apply a common signed time delta to one absolute-time track group."""

    clip_ids: tuple[str, ...]
    delta: ProjectTime
    expected: tuple[Clip, ...] | None = None
    history_label: str = "클립 그룹 시간 이동"

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        if not isinstance(self.delta, ProjectTime):
            raise _reject(
                TimelineEditErrorCode.INCOMPATIBLE_SELECTION,
                "이동 시간은 정수 기반 프로젝트 시간이어야 합니다.",
            )
        if self.delta.nanoseconds == 0:
            raise _reject(TimelineEditErrorCode.NO_CHANGE, "이동 시간이 0입니다.")
        kind, entries = _locate_group(project, self.clip_ids)
        if kind is TrackKind.VISUAL:
            raise _reject(
                TimelineEditErrorCode.INCOMPATIBLE_SELECTION,
                "시각 트랙 그룹은 시간 델타가 아니라 삽입 위치로 이동해야 합니다.",
            )
        current = tuple(clip for _, clip in entries)
        if self.expected is not None and current != self.expected:
            raise _reject(
                TimelineEditErrorCode.STALE_STATE,
                "이동할 클립이 명령 준비 이후 변경되었습니다.",
            )
        if any((clip.timeline_start + self.delta).nanoseconds < 0 for clip in current):
            raise _reject(
                TimelineEditErrorCode.OUT_OF_RANGE,
                "그룹 이동 결과가 타임라인 시작보다 앞설 수 없습니다.",
            )
        selected_ids = {clip.clip_id for clip in current}
        moved_by_id = {
            clip.clip_id: replace(clip, timeline_start=clip.timeline_start + self.delta)
            for clip in current
        }
        track = project.track(kind)
        sequence = tuple(
            moved_by_id.get(clip.clip_id, clip)
            for clip in track.clips
        )
        original_order = {clip.clip_id: index for index, clip in enumerate(track.clips)}
        ordered = tuple(
            sorted(
                sequence,
                key=lambda clip: (clip.timeline_start, original_order[clip.clip_id]),
            )
        )
        next_project = _replace_track(project, kind, ordered)
        ordered_selected_ids = tuple(clip.clip_id for clip in current)
        moved = tuple(next_project.clip(clip_id) for clip_id in ordered_selected_ids)
        if {clip.clip_id for clip in moved} != selected_ids:
            raise _reject(
                TimelineEditErrorCode.STALE_STATE,
                "이동 결과의 클립 집합이 선택과 일치하지 않습니다.",
            )
        return CommandApplication(
            next_project,
            MoveAbsoluteClipGroup(
                ordered_selected_ids,
                ProjectTime(-self.delta.nanoseconds),
                expected=moved,
                history_label=self.history_label,
            ),
        )
