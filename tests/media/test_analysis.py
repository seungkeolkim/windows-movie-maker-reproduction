import json
import subprocess
from pathlib import Path

import pytest

from movie_maker.media import (
    FfprobeAnalyzer,
    MediaAnalysisErrorCode,
    MediaAnalysisFailure,
    MediaAnalysisSuccess,
)
from movie_maker.media.process import run_process
from movie_maker.project import (
    MediaKind,
    MediaStreamKind,
    MediaTimeBase,
    ProjectTime,
)


def _completed(payload: object) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=("ffprobe",),
        returncode=0,
        stdout=json.dumps(payload, ensure_ascii=False).encode(),
        stderr=b"",
    )


def _video_payload() -> dict[str, object]:
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "time_base": "1/30000",
                "start_pts": "-1001",
                "duration_ts": "300300",
                "avg_frame_rate": "30000/1001",
                "width": 1920,
                "height": 1080,
                "disposition": {"attached_pic": 0},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "time_base": "1/48000",
                "start_pts": 0,
                "duration_ts": "480480",
                "sample_rate": "48000",
            },
        ],
        "format": {"duration": "10.010000"},
    }


def test_video_analysis_preserves_exact_time_metadata_and_converts_reference(tmp_path) -> None:
    source = tmp_path / "여행 영상 01.mp4"
    source.write_bytes(b"original-video")
    before = (source.read_bytes(), source.stat().st_size, source.stat().st_mtime_ns)
    calls: list[tuple[tuple[str, ...], float]] = []

    def runner(arguments, *, timeout):
        calls.append((tuple(arguments), timeout))
        return _completed(_video_payload())

    result = FfprobeAnalyzer("custom ffprobe", runner=runner).analyze(source)

    assert isinstance(result, MediaAnalysisSuccess)
    analysis = result.analysis
    assert analysis.kind is MediaKind.VIDEO
    assert analysis.duration == ProjectTime(10_010_000_000)
    assert (analysis.width, analysis.height) == (1920, 1080)
    assert analysis.primary_stream_index == 0
    assert analysis.streams[0].kind is MediaStreamKind.VIDEO
    assert analysis.streams[0].time_base == MediaTimeBase(1, 30_000)
    assert analysis.streams[0].start_pts == -1001
    assert analysis.streams[0].duration_ts == 300_300
    assert analysis.streams[0].average_frame_rate is not None
    assert analysis.streams[0].average_frame_rate.numerator == 30_000
    assert analysis.streams[1].sample_rate == 48_000

    reference = analysis.to_media_reference("media-1")
    assert reference.source_path == str(source.resolve())
    assert reference.streams == analysis.streams
    assert reference.primary_stream_index == 0
    assert calls == [
        (
            (
                "custom ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "-i",
                str(source.resolve()),
            ),
            15.0,
        )
    ]
    assert (source.read_bytes(), source.stat().st_size, source.stat().st_mtime_ns) == before


def test_photo_analysis_has_dimensions_without_project_duration(tmp_path) -> None:
    source = tmp_path / "한라산 사진.PNG"
    source.write_bytes(b"original-photo")
    payload = {
        "streams": [
            {
                "index": 2,
                "codec_type": "video",
                "codec_name": "png",
                "time_base": "1/25",
                "start_pts": 0,
                "duration_ts": 1,
                "avg_frame_rate": "25/1",
                "width": 4032,
                "height": 3024,
            }
        ]
    }

    result = FfprobeAnalyzer(runner=lambda _arguments, timeout: _completed(payload)).analyze(
        source
    )

    assert isinstance(result, MediaAnalysisSuccess)
    assert result.analysis.kind is MediaKind.PHOTO
    assert result.analysis.duration is None
    assert (result.analysis.width, result.analysis.height) == (4032, 3024)
    assert result.analysis.primary_stream_index == 2


