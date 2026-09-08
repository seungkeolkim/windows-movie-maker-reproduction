"""Exact mapping between the project timeline and visual source frames."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from movie_maker.project import (
    FrameRate,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    Project,
    ProjectTime,
    TimeRounding,
    TrackKind,
)


@dataclass(frozen=True, slots=True)
class FrameTarget:
    """One deterministic source frame required for a project position."""

    project_id: str
    clip_id: str
    clip_label: str
    asset_id: str
    source_path: str
    media_kind: MediaKind
    stream_index: int
    source_time: ProjectTime
    source_pts: int
    time_base_numerator: int
    time_base_denominator: int
    start_pts: int

    @property
    def cache_key(self) -> tuple[str, str, str, int, int]:
        """Return a session-local identity for the requested source frame."""

        return (
            self.project_id,
            self.clip_id,
            self.source_path,
            self.stream_index,
            self.source_pts,
        )


@dataclass(frozen=True, slots=True)
class PreviewPosition:
    """A clamped project position and its optional visual frame."""

    position: ProjectTime
    target: FrameTarget | None


def _primary_video_stream(media: MediaReference) -> MediaStream | None:
    if media.primary_stream_index is None:
        return None
    return next(
        (
            stream
            for stream in media.streams
            if stream.index == media.primary_stream_index
            and stream.kind is MediaStreamKind.VIDEO
        ),
        None,
    )


def _frame_rate(stream: MediaStream | None) -> FrameRate | None:
    return stream.average_frame_rate if stream is not None else None


def _source_boundary_at_or_before(
    requested: ProjectTime,
    *,
    stream: MediaStream | None,
) -> ProjectTime:
    if stream is None:
        return requested
    rate = _frame_rate(stream)
    if rate is not None:
        return rate.time_at_frame(rate.frame_at_or_before(requested))
    tick = stream.time_base.seconds_per_tick
    tick_index = requested.to_fractional_seconds() // tick
    return ProjectTime.from_seconds(tick_index * tick)


def _source_boundary_at_or_after(
    requested: ProjectTime,
    *,
    stream: MediaStream | None,
) -> ProjectTime:
    if stream is None:
        return requested
    rate = _frame_rate(stream)
    if rate is not None:
        index = rate.frame_at_or_before(requested)
        boundary = rate.time_at_frame(index)
        return boundary if boundary >= requested else rate.time_at_frame(index + 1)
    tick = stream.time_base.seconds_per_tick
    seconds = requested.to_fractional_seconds()
    tick_index = -(-seconds // tick)
    return ProjectTime.from_seconds(tick_index * tick)


def _last_source_boundary(
    source_out: ProjectTime,
    *,
    stream: MediaStream | None,
) -> ProjectTime:
    if source_out.nanoseconds <= 0:
        return ProjectTime.zero()
    before_end = ProjectTime(source_out.nanoseconds - 1)
    return _source_boundary_at_or_before(before_end, stream=stream)


def _source_time_for_position(
    position: ProjectTime,
    *,
    clip_start: ProjectTime,
    source_in: ProjectTime,
    source_out: ProjectTime | None,
    playback_rate: Fraction,
    stream: MediaStream | None,
    at_project_end: bool,
) -> ProjectTime:
    if source_out is None:
        return ProjectTime.zero()
    if at_project_end:
        return _last_source_boundary(source_out, stream=stream)
    local = position - clip_start
    source_seconds = source_in.to_fractional_seconds() + (
        local.to_fractional_seconds() * playback_rate
    )
    requested = ProjectTime.from_seconds(source_seconds)
    requested = max(requested, source_in)
    if requested >= source_out:
        requested = ProjectTime(source_out.nanoseconds - 1)
    boundary = _source_boundary_at_or_before(requested, stream=stream)
    if boundary < source_in:
        boundary = _source_boundary_at_or_after(source_in, stream=stream)
    return boundary


def _source_pts(source_time: ProjectTime, stream: MediaStream | None) -> tuple[int, int, int, int]:
    if stream is None:
        return source_time.nanoseconds, 1, 1_000_000_000, 0
    start_pts = stream.start_pts or 0
    relative_pts = source_time.to_fractional_seconds() // stream.time_base.seconds_per_tick
    return (
        start_pts + relative_pts,
        stream.time_base.numerator,
        stream.time_base.denominator,
        start_pts,
    )


def frame_at_project_time(project: Project, position: ProjectTime) -> PreviewPosition:
    """Return the visual source frame at a clamped project position."""

    if not isinstance(position, ProjectTime):
        raise TypeError("position must be a ProjectTime value.")
    if position.nanoseconds < 0:
        position = ProjectTime.zero()
    position = min(position, project.duration)

    visual_clips = project.track(TrackKind.VISUAL).clips
    if not visual_clips:
        return PreviewPosition(position, None)

    at_project_end = position == project.duration
    clip = visual_clips[-1]
    if not at_project_end:
        clip = next(
            candidate
            for candidate in visual_clips
            if candidate.timeline_start <= position < candidate.timeline_end
        )
    if clip.asset_id is None:
        return PreviewPosition(position, None)
    media = project.media_reference(clip.asset_id)
    stream = _primary_video_stream(media)
    stream_index = stream.index if stream is not None else 0
    source_time = _source_time_for_position(
        position,
        clip_start=clip.timeline_start,
        source_in=clip.source_in,
        source_out=clip.source_out,
        playback_rate=clip.playback_rate.fraction,
        stream=stream,
        at_project_end=at_project_end,
    )
    source_pts, numerator, denominator, start_pts = _source_pts(source_time, stream)
    return PreviewPosition(
        position,
        FrameTarget(
            project_id=project.project_id,
            clip_id=clip.clip_id,
            clip_label=clip.label,
            asset_id=media.asset_id,
            source_path=media.source_path,
            media_kind=media.kind,
            stream_index=stream_index,
            source_time=source_time,
            source_pts=source_pts,
            time_base_numerator=numerator,
            time_base_denominator=denominator,
            start_pts=start_pts,
        ),
    )


def _timeline_position_for_source(
    source_time: ProjectTime,
    *,
    clip_start: ProjectTime,
    source_in: ProjectTime,
    playback_rate: Fraction,
) -> ProjectTime:
    local_seconds = (
        source_time.to_fractional_seconds() - source_in.to_fractional_seconds()
    ) / playback_rate
    local = ProjectTime.from_seconds(local_seconds)
    return clip_start + local


def _frame_time(rate: FrameRate | None, stream: MediaStream | None, index: int) -> ProjectTime:
    if rate is not None:
        return rate.time_at_frame(index)
    if stream is not None:
        return ProjectTime.from_seconds(index * stream.time_base.seconds_per_tick)
    return ProjectTime.from_seconds(Fraction(index, 30))


def _frame_index_at_or_before(
    source_time: ProjectTime,
    rate: FrameRate | None,
    stream: MediaStream | None,
) -> int:
    if rate is not None:
        return rate.frame_at_or_before(source_time)
    if stream is not None:
        return source_time.to_fractional_seconds() // stream.time_base.seconds_per_tick
    return source_time.to_fractional_seconds() // Fraction(1, 30)


def step_project_frame(project: Project, position: ProjectTime, direction: int) -> ProjectTime:
    """Move to an adjacent deterministic visual frame boundary."""

    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1.")
    visual_clips = project.track(TrackKind.VISUAL).clips
    if not visual_clips:
        return ProjectTime.zero()
    current = frame_at_project_time(project, position)
    if current.target is None:
        return ProjectTime.zero()
    clip_index = next(
        index for index, clip in enumerate(visual_clips) if clip.clip_id == current.target.clip_id
    )
    clip = visual_clips[clip_index]
    if clip.asset_id is None:
        return position
    media = project.media_reference(clip.asset_id)
    stream = _primary_video_stream(media)
    rate = _frame_rate(stream)

    if media.kind is MediaKind.PHOTO:
        if direction > 0:
            return clip.timeline_end
        if position > clip.timeline_start:
            return clip.timeline_start
    else:
        frame_index = _frame_index_at_or_before(current.target.source_time, rate, stream)
        boundary = _frame_time(rate, stream, frame_index)
        exact_position = _timeline_position_for_source(
            boundary,
            clip_start=clip.timeline_start,
            source_in=clip.source_in,
            playback_rate=clip.playback_rate.fraction,
        )
        if direction > 0:
            candidate_source = _frame_time(rate, stream, frame_index + 1)
            if clip.source_out is not None and candidate_source < clip.source_out:
                return min(
                    _timeline_position_for_source(
                        candidate_source,
                        clip_start=clip.timeline_start,
                        source_in=clip.source_in,
                        playback_rate=clip.playback_rate.fraction,
                    ),
                    project.duration,
                )
        elif position > exact_position >= clip.timeline_start:
            return exact_position
        elif frame_index > 0:
            candidate_source = _frame_time(rate, stream, frame_index - 1)
            if candidate_source >= clip.source_in:
                return _timeline_position_for_source(
                    candidate_source,
                    clip_start=clip.timeline_start,
                    source_in=clip.source_in,
                    playback_rate=clip.playback_rate.fraction,
                )

    adjacent_index = clip_index + direction
    if adjacent_index < 0:
        return ProjectTime.zero()
    if adjacent_index >= len(visual_clips):
        return project.duration
    adjacent = visual_clips[adjacent_index]
    if direction > 0:
        return adjacent.timeline_start
    return ProjectTime(adjacent.timeline_end.nanoseconds - 1)


class PlaybackClock:
    """Non-persistent exact playback position driven by a monotonic clock."""

    def __init__(self, clock_ns: Callable[[], int]) -> None:
        self._clock_ns = clock_ns
        self._position = ProjectTime.zero()
        self._playing = False
        self._last_tick_ns: int | None = None

    @property
    def position(self) -> ProjectTime:
        return self._position

    @property
    def is_playing(self) -> bool:
        return self._playing

    def sync_project(self, project: Project) -> ProjectTime:
        self._position = min(self._position, project.duration)
        if project.duration.nanoseconds == 0:
            self._playing = False
            self._last_tick_ns = None
        return self._position

    def seek(self, project: Project, position: ProjectTime) -> ProjectTime:
        self._playing = False
        self._last_tick_ns = None
        self._position = min(max(position, ProjectTime.zero()), project.duration)
        return self._position

    def toggle(self, project: Project) -> bool:
        self.sync_project(project)
        if project.duration.nanoseconds == 0:
            return False
        if self._playing:
            self.advance(project)
            self._playing = False
            self._last_tick_ns = None
            return False
        if self._position >= project.duration:
            self._position = ProjectTime.zero()
        self._playing = True
        self._last_tick_ns = self._clock_ns()
        return True

    def advance(self, project: Project) -> ProjectTime:
        if not self._playing:
            return self.sync_project(project)
        now = self._clock_ns()
        previous = self._last_tick_ns if self._last_tick_ns is not None else now
        self._last_tick_ns = now
        elapsed = max(0, now - previous)
        self._position = ProjectTime(self._position.nanoseconds + elapsed)
        if self._position >= project.duration:
            self._position = project.duration
            self._playing = False
            self._last_tick_ns = None
        return self._position

    def advance_elapsed(self, project: Project, elapsed_nanoseconds: int) -> ProjectTime:
        """Advance by an explicit duration for deterministic adapters and tests."""

        if type(elapsed_nanoseconds) is not int or elapsed_nanoseconds < 0:
            raise ValueError("elapsed_nanoseconds must be a non-negative integer.")
        if not self._playing:
            return self.sync_project(project)
        self._position = ProjectTime(self._position.nanoseconds + elapsed_nanoseconds)
        if self._position >= project.duration:
            self._position = project.duration
            self._playing = False
            self._last_tick_ns = None
        else:
            self._last_tick_ns = self._clock_ns()
        return self._position

    def step(self, project: Project, direction: int) -> ProjectTime:
        self._playing = False
        self._last_tick_ns = None
        self._position = step_project_frame(project, self._position, direction)
        return self._position


def ui_milliseconds(position: ProjectTime) -> int:
    """Convert an exact preview position to the existing integer UI boundary."""

    return position.to_milliseconds(rounding=TimeRounding.NEAREST)
