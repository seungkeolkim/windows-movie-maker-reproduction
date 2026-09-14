"""UI-independent project audio graph and reusable FFmpeg filter construction."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from fractions import Fraction

from movie_maker.project import (
    DEFAULT_AUDIO_LEVEL,
    AudioLevel,
    DuckingPreset,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    PlaybackRate,
    Project,
    ProjectTime,
    TrackKind,
)

OUTPUT_SAMPLE_RATE = 48_000
OUTPUT_CHANNELS = 2
OUTPUT_BYTES_PER_SAMPLE = 2
OUTPUT_FRAME_BYTES = OUTPUT_CHANNELS * OUTPUT_BYTES_PER_SAMPLE
MIN_AUDIO_RATE = Fraction(1, 2)
MAX_AUDIO_RATE = Fraction(2, 1)
DUCKING_ATTACK = ProjectTime.from_milliseconds(200)
DUCKING_RELEASE = ProjectTime.from_milliseconds(500)
ZERO_AUDIO_TIME = ProjectTime.zero()


class AudioGraphErrorCode(str, Enum):
    """Typed failures that can be detected before FFmpeg starts."""

    INVALID_RANGE = "invalid_range"
    NO_AUDIO_STREAM = "no_audio_stream"
    UNSUPPORTED_SPEED = "unsupported_speed"


class AudioGraphError(ValueError):
    """Raised when a project range cannot produce a valid audio graph."""

    def __init__(self, code: AudioGraphErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class AudioSourceKind(str, Enum):
    ORIGINAL = "original"
    MUSIC = "music"
    NARRATION = "narration"


@dataclass(frozen=True, slots=True)
class DuckingWindow:
    """One narration interval and its deterministic music attenuation."""

    start: ProjectTime
    end: ProjectTime
    preset: DuckingPreset
    attack: ProjectTime = DUCKING_ATTACK
    release: ProjectTime = DUCKING_RELEASE

    def __post_init__(self) -> None:
        if self.start.nanoseconds < 0 or self.end <= self.start:
            raise ValueError("Ducking windows require an ordered positive interval.")
        if self.preset is DuckingPreset.OFF:
            raise ValueError("An off ducking preset does not define a window.")
        if self.attack.nanoseconds < 0 or self.release.nanoseconds < 0:
            raise ValueError("Ducking attack and release cannot be negative.")


@dataclass(frozen=True, slots=True)
class AudioSource:
    """One source segment positioned inside a render-range audio graph."""

    asset_id: str
    clip_id: str
    kind: AudioSourceKind
    source_path: str
    stream_index: int
    source_in: ProjectTime
    source_out: ProjectTime
    timeline_delay: ProjectTime
    duration: ProjectTime
    playback_rate: PlaybackRate
    level: AudioLevel
    muted: bool
    bus_level: AudioLevel = DEFAULT_AUDIO_LEVEL
    clip_start: ProjectTime = ZERO_AUDIO_TIME
    clip_duration: ProjectTime = ZERO_AUDIO_TIME
    fade_in: ProjectTime = ZERO_AUDIO_TIME
    fade_out: ProjectTime = ZERO_AUDIO_TIME
    ducking_windows: tuple[DuckingWindow, ...] = ()

    def __post_init__(self) -> None:
        if self.source_in.nanoseconds < 0 or self.source_out <= self.source_in:
            raise ValueError("Audio source range must be positive and ordered.")
        if self.timeline_delay.nanoseconds < 0 or self.duration.nanoseconds <= 0:
            raise ValueError("Audio placement must have a non-negative delay and positive duration.")
        if type(self.stream_index) is not int or self.stream_index < 0:
            raise ValueError("Audio stream index must be a non-negative integer.")
        if type(self.muted) is not bool:
            raise TypeError("Audio mute must be a boolean value.")
        if not isinstance(self.bus_level, AudioLevel):
            raise TypeError("Audio bus level must be an AudioLevel value.")
        if self.clip_start.nanoseconds < 0:
            raise ValueError("Audio clip start cannot be negative.")
        if self.clip_duration == ZERO_AUDIO_TIME:
            object.__setattr__(self, "clip_duration", self.duration)
        if self.clip_duration.nanoseconds <= 0:
            raise ValueError("Audio clip duration must be positive.")
        if self.fade_in.nanoseconds < 0 or self.fade_out.nanoseconds < 0:
            raise ValueError("Audio fades cannot be negative.")
        if self.fade_in + self.fade_out > self.clip_duration:
            raise ValueError("Audio fades cannot exceed the clip duration.")
        if type(self.ducking_windows) is not tuple or any(
            not isinstance(window, DuckingWindow) for window in self.ducking_windows
        ):
            raise TypeError("Ducking windows must be an immutable tuple.")


@dataclass(frozen=True, slots=True)
class AudioGraph:
    """A deterministic mix plan for one half-open project time range."""

    project_id: str
    start: ProjectTime
    duration: ProjectTime
    sources: tuple[AudioSource, ...]
    sample_rate: int = OUTPUT_SAMPLE_RATE
    channels: int = OUTPUT_CHANNELS

    def __post_init__(self) -> None:
        if self.start.nanoseconds < 0 or self.duration.nanoseconds <= 0:
            raise ValueError("Audio graph range must be non-negative and positive.")
        if self.sample_rate != OUTPUT_SAMPLE_RATE or self.channels != OUTPUT_CHANNELS:
            raise ValueError("Audio graphs use the fixed 48 kHz stereo output format.")
        if type(self.sources) is not tuple or any(
            not isinstance(source, AudioSource) for source in self.sources
        ):
            raise TypeError("Audio graph sources must be an immutable AudioSource tuple.")

    @property
    def end(self) -> ProjectTime:
        return self.start + self.duration

    @property
    def output_frames(self) -> int:
        return audio_frames_for_time(self.duration, self.sample_rate)

    @property
    def cache_key(self) -> tuple[object, ...]:
        return (
            self.project_id,
            self.start.nanoseconds,
            self.duration.nanoseconds,
            self.sources,
            self.sample_rate,
            self.channels,
        )


def _nearest_non_negative_integer(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + (1 if remainder * 2 >= value.denominator else 0)


def audio_frames_for_time(value: ProjectTime, sample_rate: int = OUTPUT_SAMPLE_RATE) -> int:
    """Convert a non-negative project time to a nearest sample boundary."""

    if value.nanoseconds < 0:
        raise ValueError("Audio sample time cannot be negative.")
    if type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError("Audio sample rate must be a positive integer.")
    return _nearest_non_negative_integer(value.to_fractional_seconds() * sample_rate)


def _audio_stream(media: MediaReference) -> MediaStream | None:
    if media.kind is MediaKind.AUDIO and media.primary_stream_index is not None:
        primary = next(
            (
                stream
                for stream in media.streams
                if stream.index == media.primary_stream_index
                and stream.kind is MediaStreamKind.AUDIO
            ),
            None,
        )
        if primary is not None:
            return primary
    return next(
        (stream for stream in media.streams if stream.kind is MediaStreamKind.AUDIO),
        None,
    )


def _source_time(
    source_in: ProjectTime,
    local_time: ProjectTime,
    rate: PlaybackRate,
) -> ProjectTime:
    return source_in + ProjectTime.from_seconds(
        local_time.to_fractional_seconds() * rate.fraction
    )


def _ducking_windows(project: Project) -> tuple[DuckingWindow, ...]:
    windows: list[DuckingWindow] = []
    for clip in project.track(TrackKind.NARRATION).clips:
        if (
            clip.ducking is DuckingPreset.OFF
            or clip.audio_muted
            or clip.audio_level.percent == 0
            or project.mixer.narration.percent == 0
        ):
            continue
        windows.append(DuckingWindow(clip.timeline_start, clip.timeline_end, clip.ducking))
    return tuple(windows)


def build_audio_graph(
    project: Project,
    *,
    start: ProjectTime | None = None,
    duration: ProjectTime | None = None,
) -> AudioGraph:
    """Interpret original sound and music over one project range."""

    graph_start = start if start is not None else ProjectTime.zero()
    if graph_start.nanoseconds < 0 or graph_start > project.duration:
        raise AudioGraphError(
            AudioGraphErrorCode.INVALID_RANGE,
            "오디오 시작 위치가 프로젝트 범위를 벗어났습니다.",
        )
    available = project.duration - graph_start
    graph_duration = available if duration is None else min(duration, available)
    if graph_duration.nanoseconds <= 0:
        raise AudioGraphError(
            AudioGraphErrorCode.INVALID_RANGE,
            "재생할 오디오 구간의 길이는 0보다 커야 합니다.",
        )
    graph_end = graph_start + graph_duration
    sources: list[AudioSource] = []

    ducking_windows = _ducking_windows(project)
    for track_kind in (TrackKind.VISUAL, TrackKind.MUSIC, TrackKind.NARRATION):
        for clip in project.track(track_kind).clips:
            media = project.media_reference(clip.asset_id or "")
            if track_kind is TrackKind.VISUAL and media.kind is not MediaKind.VIDEO:
                continue
            overlap_start = max(graph_start, clip.timeline_start)
            overlap_end = min(graph_end, clip.timeline_end)
            if overlap_end <= overlap_start or clip.source_out is None:
                continue
            stream = _audio_stream(media)
            if stream is None:
                if media.kind is MediaKind.AUDIO:
                    raise AudioGraphError(
                        AudioGraphErrorCode.NO_AUDIO_STREAM,
                        f"{media.name}에서 사용할 오디오 스트림을 찾을 수 없습니다.",
                    )
                continue
            if not MIN_AUDIO_RATE <= clip.playback_rate.fraction <= MAX_AUDIO_RATE:
                raise AudioGraphError(
                    AudioGraphErrorCode.UNSUPPORTED_SPEED,
                    f"{clip.label}의 재생 속도는 오디오 미리 듣기에서 지원하지 않습니다.",
                )
            source_start = _source_time(
                clip.source_in,
                overlap_start - clip.timeline_start,
                clip.playback_rate,
            )
            source_end = _source_time(
                clip.source_in,
                overlap_end - clip.timeline_start,
                clip.playback_rate,
            )
            source_end = min(source_end, clip.source_out)
            sources.append(
                AudioSource(
                    asset_id=media.asset_id,
                    clip_id=clip.clip_id,
                    kind=(
                        AudioSourceKind.ORIGINAL
                        if track_kind is TrackKind.VISUAL
                        else (
                            AudioSourceKind.MUSIC
                            if track_kind is TrackKind.MUSIC
                            else AudioSourceKind.NARRATION
                        )
                    ),
                    source_path=media.source_path,
                    stream_index=stream.index,
                    source_in=source_start,
                    source_out=source_end,
                    timeline_delay=overlap_start - graph_start,
                    duration=overlap_end - overlap_start,
                    playback_rate=clip.playback_rate,
                    level=clip.audio_level,
                    muted=clip.audio_muted,
                    bus_level={
                        TrackKind.VISUAL: project.mixer.original,
                        TrackKind.MUSIC: project.mixer.music,
                        TrackKind.NARRATION: project.mixer.narration,
                    }[track_kind],
                    clip_start=clip.timeline_start,
                    clip_duration=clip.duration,
                    fade_in=clip.fade_in,
                    fade_out=clip.fade_out,
                    ducking_windows=(
                        ducking_windows if track_kind is TrackKind.MUSIC else ()
                    ),
                )
            )

    return AudioGraph(
        project_id=project.project_id,
        start=graph_start,
        duration=graph_duration,
        sources=tuple(sources),
    )


def format_audio_time(value: ProjectTime) -> str:
    """Return an exact non-scientific second value with nanosecond precision."""

    sign = "-" if value.nanoseconds < 0 else ""
    seconds, remainder = divmod(abs(value.nanoseconds), 1_000_000_000)
    if remainder == 0:
        return f"{sign}{seconds}"
    return f"{sign}{seconds}.{remainder:09d}".rstrip("0")


def _format_fraction(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 24
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
    return format(decimal, ".12f").rstrip("0").rstrip(".")


def _delay_samples(value: ProjectTime, sample_rate: int) -> int:
    return audio_frames_for_time(value, sample_rate)


def _expression_call(name: str, *arguments: str) -> str:
    return f"{name}({r'\,'.join(arguments)})"


def _fade_expression(graph: AudioGraph, source: AudioSource) -> str:
    overlap_start = graph.start + source.timeline_delay
    offset = overlap_start - source.clip_start
    factors: list[str] = []
    if source.fade_in.nanoseconds > 0:
        progress = (
            f"(t+{format_audio_time(offset)})/"
            f"{format_audio_time(source.fade_in)}"
        )
        factors.append(
            _expression_call("max", "0", _expression_call("min", "1", progress))
        )
    if source.fade_out.nanoseconds > 0:
        remaining = (
            f"({format_audio_time(source.clip_duration)}-t-"
            f"{format_audio_time(offset)})/{format_audio_time(source.fade_out)}"
        )
        factors.append(
            _expression_call("max", "0", _expression_call("min", "1", remaining))
        )
    return "*".join(factors) or "1"


_DUCKING_LEVEL = {
    DuckingPreset.LIGHT: Fraction(65, 100),
    DuckingPreset.MEDIUM: Fraction(40, 100),
    DuckingPreset.STRONG: Fraction(20, 100),
}


def _ducking_window_expression(
    graph: AudioGraph,
    source: AudioSource,
    window: DuckingWindow,
) -> str:
    source_start = graph.start + source.timeline_delay
    attack_start = window.start - window.attack - source_start
    attack_end = window.start - source_start
    release_start = window.end - source_start
    release_end = window.end + window.release - source_start
    start = format_audio_time(attack_start)
    onset = format_audio_time(attack_end)
    end = format_audio_time(release_start)
    released = format_audio_time(release_end)
    gain = _format_fraction(_DUCKING_LEVEL[window.preset])
    attack = f"1-(1-{gain})*(t-({start}))/{format_audio_time(window.attack)}"
    release = f"{gain}+(1-{gain})*(t-({end}))/{format_audio_time(window.release)}"
    return _expression_call(
        "if",
        rf"lt(t\,{start})",
        "1",
        _expression_call(
            "if",
            rf"lt(t\,{onset})",
            attack,
            _expression_call(
                "if",
                rf"lt(t\,{end})",
                gain,
                _expression_call("if", rf"lt(t\,{released})", release, "1"),
            ),
        ),
    )


def _source_volume_expression(graph: AudioGraph, source: AudioSource) -> str:
    clip_gain = Fraction(0) if source.muted else source.level.fraction
    bus_gain = source.bus_level.fraction
    factors = [
        _format_fraction(clip_gain),
        _format_fraction(bus_gain),
        _fade_expression(graph, source),
    ]
    ducking = [
        _ducking_window_expression(graph, source, window)
        for window in source.ducking_windows
    ]
    if ducking:
        factors.append(
            ducking[0]
            if len(ducking) == 1
            else _expression_call("min", *ducking)
        )
    return "*".join(factors)


def _source_volume_filter(graph: AudioGraph, source: AudioSource) -> str:
    gain = (
        Fraction(0)
        if source.muted
        else source.level.fraction * source.bus_level.fraction
    )
    if (
        source.fade_in == ZERO_AUDIO_TIME
        and source.fade_out == ZERO_AUDIO_TIME
        and not source.ducking_windows
    ):
        return f"volume={_format_fraction(gain)}"
    return f"volume='{_source_volume_expression(graph, source)}':eval=frame"


def ffmpeg_audio_filter(
    graph: AudioGraph,
    *,
    input_offset: int = 0,
    output_label: str = "aout",
) -> str:
    """Build the shared normalization, placement, mix, and limiting graph."""

    if type(input_offset) is not int or input_offset < 0:
        raise ValueError("Audio input offset must be a non-negative integer.")
    if not output_label or not output_label.isalnum():
        raise ValueError("Audio output label must be a non-empty alphanumeric value.")

    duration_text = format_audio_time(graph.duration)
    filters: list[str] = []
    labels: list[str] = []
    for index, source in enumerate(graph.sources):
        label = f"a{index}"
        labels.append(label)
        chain = [
            (
                f"[{index + input_offset}:{source.stream_index}]atrim="
                f"start={format_audio_time(source.source_in)}:"
                f"end={format_audio_time(source.source_out)}"
            ),
            "asetpts=PTS-STARTPTS",
        ]
        if source.playback_rate.fraction != 1:
            chain.append(f"atempo={_format_fraction(source.playback_rate.fraction)}")
        chain.extend(
            (
                f"aresample={graph.sample_rate}",
                (
                    f"aformat=sample_fmts=s16:sample_rates={graph.sample_rate}:"
                    "channel_layouts=stereo"
                ),
                _source_volume_filter(graph, source),
                (
                    f"adelay=delays="
                    f"{_delay_samples(source.timeline_delay, graph.sample_rate)}S:all=1"
                    f"[{label}]"
                ),
            )
        )
        filters.append(",".join(chain))

    if labels:
        inputs = "".join(f"[{label}]" for label in labels)
        if len(labels) == 1:
            mix = f"{inputs}anull"
        else:
            mix = (
                f"{inputs}amix=inputs={len(labels)}:normalize=0:"
                "dropout_transition=0"
            )
        filters.append(
            f"{mix},alimiter=limit=0.95:level=false:latency=true,"
            f"apad=whole_dur={duration_text},atrim=duration={duration_text},"
            f"asetpts=PTS-STARTPTS[{output_label}]"
        )
    else:
        filters.append(
            f"anullsrc=r={graph.sample_rate}:cl=stereo,"
            f"atrim=duration={duration_text},asetpts=PTS-STARTPTS[{output_label}]"
        )
    return ";".join(filters)


def ffmpeg_audio_arguments(executable: str, graph: AudioGraph) -> tuple[str, ...]:
    """Build shell-free argv for signed 16-bit stereo PCM output."""

    arguments: list[str] = [executable, "-v", "error", "-nostdin"]
    for source in graph.sources:
        arguments.extend(("-i", source.source_path))
    arguments.extend(
        (
            "-filter_complex",
            ffmpeg_audio_filter(graph),
            "-map",
            "[aout]",
            "-vn",
            "-sn",
            "-dn",
            "-f",
            "s16le",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(graph.sample_rate),
            "-ac",
            str(graph.channels),
            "pipe:1",
        )
    )
    return tuple(arguments)
