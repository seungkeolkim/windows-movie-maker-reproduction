"""Immutable project, media, track, and clip domain values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from fractions import Fraction
from itertools import pairwise
from math import gcd
from typing import Self

from movie_maker.project.time import FrameRate, ProjectTime

CURRENT_PROJECT_SCHEMA_VERSION = 1


class ProjectValidationError(ValueError):
    """Raised when project data violates a persistent domain invariant."""


class MediaKind(str, Enum):
    """Stable media-kind values used by the project schema."""

    VIDEO = "video"
    PHOTO = "photo"
    AUDIO = "audio"


class MediaStreamKind(str, Enum):
    """Codec-level stream categories retained from source media."""

    VIDEO = "video"
    AUDIO = "audio"


class TrackKind(str, Enum):
    """The fixed Movie Maker-style tracks."""

    VISUAL = "visual"
    MUSIC = "music"
    NARRATION = "narration"
    TEXT = "text"


class FitMode(str, Enum):
    """Stable visual placement choices."""

    FIT = "fit"
    FILL = "fill"


class UserRotation(IntEnum):
    """A clockwise user rotation applied after input rotation metadata."""

    NONE = 0
    CLOCKWISE_90 = 90
    CLOCKWISE_180 = 180
    CLOCKWISE_270 = 270


class VisualEffectPreset(str, Enum):
    """The deliberately small executable-free visual effect catalogue."""

    NONE = "none"
    WARM = "warm"
    MONOCHROME = "monochrome"
    VIVID = "vivid"


class DuckingPreset(str, Enum):
    """Music attenuation requested by a narration clip."""

    OFF = "off"
    LIGHT = "light"
    MEDIUM = "medium"
    STRONG = "strong"


class TextKind(str, Enum):
    """Stable generated-text purposes."""

    TITLE = "title"
    CAPTION = "caption"
    CREDITS = "credits"


class TextAlignment(str, Enum):
    """Horizontal text alignment within its safe-area box."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class TextAnimationPreset(str, Enum):
    """The bounded text animation catalogue."""

    NONE = "none"
    FADE = "fade"
    SCROLL_UP = "scroll_up"


class TransitionPreset(str, Enum):
    """The bounded visual-boundary transition catalogue."""

    FADE = "fade"
    DISSOLVE = "dissolve"
    WIPE_LEFT = "wipe_left"


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
class AudioLevel:
    """A deterministic persisted gain percentage for one audible clip."""

    percent: int = 100

    def __post_init__(self) -> None:
        if type(self.percent) is not int:
            raise TypeError("Audio level percent must be an integer.")
        if not 0 <= self.percent <= 100:
            raise ProjectValidationError("Audio level percent must be between 0 and 100.")

    @property
    def fraction(self) -> Fraction:
        """Return the exact gain multiplier."""

        return Fraction(self.percent, 100)


DEFAULT_AUDIO_LEVEL = AudioLevel()


@dataclass(frozen=True, slots=True)
class Brightness:
    """A limited integer brightness adjustment, expressed as a percentage."""

    percent: int = 0

    def __post_init__(self) -> None:
        if type(self.percent) is not int:
            raise TypeError("Brightness percent must be an integer.")
        if not -100 <= self.percent <= 100:
            raise ProjectValidationError("Brightness percent must be between -100 and 100.")

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.percent, 100)


DEFAULT_BRIGHTNESS = Brightness()


@dataclass(frozen=True, slots=True)
class NormalizedPosition:
    """Resolution-independent coordinates in ten-thousandths of the canvas."""

    x: int = 5_000
    y: int = 8_500

    def __post_init__(self) -> None:
        if type(self.x) is not int or type(self.y) is not int:
            raise TypeError("Normalized position coordinates must be integers.")
        if not 0 <= self.x <= 10_000 or not 0 <= self.y <= 10_000:
            raise ProjectValidationError(
                "Normalized position coordinates must be between 0 and 10000."
            )


@dataclass(frozen=True, slots=True)
class TextStyle:
    """Persistent, resolution-independent text appearance intent."""

    font_family: str = "Malgun Gothic"
    size: int = 32
    bold: bool = True
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    alignment: TextAlignment = TextAlignment.CENTER

    def __post_init__(self) -> None:
        _require_text(self.font_family, "font_family")
        if type(self.size) is not int or not 12 <= self.size <= 200:
            raise ProjectValidationError("Text size must be an integer between 12 and 200.")
        if type(self.bold) is not bool:
            raise ProjectValidationError("Text bold must be a boolean value.")
        for value, label in (
            (self.color, "text color"),
            (self.outline_color, "text outline color"),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 7
                or not value.startswith("#")
            ):
                raise ProjectValidationError(f"{label} must use #RRGGBB format.")
            try:
                int(value[1:], 16)
            except ValueError as error:
                raise ProjectValidationError(f"{label} must use #RRGGBB format.") from error
        if not isinstance(self.alignment, TextAlignment):
            raise ProjectValidationError("Text alignment must be a TextAlignment value.")


