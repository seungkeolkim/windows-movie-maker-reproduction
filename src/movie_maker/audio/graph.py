"""UI-independent project audio graph and reusable FFmpeg filter construction."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from fractions import Fraction

from movie_maker.project import (
    AudioLevel,
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

    def __post_init__(self) -> None:
        if self.source_in.nanoseconds < 0 or self.source_out <= self.source_in:
            raise ValueError("Audio source range must be positive and ordered.")
        if self.timeline_delay.nanoseconds < 0 or self.duration.nanoseconds <= 0:
            raise ValueError("Audio placement must have a non-negative delay and positive duration.")
        if type(self.stream_index) is not int or self.stream_index < 0:
            raise ValueError("Audio stream index must be a non-negative integer.")
        if type(self.muted) is not bool:
            raise TypeError("Audio mute must be a boolean value.")


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

    for track_kind in (TrackKind.VISUAL, TrackKind.MUSIC):
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
                        else AudioSourceKind.MUSIC
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

    seconds, remainder = divmod(value.nanoseconds, 1_000_000_000)
    if remainder == 0:
        return str(seconds)
    return f"{seconds}.{remainder:09d}".rstrip("0")


def _format_fraction(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 24
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
    return format(decimal, ".12f").rstrip("0").rstrip(".")


def _delay_samples(value: ProjectTime, sample_rate: int) -> int:
    return audio_frames_for_time(value, sample_rate)


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
                f"volume={0 if source.muted else _format_fraction(source.level.fraction)}",
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
