"""Immutable project, media, track, and clip domain values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from math import gcd
from typing import Self

from movie_maker.project.time import ProjectTime

CURRENT_PROJECT_SCHEMA_VERSION = 1


class ProjectValidationError(ValueError):
    """Raised when project data violates a persistent domain invariant."""


class MediaKind(str, Enum):
    """Stable media-kind values used by the project schema."""

    VIDEO = "video"
    PHOTO = "photo"
    AUDIO = "audio"


class TrackKind(str, Enum):
    """The fixed Movie Maker-style tracks."""

    VISUAL = "visual"
    MUSIC = "music"
    NARRATION = "narration"
    TEXT = "text"


TRACK_ORDER = (
    TrackKind.VISUAL,
    TrackKind.MUSIC,
    TrackKind.NARRATION,
    TrackKind.TEXT,
)


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProjectValidationError(f"{field_name} must be a non-blank string.")


def _require_optional_dimension(value: int | None, field_name: str) -> None:
    if value is None:
        return
    if type(value) is not int or value <= 0:
        raise ProjectValidationError(f"{field_name} must be a positive integer.")


def _require_time(value: object, field_name: str) -> ProjectTime:
    if not isinstance(value, ProjectTime):
        raise ProjectValidationError(f"{field_name} must be a ProjectTime value.")
    return value


@dataclass(frozen=True, slots=True)
class PlaybackRate:
    """A normalized positive rational playback rate."""

    numerator: int = 1
    denominator: int = 1

    def __post_init__(self) -> None:
        if type(self.numerator) is not int or type(self.denominator) is not int:
            raise TypeError("Playback-rate numerator and denominator must be integers.")
        if self.numerator <= 0 or self.denominator <= 0:
            raise ProjectValidationError("Playback rate must be positive.")

        divisor = gcd(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", self.numerator // divisor)
        object.__setattr__(self, "denominator", self.denominator // divisor)

    @property
    def fraction(self) -> Fraction:
        """Return the exact playback-rate value."""

        return Fraction(self.numerator, self.denominator)


NORMAL_PLAYBACK_RATE = PlaybackRate()
ZERO_TIME = ProjectTime.zero()


@dataclass(frozen=True, slots=True)
class MediaReference:
    """A non-owning reference to source media and its core metadata."""

    asset_id: str
    name: str
    source_path: str
    kind: MediaKind
    duration: ProjectTime | None
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.asset_id, "asset_id")
        _require_text(self.name, "name")
        _require_text(self.source_path, "source_path")
        if not isinstance(self.kind, MediaKind):
            raise ProjectValidationError("kind must be a MediaKind value.")
        if self.duration is not None:
            duration = _require_time(self.duration, "media duration")
            if duration.nanoseconds <= 0:
                raise ProjectValidationError("Media duration must be positive when present.")
        _require_optional_dimension(self.width, "width")
        _require_optional_dimension(self.height, "height")

        if (self.width is None) != (self.height is None):
            raise ProjectValidationError("Media width and height must be present together.")
        if self.kind is MediaKind.AUDIO and (self.width is not None or self.height is not None):
            raise ProjectValidationError("Audio media cannot have visual dimensions.")
        if self.kind in {MediaKind.VIDEO, MediaKind.PHOTO} and self.width is None:
            raise ProjectValidationError("Visual media requires width and height.")
        if self.kind in {MediaKind.VIDEO, MediaKind.AUDIO} and self.duration is None:
            raise ProjectValidationError("Video and audio media require a known duration.")
        if self.kind is MediaKind.PHOTO and self.duration is not None:
            raise ProjectValidationError("Photo media has no intrinsic duration.")


@dataclass(frozen=True, slots=True)
class Canvas:
    """Persistent project output dimensions and their optional source reference."""

    width: int | None = None
    height: int | None = None
    reference_asset_id: str | None = None

    def __post_init__(self) -> None:
        _require_optional_dimension(self.width, "canvas width")
        _require_optional_dimension(self.height, "canvas height")
        if (self.width is None) != (self.height is None):
            raise ProjectValidationError("Canvas width and height must be present together.")
        if self.reference_asset_id is not None:
            _require_text(self.reference_asset_id, "reference_asset_id")
            if self.width is None:
                raise ProjectValidationError("A reference canvas requires fixed dimensions.")


@dataclass(frozen=True, slots=True)
class Clip:
    """A non-destructive timeline reference to media or generated text."""

    clip_id: str
    track: TrackKind
    asset_id: str | None
    label: str
    timeline_start: ProjectTime
    duration: ProjectTime
    source_in: ProjectTime = ZERO_TIME
    source_out: ProjectTime | None = None
    playback_rate: PlaybackRate = NORMAL_PLAYBACK_RATE

    def __post_init__(self) -> None:
        _require_text(self.clip_id, "clip_id")
        _require_text(self.label, "label")
        if not isinstance(self.track, TrackKind):
            raise ProjectValidationError("track must be a TrackKind value.")
        timeline_start = _require_time(self.timeline_start, "timeline_start")
        duration = _require_time(self.duration, "duration")
        source_in = _require_time(self.source_in, "source_in")
        if self.source_out is not None:
            _require_time(self.source_out, "source_out")
        if not isinstance(self.playback_rate, PlaybackRate):
            raise ProjectValidationError("playback_rate must be a PlaybackRate value.")
        if timeline_start.nanoseconds < 0:
            raise ProjectValidationError("Clip timeline start cannot be negative.")
        if duration.nanoseconds <= 0:
            raise ProjectValidationError("Clip duration must be positive.")
        if source_in.nanoseconds < 0:
            raise ProjectValidationError("Clip source start cannot be negative.")
        if self.source_out is not None and self.source_out <= self.source_in:
            raise ProjectValidationError("Clip source end must be after its source start.")

        if self.track is TrackKind.TEXT:
            if self.asset_id is not None:
                raise ProjectValidationError("Text clips cannot reference source media.")
            if self.source_in != ZERO_TIME or self.source_out is not None:
                raise ProjectValidationError("Text clips cannot define a source range.")
            if self.playback_rate != NORMAL_PLAYBACK_RATE:
                raise ProjectValidationError("Text clips cannot define a playback rate.")
        else:
            if self.asset_id is None:
                raise ProjectValidationError("Media clips require an asset_id.")
            _require_text(self.asset_id, "asset_id")

    @property
    def timeline_end(self) -> ProjectTime:
        """Return the first timeline tick after this clip."""

        return self.timeline_start + self.duration


@dataclass(frozen=True, slots=True)
class TimelineTrack:
    """One immutable fixed-purpose timeline track."""

    kind: TrackKind
    clips: tuple[Clip, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TrackKind):
            raise ProjectValidationError("Track kind must be a TrackKind value.")
        if type(self.clips) is not tuple:
            raise ProjectValidationError("Track clips must use an immutable tuple.")
        identifiers: set[str] = set()
        previous_start: ProjectTime | None = None
        for clip in self.clips:
            if not isinstance(clip, Clip):
                raise ProjectValidationError("Track entries must be Clip values.")
            if clip.track is not self.kind:
                raise ProjectValidationError("A clip must belong to its containing track.")
            if clip.clip_id in identifiers:
                raise ProjectValidationError("Clip identifiers must be unique within a track.")
            if previous_start is not None and clip.timeline_start < previous_start:
                raise ProjectValidationError("Track clips must be ordered by timeline start.")
            identifiers.add(clip.clip_id)
            previous_start = clip.timeline_start


@dataclass(frozen=True, slots=True)
class Project:
    """The complete persistent, UI-independent project state."""

    schema_version: int
    project_id: str
    name: str
    canvas: Canvas
    media: tuple[MediaReference, ...]
    tracks: tuple[TimelineTrack, ...]

    @classmethod
    def empty(cls, *, project_id: str, name: str = "제목 없음") -> Self:
        """Create a valid project with all fixed tracks and no content."""

        return cls(
            schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
            project_id=project_id,
            name=name,
            canvas=Canvas(),
            media=(),
            tracks=tuple(TimelineTrack(kind=kind) for kind in TRACK_ORDER),
        )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise ProjectValidationError("schema_version must be an integer.")
        if self.schema_version != CURRENT_PROJECT_SCHEMA_VERSION:
            raise ProjectValidationError(
                f"Unsupported project schema version: {self.schema_version}."
            )
        _require_text(self.project_id, "project_id")
        _require_text(self.name, "name")
        if not isinstance(self.canvas, Canvas):
            raise ProjectValidationError("canvas must be a Canvas value.")
        if type(self.media) is not tuple:
            raise ProjectValidationError("Project media must use an immutable tuple.")
        if type(self.tracks) is not tuple:
            raise ProjectValidationError("Project tracks must use an immutable tuple.")

        media_by_id: dict[str, MediaReference] = {}
        for media in self.media:
            if not isinstance(media, MediaReference):
                raise ProjectValidationError("Project media entries must be MediaReference values.")
            if media.asset_id in media_by_id:
                raise ProjectValidationError("Media identifiers must be unique within a project.")
            media_by_id[media.asset_id] = media

        if any(not isinstance(track, TimelineTrack) for track in self.tracks):
            raise ProjectValidationError("Project tracks must be TimelineTrack values.")
        if tuple(track.kind for track in self.tracks) != TRACK_ORDER:
            raise ProjectValidationError("Project tracks must match the fixed track order.")

        clip_ids: set[str] = set()
        visual_cursor = ZERO_TIME
        for track in self.tracks:
            for clip in track.clips:
                if clip.clip_id in clip_ids:
                    raise ProjectValidationError("Clip identifiers must be unique within a project.")
                clip_ids.add(clip.clip_id)

                if track.kind is TrackKind.TEXT:
                    continue

                if clip.asset_id is None or clip.asset_id not in media_by_id:
                    raise ProjectValidationError("Every media clip must reference existing media.")
                source = media_by_id[clip.asset_id]
                compatible = (
                    track.kind is TrackKind.VISUAL
                    and source.kind in {MediaKind.VIDEO, MediaKind.PHOTO}
                ) or (
                    track.kind in {TrackKind.MUSIC, TrackKind.NARRATION}
                    and source.kind is MediaKind.AUDIO
                )
                if not compatible:
                    raise ProjectValidationError("Media kind is not compatible with the clip track.")

                if source.kind in {MediaKind.VIDEO, MediaKind.AUDIO}:
                    if clip.source_out is None:
                        raise ProjectValidationError("Timed media clips require a source end.")
                    if source.duration is not None and clip.source_out > source.duration:
                        raise ProjectValidationError("Clip source range exceeds the media duration.")
                elif clip.source_in != ZERO_TIME or clip.source_out is not None:
                    raise ProjectValidationError("Photo clips cannot define a source range.")

            if track.kind is TrackKind.VISUAL:
                for clip in track.clips:
                    if clip.timeline_start != visual_cursor:
                        raise ProjectValidationError(
                            "Visual clips must be contiguous from the project origin."
                        )
                    visual_cursor = clip.timeline_end

        if self.canvas.reference_asset_id is not None:
            reference = media_by_id.get(self.canvas.reference_asset_id)
            if reference is None:
                raise ProjectValidationError("Canvas reference media must exist in the project.")
            if reference.kind not in {MediaKind.VIDEO, MediaKind.PHOTO}:
                raise ProjectValidationError("Canvas reference media must be visual.")

    def media_reference(self, asset_id: str) -> MediaReference:
        """Return a media reference or raise KeyError when it does not exist."""

        for media in self.media:
            if media.asset_id == asset_id:
                return media
        raise KeyError(asset_id)

    def track(self, kind: TrackKind) -> TimelineTrack:
        """Return one of the fixed tracks."""

        return self.tracks[TRACK_ORDER.index(kind)]

    def clip(self, clip_id: str) -> Clip:
        """Return a clip by its project-wide identifier."""

        for track in self.tracks:
            for clip in track.clips:
                if clip.clip_id == clip_id:
                    return clip
        raise KeyError(clip_id)

    @property
    def duration(self) -> ProjectTime:
        """Return the visual story-line duration."""

        visual_clips = self.track(TrackKind.VISUAL).clips
        return visual_clips[-1].timeline_end if visual_clips else ZERO_TIME
