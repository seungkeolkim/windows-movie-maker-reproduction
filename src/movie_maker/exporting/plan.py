"""Deterministic, UI-independent FFmpeg export planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from movie_maker.audio import AudioGraph, AudioGraphError, build_audio_graph, ffmpeg_audio_filter
from movie_maker.project import (
    FrameRate,
    MediaKind,
    MediaStreamKind,
    PlaybackRate,
    Project,
    ProjectTime,
    TrackKind,
)

DEFAULT_EXPORT_FRAME_RATE = FrameRate(30, 1)
NEUTRAL_BACKGROUND = "0x20242b"


class ExportPreset(str, Enum):
    """MVP output-size choices."""

    ORIGINAL = "original"
    HD_720 = "720p"
    HD_1080 = "1080p"


class ExportPlanErrorCode(str, Enum):
    """Failures detected before an export process is started."""

    EMPTY_TIMELINE = "empty_timeline"
    INVALID_CANVAS = "invalid_canvas"
    INVALID_TARGET = "invalid_target"
    NO_VIDEO_STREAM = "no_video_stream"
    INVALID_AUDIO_GRAPH = "invalid_audio_graph"


class ExportPlanError(ValueError):
    """A typed, user-correctable render-plan failure."""

    def __init__(self, code: ExportPlanErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VideoSource:
    """One visual clip and its exact source and timeline ranges."""

    asset_id: str
    clip_id: str
    label: str
    source_path: str
    media_kind: MediaKind
    stream_index: int
    source_in: ProjectTime
    source_out: ProjectTime | None
    duration: ProjectTime
    playback_rate: PlaybackRate

    def __post_init__(self) -> None:
        if self.media_kind not in {MediaKind.VIDEO, MediaKind.PHOTO}:
            raise ValueError("Video export sources must be video or photo media.")
        if type(self.stream_index) is not int or self.stream_index < 0:
            raise ValueError("Video stream index must be a non-negative integer.")
        if self.duration.nanoseconds <= 0:
            raise ValueError("Video source duration must be positive.")
        if self.media_kind is MediaKind.VIDEO and self.source_out is None:
            raise ValueError("Video sources require a source end.")


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """An immutable snapshot of one complete MP4 rendering request."""

    project: Project
    target_path: str
    preset: ExportPreset
    overwrite_existing: bool
    width: int
    height: int
    frame_rate: FrameRate
    duration: ProjectTime
    video_sources: tuple[VideoSource, ...]
    audio_graph: AudioGraph

    def __post_init__(self) -> None:
        if not self.video_sources or self.duration.nanoseconds <= 0:
            raise ValueError("Export plans require a non-empty visual timeline.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Export dimensions must be positive.")
        if self.audio_graph.project_id != self.project.project_id:
            raise ValueError("The audio graph must belong to the export project snapshot.")

    @property
    def output_frame_count(self) -> int:
        """Return the number of CFR frames whose timestamps fall in the project range."""

        frames = (
            self.duration.to_fractional_seconds()
            * self.frame_rate.numerator
            / self.frame_rate.denominator
        )
        return -(-frames.numerator // frames.denominator)


def _output_size(project: Project, preset: ExportPreset) -> tuple[int, int]:
    if preset is ExportPreset.HD_720:
        return 1280, 720
    if preset is ExportPreset.HD_1080:
        return 1920, 1080
    width, height = project.canvas.width, project.canvas.height
    if width is None or height is None:
        raise ExportPlanError(
            ExportPlanErrorCode.INVALID_CANVAS,
            "프로젝트의 원본 유지 화면 크기가 없습니다. 화면 설정을 확인하세요.",
        )
    return width, height


def _video_stream_index(project: Project, asset_id: str) -> int:
    media = project.media_reference(asset_id)
    primary = next(
        (
            stream
            for stream in media.streams
            if stream.index == media.primary_stream_index
            and stream.kind is MediaStreamKind.VIDEO
        ),
        None,
    )
    if primary is None:
        raise ExportPlanError(
            ExportPlanErrorCode.NO_VIDEO_STREAM,
            f"{media.name}에서 출력할 영상 또는 사진 스트림을 찾을 수 없습니다.",
        )
    return primary.index


def build_export_plan(
    project: Project,
    target_path: str,
    preset: ExportPreset,
    *,
    overwrite_existing: bool = False,
    frame_rate: FrameRate = DEFAULT_EXPORT_FRAME_RATE,
) -> ExportPlan:
    """Build a render plan from one validated immutable project snapshot."""

    visual_clips = project.track(TrackKind.VISUAL).clips
    if not visual_clips:
        raise ExportPlanError(
            ExportPlanErrorCode.EMPTY_TIMELINE,
            "동영상 저장에는 하나 이상의 영상 또는 사진 클립이 필요합니다.",
        )
    if not isinstance(preset, ExportPreset):
        raise ExportPlanError(
            ExportPlanErrorCode.INVALID_TARGET,
            "지원하지 않는 출력 크기입니다.",
        )
    if type(overwrite_existing) is not bool:
        raise TypeError("overwrite_existing must be a boolean value.")
    if not target_path.strip() or Path(target_path).suffix.casefold() != ".mp4":
        raise ExportPlanError(
            ExportPlanErrorCode.INVALID_TARGET,
            "출력 경로에는 .mp4 파일 이름이 필요합니다.",
        )
    width, height = _output_size(project, preset)
    sources: list[VideoSource] = []
    for clip in visual_clips:
        if clip.asset_id is None:
            raise ExportPlanError(
                ExportPlanErrorCode.NO_VIDEO_STREAM,
                f"{clip.label}의 원본 미디어 참조가 없습니다.",
            )
        media = project.media_reference(clip.asset_id)
        sources.append(
            VideoSource(
                asset_id=media.asset_id,
                clip_id=clip.clip_id,
                label=clip.label,
                source_path=media.source_path,
                media_kind=media.kind,
                stream_index=_video_stream_index(project, media.asset_id),
                source_in=clip.source_in,
                source_out=clip.source_out,
                duration=clip.duration,
                playback_rate=clip.playback_rate,
            )
        )
    try:
        audio_graph = build_audio_graph(project)
    except AudioGraphError as error:
        raise ExportPlanError(ExportPlanErrorCode.INVALID_AUDIO_GRAPH, str(error)) from error
    return ExportPlan(
        project=project,
        target_path=str(Path(target_path).expanduser().resolve(strict=False)),
        preset=preset,
        overwrite_existing=overwrite_existing,
        width=width,
        height=height,
        frame_rate=frame_rate,
        duration=project.duration,
        video_sources=tuple(sources),
        audio_graph=audio_graph,
    )


def _format_rate(rate: FrameRate) -> str:
    return f"{rate.numerator}/{rate.denominator}"


def _setpts_rate(rate: PlaybackRate) -> str:
    return f"{rate.denominator}/{rate.numerator}"


def _pixel_format(plan: ExportPlan) -> str:
    # 4:2:0 requires even dimensions. Preserve an odd stored original canvas exactly
    # with 4:4:4 instead of silently resizing the user's project.
    return "yuv420p" if plan.width % 2 == 0 and plan.height % 2 == 0 else "yuv444p"


def _video_filter(plan: ExportPlan) -> str:
    from movie_maker.audio import format_audio_time

    filters: list[str] = []
    labels: list[str] = []
    for index, source in enumerate(plan.video_sources):
        label = f"v{index}"
        labels.append(label)
        if source.media_kind is MediaKind.PHOTO:
            timing = f"trim=duration={format_audio_time(source.duration)}"
        else:
            assert source.source_out is not None
            timing = (
                f"trim=start={format_audio_time(source.source_in)}:"
                f"end={format_audio_time(source.source_out)},"
                f"setpts=(PTS-STARTPTS)*{_setpts_rate(source.playback_rate)}"
            )
        chain = (
            f"[{index}:{source.stream_index}]{timing},"
            f"fps=fps={_format_rate(plan.frame_rate)}:start_time=0:round=near,"
            f"scale={plan.width}:{plan.height}:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2:reset_sar=1,"
            f"pad={plan.width}:{plan.height}:(ow-iw)/2:(oh-ih)/2:color={NEUTRAL_BACKGROUND},"
            f"setsar=1,format={_pixel_format(plan)}[{label}]"
        )
        filters.append(chain)
    inputs = "".join(f"[{label}]" for label in labels)
    duration = format_audio_time(plan.duration)
    filters.append(
        f"{inputs}concat=n={len(labels)}:v=1:a=0,"
        f"trim=duration={duration},setpts=PTS-STARTPTS[vout]"
    )
    return ";".join(filters)


def ffmpeg_export_filter(plan: ExportPlan) -> str:
    """Return the shared visual and W-06 audio filter graph."""

    video = _video_filter(plan)
    audio = ffmpeg_audio_filter(
        plan.audio_graph,
        input_offset=len(plan.video_sources),
        output_label="aout",
    )
    return f"{video};{audio}"


def ffmpeg_export_arguments(
    executable: str,
    plan: ExportPlan,
    temporary_path: str,
) -> tuple[str, ...]:
    """Build shell-free FFmpeg argv for an H.264/AAC MP4 temporary output."""

    from movie_maker.audio import format_audio_time

    arguments: list[str] = [executable, "-v", "error", "-nostdin", "-y"]
    for source in plan.video_sources:
        if source.media_kind is MediaKind.PHOTO:
            arguments.extend(
                (
                    "-loop",
                    "1",
                    "-framerate",
                    _format_rate(plan.frame_rate),
                    "-t",
                    format_audio_time(source.duration),
                )
            )
        arguments.extend(("-i", source.source_path))
    for audio_source in plan.audio_graph.sources:
        arguments.extend(("-i", audio_source.source_path))
    arguments.extend(
        (
            "-filter_complex",
            ffmpeg_export_filter(plan),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            _pixel_format(plan),
            "-r",
            _format_rate(plan.frame_rate),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            str(plan.audio_graph.sample_rate),
            "-ac",
            str(plan.audio_graph.channels),
            "-t",
            format_audio_time(plan.duration),
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-movflags",
            "+faststart",
            "-stats_period",
            "0.1",
            "-progress",
            "pipe:1",
            temporary_path,
        )
    )
    return tuple(arguments)