DEFAULT_TEXT_STYLE = TextStyle()


@dataclass(frozen=True, slots=True)
class TextOverlay:
    """Persistent text content, placement, and bounded animation intent."""

    kind: TextKind = TextKind.CAPTION
    content: str = ""
    style: TextStyle = DEFAULT_TEXT_STYLE
    position: NormalizedPosition = NormalizedPosition()
    animation: TextAnimationPreset = TextAnimationPreset.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TextKind):
            raise ProjectValidationError("Text kind must be a TextKind value.")
        if not isinstance(self.content, str):
            raise ProjectValidationError("Text content must be a string.")
        if not isinstance(self.style, TextStyle):
            raise ProjectValidationError("Text style must be a TextStyle value.")
        if not isinstance(self.position, NormalizedPosition):
            raise ProjectValidationError("Text position must be a NormalizedPosition value.")
        if not isinstance(self.animation, TextAnimationPreset):
            raise ProjectValidationError(
                "Text animation must be a TextAnimationPreset value."
            )


@dataclass(frozen=True, slots=True)
class MixerSettings:
    """Project-wide track-bus gains, multiplied after per-clip gain."""

    original: AudioLevel = DEFAULT_AUDIO_LEVEL
    music: AudioLevel = DEFAULT_AUDIO_LEVEL
    narration: AudioLevel = DEFAULT_AUDIO_LEVEL

    def __post_init__(self) -> None:
        if any(
            not isinstance(level, AudioLevel)
            for level in (self.original, self.music, self.narration)
        ):
            raise ProjectValidationError("Mixer settings require AudioLevel values.")


DEFAULT_MIXER_SETTINGS = MixerSettings()


