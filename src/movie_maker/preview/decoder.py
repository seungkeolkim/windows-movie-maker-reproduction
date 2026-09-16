"""Cancellable FFmpeg decoding for one visual preview frame."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from pathlib import Path
from threading import Event
from typing import Protocol

from movie_maker.audio import format_audio_time
from movie_maker.creative.composition import (
    CompositionError,
    build_composition_plan,
    composition_video_filter,
)
from movie_maker.media.process import resolve_media_tool
from movie_maker.preview.timeline import FrameTarget
from movie_maker.project import (
    DEFAULT_BRIGHTNESS,
    FitMode,
    MediaKind,
    ProjectTime,
    TrackKind,
    UserRotation,
    VisualEffectPreset,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PreviewDecodeErrorCode(str, Enum):
    SOURCE_NOT_FOUND = "source_not_found"
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"
    TIMEOUT = "timeout"
    PROCESS_FAILED = "process_failed"
    INVALID_FRAME = "invalid_frame"
    INVALID_COMPOSITION = "invalid_composition"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    target: FrameTarget
    png_bytes: bytes


@dataclass(frozen=True, slots=True)
class PreviewDecodeFailure:
    target: FrameTarget
    code: PreviewDecodeErrorCode
    message: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PreviewDecodeCancelled:
    target: FrameTarget


type PreviewDecodeResult = DecodedFrame | PreviewDecodeFailure | PreviewDecodeCancelled


class RunningProcess(Protocol):
    returncode: int | None

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class ProcessLauncher(Protocol):
    def __call__(self, arguments: Sequence[str]) -> RunningProcess: ...


def _launch_process(arguments: Sequence[str]) -> RunningProcess:
    return subprocess.Popen(
        tuple(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


def format_ffmpeg_timestamp(target: FrameTarget) -> str:
    """Return an exact, non-scientific relative source timestamp."""

    nanoseconds = target.source_time.nanoseconds
    seconds, remainder = divmod(nanoseconds, 1_000_000_000)
    if remainder == 0:
        return str(seconds)
    return f"{seconds}.{remainder:09d}".rstrip("0")


def _can_decode_source_directly(target: FrameTarget) -> bool:
    """Return whether the current project frame is identical to a direct source frame."""

    project = target.project
    position = target.project_position
    if project is None or position is None or target.media_kind is not MediaKind.VIDEO:
        return project is None
    try:
        clip = project.clip(target.clip_id)
    except KeyError:
        return False
    if (
        clip.track is not TrackKind.VISUAL
        or clip.fit_mode is not FitMode.FIT
        or clip.user_rotation is not UserRotation.NONE
        or clip.brightness != DEFAULT_BRIGHTNESS
        or clip.effect_preset is not VisualEffectPreset.NONE
        or project.transitions
    ):
        return False
    return not any(
        text_clip.timeline_start <= position < text_clip.timeline_end
        and text_clip.text is not None
        and bool(text_clip.text.content)
        for text_clip in project.track(TrackKind.TEXT).clips
    )


def ffmpeg_frame_arguments(executable: str, target: FrameTarget) -> tuple[str, ...]:
    """Build the shell-free one-frame decoding argv."""

    if not _can_decode_source_directly(target):
        return ffmpeg_composition_frame_arguments(executable, target)
    arguments = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-ss",
        format_ffmpeg_timestamp(target),
        "-i",
        target.source_path,
        "-map",
        f"0:{target.stream_index}",
    ]
    if target.project is not None:
        media = target.project.media_reference(target.asset_id)
        width = target.project.canvas.width or media.width
        height = target.project.canvas.height or media.height
        if width is not None and height is not None:
            arguments.extend(
                (
                    "-vf",
                    (
                        f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
                        "force_divisible_by=2:reset_sar=1,"
                        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x20242b,setsar=1"
                    ),
                )
            )
    arguments.extend(
        (
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-f",
        "image2pipe",
        "-c:v",
        "png",
        "pipe:1",
        )
    )
    return tuple(arguments)


def ffmpeg_composition_frame_arguments(
    executable: str,
    target: FrameTarget,
) -> tuple[str, ...]:
    """Build a one-frame argv from the same composition plan as export."""

    project = target.project
    position = target.project_position
    if project is None or position is None:
        raise CompositionError("A composition preview requires a project and position.")
    width = project.canvas.width
    height = project.canvas.height
    if width is None or height is None:
        media = project.media_reference(target.asset_id)
        width, height = media.width, media.height
    if width is None or height is None:
        raise CompositionError("미리 보기 화면 크기를 결정할 수 없습니다.")
    plan = build_composition_plan(project, width=width, height=height)
    frame_duration = ProjectTime.from_seconds(
        Fraction(plan.frame_rate.denominator, plan.frame_rate.numerator)
    )
    render_position = min(position, max(ProjectTime.zero(), plan.duration - frame_duration))
    arguments: list[str] = [executable, "-v", "error", "-nostdin"]
    for source in plan.sources:
        if source.media_kind is MediaKind.PHOTO:
            arguments.extend(
                (
                    "-loop",
                    "1",
                    "-framerate",
                    f"{plan.frame_rate.numerator}/{plan.frame_rate.denominator}",
                    "-t",
                    format_audio_time(source.duration),
                )
            )
        arguments.extend(("-i", source.source_path))
    base = composition_video_filter(plan, output_label="composition")
    frame_filter = (
        f"[composition]trim=start={format_audio_time(render_position)}:"
        f"duration={format_audio_time(frame_duration)},"
        "setpts=PTS-STARTPTS[vout]"
    )
    arguments.extend(
        (
            "-filter_complex",
            f"{base};{frame_filter}",
            "-map",
            "[vout]",
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "pipe:1",
        )
    )
    return tuple(arguments)


def _detail(stderr: bytes) -> str | None:
    text = stderr.decode("utf-8", errors="replace").strip()
    return text[-2000:] or None


def _stop_process(process: RunningProcess) -> tuple[bytes, bytes]:
    process.terminate()
    try:
        return process.communicate(timeout=0.25)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate()


class FfmpegFrameDecoder:
    """Decode one frame while allowing another thread to cancel the process."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout: float = 10.0,
        launcher: ProcessLauncher = _launch_process,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._executable = executable or resolve_media_tool("ffmpeg")
        self._timeout = timeout
        self._launcher = launcher
        self._monotonic = monotonic

    def decode(self, target: FrameTarget, cancelled: Event) -> PreviewDecodeResult:
        source_paths = [target.source_path]
        if target.project is not None:
            source_paths = [
                media.source_path
                for media in target.project.media
                if media.kind in {MediaKind.VIDEO, MediaKind.PHOTO}
            ]
        missing_source = next(
            (source_path for source_path in source_paths if not Path(source_path).is_file()),
            None,
        )
        if missing_source is not None:
            return PreviewDecodeFailure(
                target,
                PreviewDecodeErrorCode.SOURCE_NOT_FOUND,
                "원본 파일을 찾을 수 없습니다. 누락 미디어에서 다시 연결하세요. "
                f"({missing_source})",
            )
        if cancelled.is_set():
            return PreviewDecodeCancelled(target)

        try:
            arguments = ffmpeg_frame_arguments(self._executable, target)
        except CompositionError as error:
            return PreviewDecodeFailure(
                target,
                PreviewDecodeErrorCode.INVALID_COMPOSITION,
                str(error),
            )
        try:
            process = self._launcher(arguments)
        except FileNotFoundError as error:
            return PreviewDecodeFailure(
                target,
                PreviewDecodeErrorCode.FFMPEG_NOT_FOUND,
                "FFmpeg를 찾을 수 없습니다. 실행 환경을 점검하세요.",
                str(error),
            )
        except OSError as error:
            return PreviewDecodeFailure(
                target,
                PreviewDecodeErrorCode.INTERNAL_ERROR,
                "미리 보기 디코더를 시작하지 못했습니다. 다시 시도하세요.",
                str(error),
            )

        deadline = self._monotonic() + self._timeout
        while True:
            if cancelled.is_set():
                _stop_process(process)
                return PreviewDecodeCancelled(target)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                _, stderr = _stop_process(process)
                return PreviewDecodeFailure(
                    target,
                    PreviewDecodeErrorCode.TIMEOUT,
                    "프레임을 읽는 시간이 초과됐습니다. 다시 시도하세요.",
                    _detail(stderr),
                )
            try:
                stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
            except OSError as error:
                _stop_process(process)
                return PreviewDecodeFailure(
                    target,
                    PreviewDecodeErrorCode.INTERNAL_ERROR,
                    "프레임을 읽는 중 오류가 발생했습니다. 다시 시도하세요.",
                    str(error),
                )

        if process.returncode != 0:
            return PreviewDecodeFailure(
                target,
                PreviewDecodeErrorCode.PROCESS_FAILED,
                "미디어를 디코딩할 수 없습니다. 파일과 FFmpeg 지원을 확인하세요.",
                _detail(stderr),
            )
        if not stdout.startswith(PNG_SIGNATURE):
            return PreviewDecodeFailure(
                target,
                PreviewDecodeErrorCode.INVALID_FRAME,
                "이 위치의 영상 프레임을 표시할 수 없습니다.",
                _detail(stderr),
            )
        return DecodedFrame(target, stdout)
