"""Exact timeline playback and cancellable visual-frame decoding."""

from movie_maker.preview.coordinator import PreviewDecodeCoordinator
from movie_maker.preview.decoder import (
    DecodedFrame,
    FfmpegFrameDecoder,
    PreviewDecodeCancelled,
    PreviewDecodeErrorCode,
    PreviewDecodeFailure,
    PreviewDecodeResult,
    ffmpeg_frame_arguments,
    format_ffmpeg_timestamp,
)
from movie_maker.preview.timeline import (
    FrameTarget,
    PlaybackClock,
    PreviewPosition,
    frame_at_project_time,
    step_project_frame,
    ui_milliseconds,
)

__all__ = [
    "DecodedFrame",
    "FfmpegFrameDecoder",
    "FrameTarget",
    "PlaybackClock",
    "PreviewDecodeCancelled",
    "PreviewDecodeCoordinator",
    "PreviewDecodeErrorCode",
    "PreviewDecodeFailure",
    "PreviewDecodeResult",
    "PreviewPosition",
    "ffmpeg_frame_arguments",
    "format_ffmpeg_timestamp",
    "frame_at_project_time",
    "step_project_frame",
    "ui_milliseconds",
]