def test_audio_analysis_falls_back_to_exact_container_duration(tmp_path) -> None:
    source = tmp_path / "배경 음악.flac"
    source.write_bytes(b"original-audio")
    payload = {
        "streams": [
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "flac",
                "time_base": "1/44100",
                "start_pts": "0",
                "sample_rate": "44100",
            }
        ],
        "format": {"duration": "3.250000000"},
    }

    result = FfprobeAnalyzer(runner=lambda _arguments, timeout: _completed(payload)).analyze(
        source
    )

    assert isinstance(result, MediaAnalysisSuccess)
    assert result.analysis.kind is MediaKind.AUDIO
    assert result.analysis.duration == ProjectTime(3_250_000_000)
    assert result.analysis.width is None
    assert result.analysis.height is None
    assert result.analysis.streams[0].sample_rate == 44_100


def test_unsupported_and_missing_files_fail_without_running_ffprobe(tmp_path) -> None:
    calls = 0

    def runner(_arguments, *, timeout):
        nonlocal calls
        calls += 1
        return _completed({})

    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not media", encoding="utf-8")
    analyzer = FfprobeAnalyzer(runner=runner)

    unsupported_result = analyzer.analyze(unsupported)
    missing_result = analyzer.analyze(tmp_path / "missing.mp4")

    assert isinstance(unsupported_result, MediaAnalysisFailure)
    assert unsupported_result.code is MediaAnalysisErrorCode.UNSUPPORTED_TYPE
    assert isinstance(missing_result, MediaAnalysisFailure)
    assert missing_result.code is MediaAnalysisErrorCode.SOURCE_NOT_FOUND
    assert calls == 0


@pytest.mark.parametrize(
    ("runner", "expected_code"),
    [
        (
            lambda _arguments, timeout: (_ for _ in ()).throw(FileNotFoundError("missing")),
            MediaAnalysisErrorCode.FFPROBE_NOT_FOUND,
        ),
        (
            lambda arguments, timeout: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(arguments, timeout)
            ),
            MediaAnalysisErrorCode.TIMEOUT,
        ),
        (
            lambda _arguments, timeout: subprocess.CompletedProcess(
                ("ffprobe",), 1, stdout=b"", stderr=b"corrupt header"
            ),
            MediaAnalysisErrorCode.PROCESS_FAILED,
        ),
        (
            lambda _arguments, timeout: subprocess.CompletedProcess(
                ("ffprobe",), 0, stdout=b"{broken", stderr=b""
            ),
            MediaAnalysisErrorCode.INVALID_JSON,
        ),
        (
            lambda _arguments, timeout: _completed({"streams": []}),
            MediaAnalysisErrorCode.INVALID_MEDIA,
        ),
    ],
)
def test_expected_ffprobe_failures_are_typed_and_isolated(tmp_path, runner, expected_code) -> None:
    source = tmp_path / "broken video.mp4"
    source.write_bytes(b"unchanged")

    result = FfprobeAnalyzer(runner=runner).analyze(source)

    assert isinstance(result, MediaAnalysisFailure)
    assert result.code is expected_code
    assert source.read_bytes() == b"unchanged"


def test_unicode_spaces_and_long_path_are_one_argv_item(monkeypatch, tmp_path) -> None:
    long_tail = "/".join(["긴 경로 구간"] * 24)
    source_text = str(tmp_path / long_tail / "가족 여행 최종 영상.mp4")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(Path, "is_file", lambda _path: True)

    def runner(arguments, *, timeout):
        calls.append(tuple(arguments))
        return _completed(_video_payload())

    result = FfprobeAnalyzer(runner=runner).analyze(source_text)

    assert isinstance(result, MediaAnalysisSuccess)
    assert calls[0][-2] == "-i"
    assert calls[0][-1] == str(Path(source_text).resolve(strict=False))
    assert len(calls[0]) == 9


def test_default_process_runner_explicitly_disables_shell(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return subprocess.CompletedProcess(arguments, 0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_process(("ffprobe", "-i", "C:/유니코드 경로/file.mp4"), timeout=2.5)

    assert captured["arguments"] == (
        "ffprobe",
        "-i",
        "C:/유니코드 경로/file.mp4",
    )
    assert captured["shell"] is False
    assert captured["capture_output"] is True
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["timeout"] == 2.5
