"""Persistent project audio interpretation and cancellable FFmpeg mixing."""

from movie_maker.audio.coordinator import AudioDecodeCoordinator
from movie_maker.audio.decoder import (
    AudioDecodeCancelled,
    AudioDecodeErrorCode,
    AudioDecodeFailure,
    AudioDecodeResult,
    DecodedAudio,
    FfmpegAudioDecoder,
)
from movie_maker.audio.graph import (
    MAX_AUDIO_RATE,
    MIN_AUDIO_RATE,
    OUTPUT_CHANNELS,
    OUTPUT_FRAME_BYTES,
    OUTPUT_SAMPLE_RATE,
    AudioGraph,
    AudioGraphError,
    AudioGraphErrorCode,
    AudioSource,
    AudioSourceKind,
    audio_frames_for_time,
    build_audio_graph,
    ffmpeg_audio_arguments,
    ffmpeg_audio_filter,
    format_audio_time,
)

__all__ = [
    "MAX_AUDIO_RATE",
    "MIN_AUDIO_RATE",
    "OUTPUT_CHANNELS",
    "OUTPUT_FRAME_BYTES",
    "OUTPUT_SAMPLE_RATE",
    "AudioDecodeCancelled",
    "AudioDecodeCoordinator",
    "AudioDecodeErrorCode",
    "AudioDecodeFailure",
    "AudioDecodeResult",
    "AudioGraph",
    "AudioGraphError",
    "AudioGraphErrorCode",
    "AudioSource",
    "AudioSourceKind",
    "DecodedAudio",
    "FfmpegAudioDecoder",
    "audio_frames_for_time",
    "build_audio_graph",
    "ffmpeg_audio_arguments",
    "ffmpeg_audio_filter",
    "format_audio_time",
]
