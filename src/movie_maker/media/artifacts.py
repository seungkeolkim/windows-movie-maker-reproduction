"""Real FFmpeg thumbnail, waveform, and edit-proxy cache producers."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from movie_maker.media.analysis import FfprobeAnalyzer, MediaAnalysisFailure, MediaAnalysisSuccess
from movie_maker.media.cache import CacheKey, CacheKind, MediaCache
from movie_maker.media.process import (
    ProcessLauncher,
    launch_process,
    resolve_media_tool,
    run_cancellable_process,
)
from movie_maker.media.thumbnail import PNG_SIGNATURE
from movie_maker.project import MediaKind, MediaReference, MediaStreamKind, ProjectTime


class ArtifactGenerationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WaveformBucket:
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class WaveformSummary:
    sample_rate: int
    samples_per_bucket: int
    buckets: tuple[WaveformBucket, ...]

    def to_bytes(self) -> bytes:
        return (
            json.dumps(
                {
                    "format_version": 1,
                    "sample_rate": self.sample_rate,
                    "samples_per_bucket": self.samples_per_bucket,
                    "buckets": [
                        [round(bucket.minimum, 7), round(bucket.maximum, 7)]
                        for bucket in self.buckets
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ProxySettings:
    width: int = 960
    height: int = 540
    frame_rate: int = 30
    video_codec: str = "libx264"
    audio_codec: str = "aac"

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.frame_rate <= 0:
            raise ValueError("Proxy dimensions and frame rate must be positive.")

    def key_values(self) -> dict[str, str | int]:
        return {
            "width": self.width,
            "height": self.height,
            "frame_rate": self.frame_rate,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
        }


DEFAULT_PROXY_SETTINGS = ProxySettings()


class MediaArtifactGenerator:
    """Create verified cache artifacts without changing the source or project."""

    def __init__(
        self,
        cache: MediaCache,
        *,
        ffmpeg: str | None = None,
        analyzer: FfprobeAnalyzer | None = None,
        launcher: ProcessLauncher = launch_process,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.cache = cache
        self.ffmpeg = ffmpeg or resolve_media_tool("ffmpeg")
        self.analyzer = analyzer or FfprobeAnalyzer()
        self.launcher = launcher
        self.timeout_seconds = timeout_seconds

    def thumbnail_key(
        self, media: MediaReference, *, source_time: ProjectTime | None = None
    ) -> CacheKey:
        settings: dict[str, str | int | float | bool] = {
            "width": 320, "height": 180, "format": "png",
        }
        if source_time is not None:
            if source_time.nanoseconds < 0 or (
                media.duration is not None and source_time >= media.duration
            ):
                raise ValueError("Thumbnail time must be inside the source duration.")
            settings.update(
                width=160, height=90, source_ns=source_time.nanoseconds,
                stream_index=media.primary_stream_index or 0,
            )
        return CacheKey.create(
            CacheKind.THUMBNAIL,
            media.source_path,
            settings,
        )

    def waveform_key(self, media: MediaReference, *, bucket_count: int = 1200) -> CacheKey:
        return CacheKey.create(
            CacheKind.WAVEFORM,
            media.source_path,
            {"sample_rate": 8000, "bucket_count": bucket_count, "format": "minmax-v1"},
        )

    def proxy_key(
        self,
        media: MediaReference,
        settings: ProxySettings = DEFAULT_PROXY_SETTINGS,
    ) -> CacheKey:
        return CacheKey.create(CacheKind.PROXY, media.source_path, settings.key_values())

    def create_thumbnail(
        self, media: MediaReference, cancel: Event, *, source_time: ProjectTime | None = None
    ) -> Path:
        key = self.thumbnail_key(media, source_time=source_time)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        if media.kind not in {MediaKind.VIDEO, MediaKind.PHOTO}:
            raise ArtifactGenerationError("오디오 미디어에는 썸네일이 필요하지 않습니다.")
        if media.primary_stream_index is None:
            raise ArtifactGenerationError("분석되지 않은 미디어의 썸네일을 만들 수 없습니다.")
        arguments = [self.ffmpeg, "-v", "error", "-nostdin"]
        if media.kind is MediaKind.VIDEO and media.duration is not None:
            seek_nanoseconds = (
                source_time.nanoseconds if source_time is not None
                else min(1_000_000_000, max(0, media.duration.nanoseconds // 10))
            )
            seconds, remainder = divmod(seek_nanoseconds, 1_000_000_000)
            arguments.extend(("-ss", f"{seconds}.{remainder:09d}"))
        arguments.extend(
            (
                "-i",
                media.source_path,
                "-map",
                f"0:{media.primary_stream_index}",
                "-frames:v",
                "1",
                "-vf",
                (
                    "scale=320:180:force_original_aspect_ratio=decrease"
                    if source_time is None else "scale=160:90:force_original_aspect_ratio=decrease"
                ),
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            )
        )
        completed = run_cancellable_process(
            arguments,
            cancel,
            timeout=min(self.timeout_seconds, 15.0),
            launcher=self.launcher,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()[:1000]
            raise ArtifactGenerationError(detail or "썸네일 생성에 실패했습니다.")
        if not completed.stdout.startswith(PNG_SIGNATURE):
            raise ArtifactGenerationError("올바른 PNG 썸네일이 생성되지 않았습니다.")
        return self.cache.publish_bytes(key, completed.stdout)

    def create_waveform(
        self,
        media: MediaReference,
        cancel: Event,
        *,
        bucket_count: int = 1200,
    ) -> Path:
        if bucket_count <= 0:
            raise ValueError("Waveform bucket count must be positive.")
        audio_stream = next(
            (stream for stream in media.streams if stream.kind is MediaStreamKind.AUDIO),
            None,
        )
        if audio_stream is None:
            raise ArtifactGenerationError("파형을 만들 오디오 스트림이 없습니다.")
        key = self.waveform_key(media, bucket_count=bucket_count)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        arguments = (
            self.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            media.source_path,
            "-map",
            f"0:{audio_stream.index}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "f32le",
            "pipe:1",
        )
        completed = run_cancellable_process(
            arguments,
            cancel,
            timeout=self.timeout_seconds,
            launcher=self.launcher,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()[:1000]
            raise ArtifactGenerationError(detail or "파형 디코딩에 실패했습니다.")
        summary = summarize_waveform(completed.stdout, sample_rate=8000, bucket_count=bucket_count)
        return self.cache.publish_bytes(key, summary.to_bytes())

    def create_proxy(
        self,
        media: MediaReference,
        cancel: Event,
        settings: ProxySettings = DEFAULT_PROXY_SETTINGS,
    ) -> Path:
        if media.kind is not MediaKind.VIDEO:
            raise ArtifactGenerationError("영상 원본만 프록시를 만들 수 있습니다.")
        video_stream = next(
            (stream for stream in media.streams if stream.kind is MediaStreamKind.VIDEO),
            None,
        )
        if video_stream is None:
            raise ArtifactGenerationError("프록시를 만들 영상 스트림이 없습니다.")
        audio_stream = next(
            (stream for stream in media.streams if stream.kind is MediaStreamKind.AUDIO),
            None,
        )
        key = self.proxy_key(media, settings)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        temporary = self.cache.temporary_path(key)
        arguments = [
            self.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-i",
            media.source_path,
            "-map",
            f"0:{video_stream.index}",
        ]
        if audio_stream is not None:
            arguments.extend(("-map", f"0:{audio_stream.index}"))
        arguments.extend(
            (
                "-vf",
                (
                    f"scale={settings.width}:{settings.height}:"
                    "force_original_aspect_ratio=decrease,"
                    f"pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2,"
                    f"fps={settings.frame_rate}"
                ),
                "-c:v",
                settings.video_codec,
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
            )
        )
        if audio_stream is not None:
            arguments.extend(("-c:a", settings.audio_codec, "-ar", "48000", "-ac", "2"))
        arguments.extend(("-movflags", "+faststart", str(temporary)))
        try:
            completed = run_cancellable_process(
                arguments,
                cancel,
                timeout=self.timeout_seconds,
                launcher=self.launcher,
            )
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace").strip()[:1000]
                raise ArtifactGenerationError(detail or "프록시 생성에 실패했습니다.")
            self._verify_proxy(media, temporary, settings, cancel)
            return self.cache.publish_temporary(key, temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def _verify_proxy(
        self,
        source: MediaReference,
        proxy_path: Path,
        settings: ProxySettings,
        cancel: Event,
    ) -> None:
        analyzer = self.analyzer
        if isinstance(analyzer, FfprobeAnalyzer):
            analyzer = analyzer.with_runner(
                lambda arguments, *, timeout: run_cancellable_process(
                    arguments,
                    cancel,
                    timeout=timeout,
                    launcher=self.launcher,
                )
            )
        result = analyzer.analyze(proxy_path)
        if isinstance(result, MediaAnalysisFailure):
            raise ArtifactGenerationError(result.message)
        if not isinstance(result, MediaAnalysisSuccess):
            raise ArtifactGenerationError("프록시 검증 결과가 올바르지 않습니다.")
        proxy = result.analysis
        if proxy.kind is not MediaKind.VIDEO or proxy.width != settings.width or proxy.height != settings.height:
            raise ArtifactGenerationError("프록시 화면 크기 또는 형식이 올바르지 않습니다.")
        if source.duration is not None and proxy.duration is not None:
            tolerance = max(100_000_000, source.duration.nanoseconds // 100)
            if abs(source.duration.nanoseconds - proxy.duration.nanoseconds) > tolerance:
                raise ArtifactGenerationError("프록시 시간축이 원본과 일치하지 않습니다.")


def summarize_waveform(payload: bytes, *, sample_rate: int, bucket_count: int) -> WaveformSummary:
    if sample_rate <= 0 or bucket_count <= 0:
        raise ValueError("Waveform summary settings must be positive.")
    if not payload or len(payload) % 4:
        raise ArtifactGenerationError("파형 샘플 출력이 올바르지 않습니다.")
    samples = tuple(value[0] for value in struct.iter_unpack("<f", payload))
    if any(not math.isfinite(value) for value in samples):
        raise ArtifactGenerationError("파형 샘플에 유효하지 않은 값이 있습니다.")
    samples_per_bucket = max(1, math.ceil(len(samples) / bucket_count))
    buckets = tuple(
        WaveformBucket(max(-1.0, min(chunk)), min(1.0, max(chunk)))
        for start in range(0, len(samples), samples_per_bucket)
        if (chunk := samples[start : start + samples_per_bucket])
    )
    return WaveformSummary(sample_rate, samples_per_bucket, buckets)
