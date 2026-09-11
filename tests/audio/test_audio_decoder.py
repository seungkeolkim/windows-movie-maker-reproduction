from __future__ import annotations

import subprocess
from hashlib import sha256
from pathlib import Path
from threading import Event

from movie_maker.audio import (
    AudioDecodeCancelled,
    AudioDecodeErrorCode,
    AudioDecodeFailure,
    AudioGraph,
    AudioSource,
    AudioSourceKind,
    DecodedAudio,
    FfmpegAudioDecoder,
    ffmpeg_audio_arguments,
)
from movie_maker.project import AudioLevel, PlaybackRate, ProjectTime


def _graph(path: Path) -> AudioGraph:
    return AudioGraph(
        "project",
        ProjectTime.zero(),
        ProjectTime.from_milliseconds(1),
        (
            AudioSource(
                "media",
                "clip",
                AudioSourceKind.MUSIC,
                str(path),
                3,
                ProjectTime.zero(),
                ProjectTime.from_milliseconds(1),
                ProjectTime.zero(),
                ProjectTime.from_milliseconds(1),
                PlaybackRate(),
                AudioLevel(50),
                False,
            ),
        ),
    )


class _Process:
    def __init__(
        self,
        stdout: bytes = b"\x01\x00\x01\x00",
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


def test_ffmpeg_decoder_uses_shell_free_graph_argv_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "한글 music source.wav"
    source.write_bytes(b"immutable audio source")
    before = (sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source.stat().st_mtime_ns)
    calls: list[tuple[str, ...]] = []

    def launch(arguments):
        calls.append(tuple(arguments))
        return _Process()

    graph = _graph(source)
    result = FfmpegAudioDecoder(executable="custom ffmpeg", launcher=launch).decode(
        graph, Event()
    )

    assert isinstance(result, DecodedAudio)
    assert len(result.pcm_bytes) == graph.output_frames * 4
    assert calls == [ffmpeg_audio_arguments("custom ffmpeg", graph)]
    assert calls[0][calls[0].index("-i") + 1] == str(source)
    after = (sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source.stat().st_mtime_ns)
    assert after == before


def test_missing_source_ffmpeg_filter_media_and_invalid_pcm_have_typed_failures(
    tmp_path: Path,
) -> None:
    missing = FfmpegAudioDecoder(launcher=lambda arguments: _Process()).decode(
        _graph(tmp_path / "missing.wav"), Event()
    )
    assert isinstance(missing, AudioDecodeFailure)
    assert missing.code is AudioDecodeErrorCode.SOURCE_NOT_FOUND

    source = tmp_path / "source.wav"
    source.write_bytes(b"source")

    def missing_ffmpeg(arguments):
        raise FileNotFoundError("ffmpeg")

    cases = (
        (
            FfmpegAudioDecoder(launcher=missing_ffmpeg).decode(_graph(source), Event()),
            AudioDecodeErrorCode.FFMPEG_NOT_FOUND,
        ),
        (
            FfmpegAudioDecoder(
                launcher=lambda arguments: _Process(
                    stderr=b"No such filter: alimiter", returncode=1
                )
            ).decode(_graph(source), Event()),
            AudioDecodeErrorCode.FILTER_UNAVAILABLE,
        ),
        (
            FfmpegAudioDecoder(
                launcher=lambda arguments: _Process(
                    stderr=b"unsupported decoder", returncode=1
                )
            ).decode(_graph(source), Event()),
            AudioDecodeErrorCode.UNSUPPORTED_MEDIA,
        ),
        (
            FfmpegAudioDecoder(
                launcher=lambda arguments: _Process(stdout=b"odd")
            ).decode(_graph(source), Event()),
            AudioDecodeErrorCode.INVALID_AUDIO,
        ),
    )

    for result, code in cases:
        assert isinstance(result, AudioDecodeFailure)
        assert result.code is code


def test_running_process_is_terminated_on_cancel_and_timeout(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    cancelled = Event()
    cancel_process = _Process(timeout_once=True, timeout_hook=cancelled.set)

    cancelled_result = FfmpegAudioDecoder(
        launcher=lambda arguments: cancel_process
    ).decode(_graph(source), cancelled)

    assert isinstance(cancelled_result, AudioDecodeCancelled)
    assert cancel_process.terminated

    timeout_process = _Process(timeout_once=True)
    moments = iter((0.0, 2.0))
    timeout_result = FfmpegAudioDecoder(
        timeout=1.0,
        launcher=lambda arguments: timeout_process,
        monotonic=lambda: next(moments),
    ).decode(_graph(source), Event())

    assert isinstance(timeout_result, AudioDecodeFailure)
    assert timeout_result.code is AudioDecodeErrorCode.TIMEOUT
    assert timeout_process.terminated