@dataclass(frozen=True, slots=True)
class MediaTimeBase:
    """The exact number of seconds represented by one source timestamp unit."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if type(self.numerator) is not int or type(self.denominator) is not int:
            raise TypeError("Media-time-base numerator and denominator must be integers.")
        if self.numerator <= 0 or self.denominator <= 0:
            raise ProjectValidationError("Media time base must be positive.")

        divisor = gcd(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", self.numerator // divisor)
        object.__setattr__(self, "denominator", self.denominator // divisor)

    @property
    def seconds_per_tick(self) -> Fraction:
        """Return the exact duration of one source timestamp unit."""

        return Fraction(self.numerator, self.denominator)


@dataclass(frozen=True, slots=True)
class MediaStream:
    """Persistent timing metadata for one ffprobe video or audio stream."""

    index: int
    kind: MediaStreamKind
    codec_name: str
    time_base: MediaTimeBase
    start_pts: int | None = None
    duration_ts: int | None = None
    average_frame_rate: FrameRate | None = None
    sample_rate: int | None = None
    rotation_degrees: int = 0

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ProjectValidationError("Media stream index must be a non-negative integer.")
        if not isinstance(self.kind, MediaStreamKind):
            raise ProjectValidationError("Media stream kind must be a MediaStreamKind value.")
        _require_text(self.codec_name, "codec_name")
        if not isinstance(self.time_base, MediaTimeBase):
            raise ProjectValidationError("Media stream time_base must be a MediaTimeBase value.")
        if self.start_pts is not None and type(self.start_pts) is not int:
            raise ProjectValidationError("Media stream start_pts must be an integer when present.")
        if self.duration_ts is not None and (
            type(self.duration_ts) is not int or self.duration_ts <= 0
        ):
            raise ProjectValidationError("Media stream duration_ts must be positive when present.")
        if self.average_frame_rate is not None and not isinstance(
            self.average_frame_rate, FrameRate
        ):
            raise ProjectValidationError(
                "Media stream average_frame_rate must be a FrameRate when present."
            )
        if self.sample_rate is not None and (
            type(self.sample_rate) is not int or self.sample_rate <= 0
        ):
            raise ProjectValidationError("Media stream sample_rate must be positive when present.")
        if self.kind is MediaStreamKind.VIDEO and self.sample_rate is not None:
            raise ProjectValidationError("Video streams cannot define an audio sample rate.")
        if self.kind is MediaStreamKind.AUDIO and self.average_frame_rate is not None:
            raise ProjectValidationError("Audio streams cannot define a video frame rate.")
        if type(self.rotation_degrees) is not int or not 0 <= self.rotation_degrees < 360:
            raise ProjectValidationError("Media rotation must be an integer from 0 through 359.")
        if self.kind is MediaStreamKind.AUDIO and self.rotation_degrees != 0:
            raise ProjectValidationError("Audio streams cannot define rotation metadata.")


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
    primary_stream_index: int | None = None
    streams: tuple[MediaStream, ...] = ()

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

        if self.primary_stream_index is not None and (
            type(self.primary_stream_index) is not int or self.primary_stream_index < 0
        ):
            raise ProjectValidationError(
                "primary_stream_index must be a non-negative integer when present."
            )
        if type(self.streams) is not tuple:
            raise ProjectValidationError("Media streams must use an immutable tuple.")
        if any(not isinstance(stream, MediaStream) for stream in self.streams):
            raise ProjectValidationError("Media streams must contain MediaStream values.")
        stream_indexes = {stream.index for stream in self.streams}
        if len(stream_indexes) != len(self.streams):
            raise ProjectValidationError("Media stream indexes must be unique within a source.")
        if self.streams and self.primary_stream_index is None:
            raise ProjectValidationError("Analyzed media requires a primary stream index.")
        if not self.streams and self.primary_stream_index is not None:
            raise ProjectValidationError("A primary stream index requires stream metadata.")
        if self.primary_stream_index is not None:
            primary = next(
                (stream for stream in self.streams if stream.index == self.primary_stream_index),
                None,
            )
            if primary is None:
                raise ProjectValidationError("Primary stream index must identify a source stream.")
            expected_kind = (
                MediaStreamKind.AUDIO
                if self.kind is MediaKind.AUDIO
                else MediaStreamKind.VIDEO
            )
            if primary.kind is not expected_kind:
                raise ProjectValidationError("Primary stream kind must match the media kind.")


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
    audio_level: AudioLevel = DEFAULT_AUDIO_LEVEL
    audio_muted: bool = False
    fade_in: ProjectTime = ZERO_TIME
    fade_out: ProjectTime = ZERO_TIME
    ducking: DuckingPreset = DuckingPreset.OFF
    fit_mode: FitMode = FitMode.FIT
    user_rotation: UserRotation = UserRotation.NONE
    brightness: Brightness = DEFAULT_BRIGHTNESS
    effect_preset: VisualEffectPreset = VisualEffectPreset.NONE
    text: TextOverlay | None = None

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
        if not isinstance(self.audio_level, AudioLevel):
            raise ProjectValidationError("audio_level must be an AudioLevel value.")
        if type(self.audio_muted) is not bool:
            raise ProjectValidationError("audio_muted must be a boolean value.")
        fade_in = _require_time(self.fade_in, "fade_in")
        fade_out = _require_time(self.fade_out, "fade_out")
        if fade_in.nanoseconds < 0 or fade_out.nanoseconds < 0:
            raise ProjectValidationError("Audio fades cannot be negative.")
        if fade_in + fade_out > duration:
            raise ProjectValidationError("Audio fade durations cannot exceed clip duration.")
        if not isinstance(self.ducking, DuckingPreset):
            raise ProjectValidationError("ducking must be a DuckingPreset value.")
        if not isinstance(self.fit_mode, FitMode):
            raise ProjectValidationError("fit_mode must be a FitMode value.")
        if not isinstance(self.user_rotation, UserRotation):
            raise ProjectValidationError("user_rotation must be a UserRotation value.")
        if not isinstance(self.brightness, Brightness):
            raise ProjectValidationError("brightness must be a Brightness value.")
        if not isinstance(self.effect_preset, VisualEffectPreset):
            raise ProjectValidationError(
                "effect_preset must be a VisualEffectPreset value."
            )
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
            if self.audio_level != DEFAULT_AUDIO_LEVEL or self.audio_muted:
                raise ProjectValidationError("Text clips cannot define audio properties.")
            if self.fade_in != ZERO_TIME or self.fade_out != ZERO_TIME:
                raise ProjectValidationError("Text clips cannot define audio fades.")
            if self.ducking is not DuckingPreset.OFF:
                raise ProjectValidationError("Text clips cannot define audio ducking.")
            if self.text is None:
                object.__setattr__(self, "text", TextOverlay())
        else:
            if self.asset_id is None:
                raise ProjectValidationError("Media clips require an asset_id.")
            _require_text(self.asset_id, "asset_id")
            if self.text is not None:
                raise ProjectValidationError("Media clips cannot define text overlay values.")

        if self.track is not TrackKind.VISUAL and (
            self.fit_mode is not FitMode.FIT
            or self.user_rotation is not UserRotation.NONE
            or self.brightness != DEFAULT_BRIGHTNESS
            or self.effect_preset is not VisualEffectPreset.NONE
        ):
            raise ProjectValidationError("Only visual clips can define visual properties.")
        if self.track not in {TrackKind.VISUAL, TrackKind.MUSIC, TrackKind.NARRATION} and (
            self.audio_level != DEFAULT_AUDIO_LEVEL
            or self.audio_muted
            or self.fade_in != ZERO_TIME
            or self.fade_out != ZERO_TIME
            or self.ducking is not DuckingPreset.OFF
        ):
            raise ProjectValidationError("This clip type cannot define audio properties.")
        if self.track is not TrackKind.NARRATION and self.ducking is not DuckingPreset.OFF:
            raise ProjectValidationError("Only narration clips can request music ducking.")

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
class Transition:
    """A bounded effect attached to one adjacent visual clip-ID boundary."""

    left_clip_id: str
    right_clip_id: str
    preset: TransitionPreset
    duration: ProjectTime

    def __post_init__(self) -> None:
        _require_text(self.left_clip_id, "left_clip_id")
        _require_text(self.right_clip_id, "right_clip_id")
        if self.left_clip_id == self.right_clip_id:
            raise ProjectValidationError("A transition requires two different clip identifiers.")
        if not isinstance(self.preset, TransitionPreset):
            raise ProjectValidationError("Transition preset must be a TransitionPreset value.")
        duration = _require_time(self.duration, "transition duration")
        if duration.nanoseconds < 100_000_000:
            raise ProjectValidationError("Transition duration must be at least 100 milliseconds.")

    @property
    def boundary(self) -> tuple[str, str]:
        return self.left_clip_id, self.right_clip_id


def transitions_for_tracks(
    transitions: tuple[Transition, ...],
    tracks: tuple[TimelineTrack, ...],
) -> tuple[Transition, ...]:
    """Drop transitions whose visual adjacency no longer exists."""

    visual = tracks[TRACK_ORDER.index(TrackKind.VISUAL)].clips
    adjacent = {
        (left.clip_id, right.clip_id)
        for left, right in pairwise(visual)
    }
    return tuple(transition for transition in transitions if transition.boundary in adjacent)


@dataclass(frozen=True, slots=True)
class Project:
    """The complete persistent, UI-independent project state."""

    schema_version: int
    project_id: str
    name: str
    canvas: Canvas
    media: tuple[MediaReference, ...]
    tracks: tuple[TimelineTrack, ...]
    mixer: MixerSettings = DEFAULT_MIXER_SETTINGS
    transitions: tuple[Transition, ...] = ()

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
            mixer=DEFAULT_MIXER_SETTINGS,
            transitions=(),
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
        if not isinstance(self.mixer, MixerSettings):
            raise ProjectValidationError("Project mixer must be a MixerSettings value.")
        if type(self.transitions) is not tuple or any(
            not isinstance(transition, Transition) for transition in self.transitions
        ):
            raise ProjectValidationError("Project transitions must be an immutable Transition tuple.")

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
                    source_seconds = (
                        clip.source_out - clip.source_in
                    ).to_fractional_seconds()
                    expected_duration = ProjectTime.from_seconds(
                        source_seconds / clip.playback_rate.fraction
                    )
                    if clip.duration != expected_duration:
                        raise ProjectValidationError(
                            "Timed clip duration must equal source span divided by playback rate."
                        )
                elif (
                    clip.source_in != ZERO_TIME
                    or clip.source_out is not None
                    or clip.playback_rate != NORMAL_PLAYBACK_RATE
                ):
                    raise ProjectValidationError(
                        "Photo clips cannot define a source range or playback rate."
                    )
                if source.kind is MediaKind.PHOTO and (
                    clip.audio_level != DEFAULT_AUDIO_LEVEL
                    or clip.audio_muted
                    or clip.fade_in != ZERO_TIME
                    or clip.fade_out != ZERO_TIME
                ):
                    raise ProjectValidationError("Photo clips cannot define audio properties.")

            if track.kind is TrackKind.VISUAL:
                for clip in track.clips:
                    if clip.timeline_start != visual_cursor:
                        raise ProjectValidationError(
                            "Visual clips must be contiguous from the project origin."
                        )
                    visual_cursor = clip.timeline_end

        visual_clips = self.track(TrackKind.VISUAL).clips
        adjacent_boundaries = {
            (left.clip_id, right.clip_id)
            for left, right in pairwise(visual_clips)
        }
        transition_boundaries: set[tuple[str, str]] = set()
        for transition in self.transitions:
            if transition.boundary not in adjacent_boundaries:
                raise ProjectValidationError(
                    "Transitions must identify one current adjacent visual boundary."
                )
            if transition.boundary in transition_boundaries:
                raise ProjectValidationError("A visual boundary can contain only one transition.")
            left = self.clip(transition.left_clip_id)
            right = self.clip(transition.right_clip_id)
            maximum = ProjectTime(
                min(left.duration.nanoseconds, right.duration.nanoseconds) // 2
            )
            if transition.duration > maximum:
                raise ProjectValidationError(
                    "Transition duration cannot exceed half of either adjacent clip."
                )
            transition_boundaries.add(transition.boundary)

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
