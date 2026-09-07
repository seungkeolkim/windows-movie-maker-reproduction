"""Typed ffprobe analysis for local media files."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import cast

from movie_maker.media.process import ProcessRunner, resolve_media_tool, run_process
from movie_maker.project import (
    FrameRate,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    ProjectTime,
)

VIDEO_EXTENSIONS = frozenset(
    {
        ".3gp",
        ".avi",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".webm",
        ".wmv",
    }
)
PHOTO_EXTENSIONS = frozenset(
    {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)
AUDIO_EXTENSIONS = frozenset(
    {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
)
SUPPORTED_MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | PHOTO_EXTENSIONS | AUDIO_EXTENSIONS


class MediaAnalysisErrorCode(str, Enum):
    """Stable categories for expected media-analysis failures."""

    SOURCE_NOT_FOUND = "source_not_found"
    UNSUPPORTED_TYPE = "unsupported_type"
    FFPROBE_NOT_FOUND = "ffprobe_not_found"
    TIMEOUT = "timeout"
    PROCESS_FAILED = "process_failed"
    INVALID_JSON = "invalid_json"
    INVALID_MEDIA = "invalid_media"


@dataclass(frozen=True, slots=True)
class MediaAnalysis:
    """Validated metadata that can be converted into a project media reference."""

    source_path: str
    name: str
    kind: MediaKind
    duration: ProjectTime | None
    width: int | None
    height: int | None
    primary_stream_index: int
    streams: tuple[MediaStream, ...]

    def to_media_reference(self, asset_id: str) -> MediaReference:
        """Build the W-01 persistent value without touching source bytes."""

        return MediaReference(
            asset_id=asset_id,
            name=self.name,
            source_path=self.source_path,
            kind=self.kind,
            duration=self.duration,
            width=self.width,
            height=self.height,
            primary_stream_index=self.primary_stream_index,
            streams=self.streams,
        )


@dataclass(frozen=True, slots=True)
class MediaAnalysisSuccess:
    """A successful and fully validated ffprobe result."""

    analysis: MediaAnalysis


@dataclass(frozen=True, slots=True)
class MediaAnalysisFailure:
    """An expected failure isolated to one selected source file."""

    source_path: str
    code: MediaAnalysisErrorCode
    message: str
    detail: str | None = None


type MediaAnalysisResult = MediaAnalysisSuccess | MediaAnalysisFailure


@dataclass(frozen=True, slots=True)
class _ParsedStream:
    metadata: MediaStream
    width: int | None
    height: int | None
    duration_seconds: Decimal | None
    attached_picture: bool


class _InvalidMedia(ValueError):
    pass


def canonical_source_path(source_path: str | os.PathLike[str]) -> str:
    """Return an absolute source path without requiring the file to exist."""

    return str(Path(source_path).expanduser().resolve(strict=False))


def canonical_source_key(source_path: str | os.PathLike[str]) -> str:
    """Return the platform-aware identity key used for duplicate detection."""

    return os.path.normcase(os.path.normpath(canonical_source_path(source_path)))


def media_kind_for_path(source_path: str | os.PathLike[str]) -> MediaKind | None:
    """Classify one supported path by its final suffix."""

    suffix = Path(source_path).suffix.casefold()
    if suffix in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    if suffix in PHOTO_EXTENSIONS:
        return MediaKind.PHOTO
    if suffix in AUDIO_EXTENSIONS:
        return MediaKind.AUDIO
    return None


class FfprobeAnalyzer:
    """Analyze one local file with ffprobe JSON output."""

    def __init__(
        self,
        executable: str | None = None,
        *,
        timeout_seconds: float = 15.0,
        runner: ProcessRunner = run_process,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("ffprobe timeout must be positive.")
        self._executable = executable or resolve_media_tool("ffprobe")
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def analyze(self, source_path: str | os.PathLike[str]) -> MediaAnalysisResult:
        """Return a typed result; expected per-file failures never escape."""

        try:
            normalized_path = canonical_source_path(source_path)
        except (OSError, RuntimeError, ValueError) as error:
            return MediaAnalysisFailure(
                str(source_path),
                MediaAnalysisErrorCode.SOURCE_NOT_FOUND,
                "파일 경로를 확인할 수 없습니다.",
                str(error),
            )

        path = Path(normalized_path)
        try:
            is_file = path.is_file()
        except OSError as error:
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.SOURCE_NOT_FOUND,
                "파일에 접근할 수 없습니다.",
                str(error),
            )
        if not is_file:
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.SOURCE_NOT_FOUND,
                "파일을 찾을 수 없습니다.",
            )

        kind = media_kind_for_path(path)
        if kind is None:
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.UNSUPPORTED_TYPE,
                "지원하지 않는 파일 형식입니다.",
            )

        arguments = (
            self._executable,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-i",
            normalized_path,
        )
        try:
            completed = self._runner(arguments, timeout=self._timeout_seconds)
        except FileNotFoundError as error:
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.FFPROBE_NOT_FOUND,
                "ffprobe 실행 파일을 찾을 수 없습니다.",
                str(error),
            )
        except subprocess.TimeoutExpired as error:
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.TIMEOUT,
                "미디어 분석 시간이 초과되었습니다.",
                str(error),
            )
        except OSError as error:
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.PROCESS_FAILED,
                "ffprobe를 실행하지 못했습니다.",
                str(error),
            )

        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()[:1000]
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.PROCESS_FAILED,
                "ffprobe가 파일을 분석하지 못했습니다.",
                detail or None,
            )

        try:
            raw_payload: object = json.loads(completed.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.INVALID_JSON,
                "ffprobe가 올바른 JSON을 반환하지 않았습니다.",
                str(error),
            )

        try:
            analysis = _parse_analysis(raw_payload, path=path, kind=kind)
        except _InvalidMedia as error:
            return MediaAnalysisFailure(
                normalized_path,
                MediaAnalysisErrorCode.INVALID_MEDIA,
                "필요한 미디어 정보를 읽을 수 없습니다.",
                str(error),
            )
        return MediaAnalysisSuccess(analysis)


def _parse_analysis(payload: object, *, path: Path, kind: MediaKind) -> MediaAnalysis:
    root = _mapping(payload, "ffprobe result")
    raw_streams = root.get("streams")
    if not isinstance(raw_streams, list):
        raise _InvalidMedia("ffprobe result has no streams array.")

    parsed_streams = tuple(
        parsed
        for item in raw_streams
        if (parsed := _parse_stream_if_supported(item)) is not None
    )
    if not parsed_streams:
        raise _InvalidMedia("No video or audio stream was found.")

    expected_kind = (
        MediaStreamKind.AUDIO if kind is MediaKind.AUDIO else MediaStreamKind.VIDEO
    )
    candidates = [
        stream for stream in parsed_streams if stream.metadata.kind is expected_kind
    ]
    if kind is MediaKind.VIDEO:
        candidates.sort(key=lambda stream: stream.attached_picture)
    if not candidates:
        raise _InvalidMedia(f"No {expected_kind.value} stream matches the file type.")
    primary = candidates[0]

    width: int | None = None
    height: int | None = None
    if kind in {MediaKind.VIDEO, MediaKind.PHOTO}:
        width, height = primary.width, primary.height
        if width is None or height is None:
            raise _InvalidMedia("The primary visual stream has no positive dimensions.")

    duration: ProjectTime | None = None
    if kind in {MediaKind.VIDEO, MediaKind.AUDIO}:
        duration = _stream_duration(primary)
        if duration is None:
            raw_format = root.get("format")
            if isinstance(raw_format, dict):
                format_data = cast(dict[str, object], raw_format)
                duration = _project_duration(format_data.get("duration"))
        if duration is None or duration.nanoseconds <= 0:
            raise _InvalidMedia("Timed media has no positive duration.")

    return MediaAnalysis(
        source_path=str(path),
        name=path.name,
        kind=kind,
        duration=duration,
        width=width,
        height=height,
        primary_stream_index=primary.metadata.index,
        streams=tuple(stream.metadata for stream in parsed_streams),
    )


def _parse_stream_if_supported(value: object) -> _ParsedStream | None:
    stream = _mapping(value, "stream")
    codec_type = stream.get("codec_type")
    if codec_type not in {"video", "audio"}:
        return None
    kind = MediaStreamKind(codec_type)

    index = _integer(stream.get("index"), "stream index")
    if index is None or index < 0:
        raise _InvalidMedia("Stream index must be a non-negative integer.")
    codec_name = stream.get("codec_name")
    if not isinstance(codec_name, str) or not codec_name.strip():
        raise _InvalidMedia(f"Stream {index} has no codec name.")
    time_base = _time_base(stream.get("time_base"), index=index)
    start_pts = _integer(stream.get("start_pts"), "start_pts")
    duration_ts = _integer(stream.get("duration_ts"), "duration_ts")
    if duration_ts is not None and duration_ts <= 0:
        duration_ts = None

    average_frame_rate: FrameRate | None = None
    sample_rate: int | None = None
    if kind is MediaStreamKind.VIDEO:
        average_frame_rate = _frame_rate(stream.get("avg_frame_rate"), index=index)
    else:
        sample_rate = _integer(stream.get("sample_rate"), "sample_rate")
        if sample_rate is not None and sample_rate <= 0:
            raise _InvalidMedia(f"Audio stream {index} has an invalid sample rate.")

    metadata = MediaStream(
        index=index,
        kind=kind,
        codec_name=codec_name,
        time_base=time_base,
        start_pts=start_pts,
        duration_ts=duration_ts,
        average_frame_rate=average_frame_rate,
        sample_rate=sample_rate,
    )
    width = _positive_integer(stream.get("width"))
    height = _positive_integer(stream.get("height"))
    duration_seconds = _decimal_seconds(stream.get("duration"))
    disposition = stream.get("disposition")
    attached_picture = False
    if isinstance(disposition, dict):
        disposition_data = cast(dict[str, object], disposition)
        attached_picture = disposition_data.get("attached_pic") in {1, "1"}
    return _ParsedStream(
        metadata,
        width,
        height,
        duration_seconds,
        attached_picture,
    )


def _stream_duration(stream: _ParsedStream) -> ProjectTime | None:
    duration_ts = stream.metadata.duration_ts
    if duration_ts is not None:
        time_base = stream.metadata.time_base
        seconds = Fraction(duration_ts * time_base.numerator, time_base.denominator)
        duration = ProjectTime.from_seconds(seconds)
        return duration if duration.nanoseconds > 0 else None
    if stream.duration_seconds is not None:
        return ProjectTime.from_seconds(stream.duration_seconds)
    return None


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _InvalidMedia(f"{label} must be an object.")
    return cast(dict[str, object], value)


def _integer(value: object, label: str) -> int | None:
    if value is None or value == "N/A":
        return None
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as error:
            raise _InvalidMedia(f"{label} must be an integer.") from error
    raise _InvalidMedia(f"{label} must be an integer.")


def _positive_integer(value: object) -> int | None:
    parsed = _integer(value, "dimension")
    return parsed if parsed is not None and parsed > 0 else None


def _ratio(value: object, *, label: str) -> tuple[int, int]:
    if not isinstance(value, str):
        raise _InvalidMedia(f"{label} must be a rational string.")
    parts = value.split("/", maxsplit=1)
    if len(parts) != 2:
        raise _InvalidMedia(f"{label} must contain a numerator and denominator.")
    try:
        numerator, denominator = (int(part) for part in parts)
    except ValueError as error:
        raise _InvalidMedia(f"{label} contains non-integer values.") from error
    return numerator, denominator


def _time_base(value: object, *, index: int) -> MediaTimeBase:
    numerator, denominator = _ratio(value, label=f"stream {index} time_base")
    if numerator <= 0 or denominator <= 0:
        raise _InvalidMedia(f"Stream {index} has an invalid time base.")
    return MediaTimeBase(numerator, denominator)


def _frame_rate(value: object, *, index: int) -> FrameRate | None:
    if value in {None, "N/A", "0/0"}:
        return None
    numerator, denominator = _ratio(value, label=f"stream {index} avg_frame_rate")
    if numerator <= 0 or denominator <= 0:
        raise _InvalidMedia(f"Stream {index} has an invalid average frame rate.")
    return FrameRate(numerator, denominator)


def _decimal_seconds(value: object) -> Decimal | None:
    if value is None or value == "N/A":
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise _InvalidMedia("Duration must be a decimal string or integer.")
    try:
        duration = Decimal(value)
    except InvalidOperation as error:
        raise _InvalidMedia("Duration is not a valid decimal.") from error
    if not duration.is_finite() or duration <= 0:
        return None
    return duration


def _project_duration(value: object) -> ProjectTime | None:
    seconds = _decimal_seconds(value)
    return ProjectTime.from_seconds(seconds) if seconds is not None else None
