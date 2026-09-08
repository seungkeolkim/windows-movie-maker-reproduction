from __future__ import annotations

import subprocess
from hashlib import sha256
from pathlib import Path
from threading import Event

from movie_maker.preview import (
    DecodedFrame,
    FfmpegFrameDecoder,
    FrameTarget,
    PreviewDecodeCancelled,
    PreviewDecodeErrorCode,
    PreviewDecodeFailure,
    ffmpeg_frame_arguments,
)
from movie_maker.project import MediaKind, ProjectTime

PNG_BYTES = b"\x89PNG\r\n\x1a\nactual-frame"


def _target(path: Path) -> FrameTarget:
    return FrameTarget(
        project_id="project",
        clip_id="clip",
        clip_label="clip",
        asset_id="media",
        source_path=str(path),
        media_kind=MediaKind.VIDEO,
        stream_index=3,
        source_time=ProjectTime(12_345_678_900),
        source_pts=1_120_111,
        time_base_numerator=1,
        time_base_denominator=90_000,
        start_pts=9_000,
    )


class _Process:
    def __init__(
        self,
        stdout: bytes = PNG_BYTES,
        stderr: bytes = b"",
        returncode: int = 0,
        *,
        timeout_once: bool = False,
        timeout_hook=None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timeout_once = timeout_once
        self.timeout_hook = timeout_hook
        self.terminated = False
        self.killed = False

    def communicate(self, input=None, timeout=None):
        if self.timeout_once:
            self.timeout_once = False
            if self.timeout_hook is not None:
                self.timeout_hook()
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_ffmpeg_decode_uses_one_argv_stream_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "한글 source video.mp4"
    source.write_bytes(b"read only source")
    before = (sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source.stat().st_mtime_ns)
    calls: list[tuple[str, ...]] = []

    def launch(arguments):
        calls.append(tuple(arguments))
        return _Process()

    result = FfmpegFrameDecoder(executable="custom ffmpeg", launcher=launch).decode(
        _target(source), Event()
    )

    assert isinstance(result, DecodedFrame)
    assert result.png_bytes == PNG_BYTES
    arguments = calls[0]
    assert arguments == ffmpeg_frame_arguments("custom ffmpeg", _target(source))
    assert arguments[arguments.index("-ss") + 1] == "12.3456789"
    assert arguments[arguments.index("-i") + 1] == str(source)
    assert arguments[arguments.index("-map") + 1] == "0:3"
    after = (sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source.stat().st_mtime_ns)
    assert after == before


def test_missing_failed_and_invalid_sources_return_typed_failures(tmp_path: Path) -> None:
    missing = FfmpegFrameDecoder(launcher=lambda arguments: _Process()).decode(
        _target(tmp_path / "missing.mp4"), Event()
    )
    assert isinstance(missing, PreviewDecodeFailure)
    assert missing.code is PreviewDecodeErrorCode.SOURCE_NOT_FOUND

    source = tmp_path / "bad.mp4"
    source.write_bytes(b"bad")
    failed = FfmpegFrameDecoder(
        launcher=lambda arguments: _Process(stderr=b"unsupported codec", returncode=1)
    ).decode(_target(source), Event())
    invalid = FfmpegFrameDecoder(
        launcher=lambda arguments: _Process(stdout=b"not png")
    ).decode(_target(source), Event())

    assert isinstance(failed, PreviewDecodeFailure)
    assert failed.code is PreviewDecodeErrorCode.PROCESS_FAILED
    assert failed.detail == "unsupported codec"
    assert isinstance(invalid, PreviewDecodeFailure)
    assert invalid.code is PreviewDecodeErrorCode.INVALID_FRAME


def test_missing_ffmpeg_and_launcher_error_are_typed(tmp_path: Path) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"source")

    def missing_launcher(arguments):
        raise FileNotFoundError("ffmpeg")

    def broken_launcher(arguments):
        raise OSError("cannot start")

    missing = FfmpegFrameDecoder(launcher=missing_launcher).decode(_target(source), Event())
    broken = FfmpegFrameDecoder(launcher=broken_launcher).decode(_target(source), Event())

    assert isinstance(missing, PreviewDecodeFailure)
    assert missing.code is PreviewDecodeErrorCode.FFMPEG_NOT_FOUND
    assert isinstance(broken, PreviewDecodeFailure)
    assert broken.code is PreviewDecodeErrorCode.INTERNAL_ERROR


def test_running_process_is_terminated_when_request_is_cancelled(tmp_path: Path) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"source")
    cancelled = Event()
    process = _Process(timeout_once=True, timeout_hook=cancelled.set)
    decoder = FfmpegFrameDecoder(launcher=lambda arguments: process)

    result = decoder.decode(_target(source), cancelled)

    assert isinstance(result, PreviewDecodeCancelled)
    assert process.terminated


def test_timeout_terminates_process_and_reports_timeout(tmp_path: Path) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"source")
    process = _Process(timeout_once=True)
    moments = iter((0.0, 2.0))
    decoder = FfmpegFrameDecoder(
        timeout=1.0,
        launcher=lambda arguments: process,
        monotonic=lambda: next(moments),
    )

    result = decoder.decode(_target(source), Event())

    assert isinstance(result, PreviewDecodeFailure)
    assert result.code is PreviewDecodeErrorCode.TIMEOUT
    assert process.terminated
