"""Cancellable FFmpeg PCM rendering for a reusable project audio graph."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Protocol

from movie_maker.audio.graph import OUTPUT_FRAME_BYTES, AudioGraph, ffmpeg_audio_arguments
from movie_maker.media.process import resolve_media_tool


class AudioDecodeErrorCode(str, Enum):
    SOURCE_NOT_FOUND = "source_not_found"
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"
    FILTER_UNAVAILABLE = "filter_unavailable"
    UNSUPPORTED_MEDIA = "unsupported_media"
    TIMEOUT = "timeout"
    PROCESS_FAILED = "process_failed"
    INVALID_AUDIO = "invalid_audio"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class DecodedAudio:
    graph: AudioGraph
    pcm_bytes: bytes


@dataclass(frozen=True, slots=True)
class AudioDecodeFailure:
    graph: AudioGraph
    code: AudioDecodeErrorCode
    message: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AudioDecodeCancelled:
    graph: AudioGraph


type AudioDecodeResult = DecodedAudio | AudioDecodeFailure | AudioDecodeCancelled


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


def _classify_process_failure(stderr: bytes) -> AudioDecodeErrorCode:
    message = stderr.decode("utf-8", errors="replace").casefold()
    if "no such filter" in message or "filter not found" in message:
        return AudioDecodeErrorCode.FILTER_UNAVAILABLE
    if "decoder" in message or "invalid data" in message or "unsupported" in message:
        return AudioDecodeErrorCode.UNSUPPORTED_MEDIA
    return AudioDecodeErrorCode.PROCESS_FAILED


class FfmpegAudioDecoder:
    """Render one bounded PCM range while allowing prompt process cancellation."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout: float = 20.0,
        launcher: ProcessLauncher = _launch_process,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._executable = executable or resolve_media_tool("ffmpeg")
        self._timeout = timeout
        self._launcher = launcher
        self._monotonic = monotonic

    def decode(self, graph: AudioGraph, cancelled: Event) -> AudioDecodeResult:
        missing = next(
            (source.source_path for source in graph.sources if not Path(source.source_path).is_file()),
            None,
        )
        if missing is not None:
            return AudioDecodeFailure(
                graph,
                AudioDecodeErrorCode.SOURCE_NOT_FOUND,
                "오디오 원본 파일을 찾을 수 없습니다. 누락 미디어에서 다시 연결하세요.",
                missing,
            )
        if cancelled.is_set():
            return AudioDecodeCancelled(graph)

        arguments = ffmpeg_audio_arguments(self._executable, graph)
        try:
            process = self._launcher(arguments)
        except FileNotFoundError as error:
            return AudioDecodeFailure(
                graph,
                AudioDecodeErrorCode.FFMPEG_NOT_FOUND,
                "FFmpeg를 찾을 수 없습니다. 실행 환경을 점검하세요.",
                str(error),
            )
        except OSError as error:
            return AudioDecodeFailure(
                graph,
                AudioDecodeErrorCode.INTERNAL_ERROR,
                "오디오 디코더를 시작하지 못했습니다. 다시 시도하세요.",
                str(error),
            )

        deadline = self._monotonic() + self._timeout
        while True:
            if cancelled.is_set():
                _stop_process(process)
                return AudioDecodeCancelled(graph)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                _, stderr = _stop_process(process)
                return AudioDecodeFailure(
                    graph,
                    AudioDecodeErrorCode.TIMEOUT,
                    "오디오를 읽는 시간이 초과됐습니다. 다시 시도하세요.",
                    _detail(stderr),
                )
            try:
                stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
            except OSError as error:
                _stop_process(process)
                return AudioDecodeFailure(
                    graph,
                    AudioDecodeErrorCode.INTERNAL_ERROR,
                    "오디오를 읽는 중 오류가 발생했습니다. 다시 시도하세요.",
                    str(error),
                )

        if process.returncode != 0:
            code = _classify_process_failure(stderr)
            return AudioDecodeFailure(
                graph,
                code,
                "오디오를 디코딩할 수 없습니다. 파일과 FFmpeg 지원을 확인하세요.",
                _detail(stderr),
            )
        if not stdout or len(stdout) % OUTPUT_FRAME_BYTES:
            return AudioDecodeFailure(
                graph,
                AudioDecodeErrorCode.INVALID_AUDIO,
                "재생 가능한 오디오 샘플을 만들지 못했습니다.",
                _detail(stderr),
            )
        expected_bytes = graph.output_frames * OUTPUT_FRAME_BYTES
        normalized = stdout[:expected_bytes]
        if len(normalized) < expected_bytes:
            normalized += bytes(expected_bytes - len(normalized))
        return DecodedAudio(graph, normalized)
