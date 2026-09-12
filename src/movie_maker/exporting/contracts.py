"""Typed states shared by MP4 export workers and UI adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from movie_maker.exporting.plan import ExportPlan
from movie_maker.project import ProjectTime


class ExportErrorCode(str, Enum):
    """Stable export failure categories with distinct recovery actions."""

    OUTPUT_PATH_INVALID = "output_path_invalid"
    OUTPUT_NOT_WRITABLE = "output_not_writable"
    TARGET_EXISTS = "target_exists"
    DISK_FULL = "disk_full"
    SOURCE_NOT_FOUND = "source_not_found"
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"
    FFPROBE_NOT_FOUND = "ffprobe_not_found"
    ENCODER_UNAVAILABLE = "encoder_unavailable"
    FILTER_UNAVAILABLE = "filter_unavailable"
    UNSUPPORTED_MEDIA = "unsupported_media"
    IO_ERROR = "io_error"
    PROCESS_FAILED = "process_failed"
    TIMEOUT = "timeout"
    VALIDATION_FAILED = "validation_failed"
    INTERNAL_ERROR = "internal_error"


class ExportProgressStage(str, Enum):
    ENCODING = "encoding"
    VERIFYING = "verifying"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class ExportProgress:
    """A monotonic progress snapshot for one immutable plan."""

    plan: ExportPlan
    percent: int
    elapsed_seconds: float
    estimated_remaining_seconds: float | None
    stage: ExportProgressStage

    def __post_init__(self) -> None:
        if type(self.percent) is not int or not 0 <= self.percent <= 100:
            raise ValueError("Export progress percent must be between 0 and 100.")
        if self.elapsed_seconds < 0:
            raise ValueError("Export elapsed time cannot be negative.")
        if self.estimated_remaining_seconds is not None and self.estimated_remaining_seconds < 0:
            raise ValueError("Export estimated remaining time cannot be negative.")
        if self.percent == 100 and self.stage is not ExportProgressStage.COMPLETE:
            raise ValueError("Only a completed, verified export may report 100 percent.")


@dataclass(frozen=True, slots=True)
class ExportVerification:
    """Authoritative metadata observed from the completed temporary file."""

    width: int
    height: int
    duration: ProjectTime
    size_bytes: int
    frame_count: int | None = None
    video_codec: str = "h264"
    audio_codec: str = "aac"


@dataclass(frozen=True, slots=True)
class ExportSucceeded:
    plan: ExportPlan
    output_path: str
    verification: ExportVerification
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ExportFailed:
    plan: ExportPlan
    code: ExportErrorCode
    message: str
    detail: str | None = None
    return_code: int | None = None


@dataclass(frozen=True, slots=True)
class ExportCancelled:
    plan: ExportPlan


type ExportResult = ExportSucceeded | ExportFailed | ExportCancelled
