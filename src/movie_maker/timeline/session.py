"""Typed, non-persistent timeline selection and view state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite

from movie_maker.project.model import Project, TrackKind


class TimelineViewMode(str, Enum):
    TIMELINE = "타임라인"
    STORYBOARD = "스토리보드"


@dataclass(frozen=True, slots=True)
class TimelineSelection:
    """A compatible ordered selection with one deterministic active item."""

    clip_ids: tuple[str, ...] = ()
    active_clip_id: str | None = None
    track: TrackKind | None = None

    @classmethod
    def from_project(
        cls,
        project: Project,
        clip_ids: tuple[str, ...],
        active_clip_id: str | None,
    ) -> TimelineSelection:
        if not clip_ids:
            return cls()
        if len(set(clip_ids)) != len(clip_ids):
            raise ValueError("선택에 같은 클립이 두 번 들어 있습니다.")
        requested = set(clip_ids)
        matches = [
            (track.kind, clip.clip_id)
            for track in project.tracks
            for clip in track.clips
            if clip.clip_id in requested
        ]
        if len(matches) != len(requested):
            raise ValueError("선택한 클립 중 타임라인에 없는 항목이 있습니다.")
        tracks = {track for track, _ in matches}
        if len(tracks) != 1:
            raise ValueError("서로 다른 트랙의 클립은 함께 선택할 수 없습니다.")
        ordered_ids = tuple(clip_id for _, clip_id in matches)
        active = active_clip_id if active_clip_id in requested else ordered_ids[-1]
        return cls(ordered_ids, active, matches[0][0])

    def reconcile(self, project: Project) -> TimelineSelection:
        remaining = tuple(
            clip.clip_id
            for track in project.tracks
            for clip in track.clips
            if clip.clip_id in set(self.clip_ids)
        )
        if not remaining:
            return TimelineSelection()
        active = self.active_clip_id if self.active_clip_id in remaining else remaining[0]
        return TimelineSelection.from_project(project, remaining, active)


def normalise_timeline_zoom(value: float) -> float:
    """Accept a positive finite percentage without an application zoom range."""

    if type(value) not in (int, float):
        raise TypeError("타임라인 배율은 숫자여야 합니다.")
    if not isfinite(value) or value <= 0:
        raise ValueError("타임라인 배율은 0보다 큰 유한한 값이어야 합니다.")
    return float(value)
