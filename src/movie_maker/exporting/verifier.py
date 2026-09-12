"""ffprobe metadata and full decode verification for temporary MP4 output."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from threading import Event
from typing import Protocol

from movie_maker.exporting.contracts import ExportErrorCode, ExportVerification
from movie_maker.exporting.plan import ExportPlan
from movie_maker.media.process import resolve_media_tool
from movie_maker.project import ProjectTime


class ExportVerificationError(RuntimeError):
    def __init__(
        self,
        code: ExportErrorCode,
        message: str,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class ExportVerificationCancelled(RuntimeError):
    pass


class VerificationProcess(Protocol):
    returncode: int | None

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class VerificationLauncher(Protocol):
    def __call__(self, arguments: Sequence[str]) -> VerificationProcess: ...


def _launch(arguments: Sequence[str]) -> VerificationProcess:
    return subprocess.Popen(
        tuple(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


def _limited_detail(value: bytes) -> str | None:
    detail = value.decode("utf-8", errors="replace").strip()
    return detail[-2000:] or None


def _stop(process: VerificationProcess) -> None:
    try:
        process.terminate()
    except OSError:
        if process.returncode is not None:
            return
    try:
        process.communicate(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            if process.returncode is not None:
                return
        try:
            process.communicate()
        except OSError:
            pass


def _communicate(
    process: VerificationProcess,
    cancelled: Event,
    *,
    timeout: float,
) -> tuple[bytes, bytes]:
    elapsed = 0.0
    while True:
        if cancelled.is_set():
            _stop(process)
            raise ExportVerificationCancelled
        if elapsed >= timeout:
            _stop(process)
            raise ExportVerificationError(
                ExportErrorCode.TIMEOUT,
                "완성 파일을 검증하는 시간이 초과됐습니다.",
            )
        wait = min(0.05, timeout - elapsed)
        try:
            return process.communicate(timeout=wait)
        except subprocess.TimeoutExpired:
            elapsed += wait


class ExportVerifier(Protocol):
    def verify(
        self,
        plan: ExportPlan,
        temporary_path: str,
        cancelled: Event,
    ) -> ExportVerification: ...


@dataclass(slots=True)
class FfprobeExportVerifier:
    """Require MP4/H.264/AAC metadata and a successful full decode."""

    ffprobe_executable: str | None = None
    ffmpeg_executable: str | None = None
    launcher: VerificationLauncher = _launch
    timeout: float = 60.0

    def verify(
        self,
        plan: ExportPlan,
        temporary_path: str,
        cancelled: Event,
    ) -> ExportVerification:
        ffprobe = self.ffprobe_executable or resolve_media_tool("ffprobe")
        probe_arguments = (
            ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-count_frames",
            "-of",
            "json",
            temporary_path,
        )
        try:
            process = self.launcher(probe_arguments)
        except FileNotFoundError as error:
            raise ExportVerificationError(
                ExportErrorCode.FFPROBE_NOT_FOUND,
                "ffprobe를 찾을 수 없어 완성 파일을 검증하지 못했습니다.",
                str(error),
            ) from error
        stdout, stderr = _communicate(process, cancelled, timeout=self.timeout)
        if process.returncode != 0:
            raise ExportVerificationError(
                ExportErrorCode.VALIDATION_FAILED,
                "ffprobe가 완성 파일을 읽지 못했습니다.",
                _limited_detail(stderr),
            )
        try:
            root = json.loads(stdout.decode("utf-8"))
            streams = root["streams"]
            format_value = root["format"]
            format_name = str(format_value["format_name"])
            duration_decimal = Decimal(str(format_value["duration"]))
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError, InvalidOperation) as error:
            raise ExportVerificationError(
                ExportErrorCode.VALIDATION_FAILED,
                "완성 파일의 ffprobe 정보가 올바르지 않습니다.",
                str(error),
            ) from error
        video = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            None,
        )
        audio = next(
            (stream for stream in streams if stream.get("codec_type") == "audio"),
            None,
        )
        if "mp4" not in format_name or video is None or audio is None:
            raise ExportVerificationError(
                ExportErrorCode.VALIDATION_FAILED,
                "완성 파일에 MP4 영상과 오디오 스트림이 모두 있어야 합니다.",
            )
        try:
            width = int(video["width"])
            height = int(video["height"])
            frame_count = int(video["nb_read_frames"])
            video_codec = str(video["codec_name"])
            audio_codec = str(audio["codec_name"])
        except (KeyError, TypeError, ValueError) as error:
            raise ExportVerificationError(
                ExportErrorCode.VALIDATION_FAILED,
                "완성 파일의 스트림 정보가 불완전합니다.",
                str(error),
            ) from error
        duration = ProjectTime.from_seconds(duration_decimal)
        tolerance_ns = max(
            100_000_000,
            ProjectTime.from_seconds(
                Fraction(
                    2 * plan.frame_rate.denominator,
                    plan.frame_rate.numerator,
                )
            ).nanoseconds,
        )
        if (
            video_codec != "h264"
            or audio_codec != "aac"
            or (width, height) != (plan.width, plan.height)
            or frame_count != plan.output_frame_count
            or abs(duration.nanoseconds - plan.duration.nanoseconds) > tolerance_ns
        ):
            raise ExportVerificationError(
                ExportErrorCode.VALIDATION_FAILED,
                "완성 파일의 코덱, 화면 크기, 프레임 수 또는 길이가 요청과 다릅니다.",
            )
        size = Path(temporary_path).stat().st_size
        if size <= 0:
            raise ExportVerificationError(
                ExportErrorCode.VALIDATION_FAILED,
                "완성된 동영상 파일이 비어 있습니다.",
            )

        ffmpeg = self.ffmpeg_executable or resolve_media_tool("ffmpeg")
        decode_arguments = (
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            temporary_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        )
        try:
            decode_process = self.launcher(decode_arguments)
        except FileNotFoundError as error:
            raise ExportVerificationError(
                ExportErrorCode.FFMPEG_NOT_FOUND,
                "FFmpeg를 찾을 수 없어 완성 파일을 디코딩하지 못했습니다.",
                str(error),
            ) from error
        _, decode_stderr = _communicate(decode_process, cancelled, timeout=self.timeout)
        if decode_process.returncode != 0:
            raise ExportVerificationError(
                ExportErrorCode.VALIDATION_FAILED,
                "완성 파일을 끝까지 디코딩할 수 없습니다.",
                _limited_detail(decode_stderr),
            )
        return ExportVerification(
            width=width,
            height=height,
            duration=duration,
            size_bytes=size,
            frame_count=frame_count,
            video_codec=video_codec,
            audio_codec=audio_codec,
        )
