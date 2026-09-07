import subprocess

import pytest

from movie_maker.media import (
    FfmpegThumbnailer,
    MediaAnalysis,
    ThumbnailErrorCode,
    ThumbnailFailure,
    ThumbnailSuccess,
)
from movie_maker.project import (
    FrameRate,
    MediaKind,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    ProjectTime,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nthumbnail-data"


def _analysis(source_path: str, kind: MediaKind = MediaKind.VIDEO) -> MediaAnalysis:
    stream_kind = (
        MediaStreamKind.AUDIO if kind is MediaKind.AUDIO else MediaStreamKind.VIDEO
    )
    stream = MediaStream(
        index=2,
        kind=stream_kind,
        codec_name="aac" if kind is MediaKind.AUDIO else "h264",
        time_base=MediaTimeBase(1, 1_000),
        duration_ts=20_000,
        average_frame_rate=FrameRate(30, 1) if stream_kind is MediaStreamKind.VIDEO else None,
        sample_rate=48_000 if stream_kind is MediaStreamKind.AUDIO else None,
    )
    return MediaAnalysis(
        source_path=source_path,
        name="source.mp4",
        kind=kind,
        duration=None if kind is MediaKind.PHOTO else ProjectTime.from_seconds(20),
        width=1920 if kind is not MediaKind.AUDIO else None,
        height=1080 if kind is not MediaKind.AUDIO else None,
        primary_stream_index=2,
        streams=(stream,),
    )


def test_video_thumbnail_is_png_from_stdout_without_source_changes(tmp_path) -> None:
    source = tmp_path / "유니코드 영상 원본.mp4"
    source.write_bytes(b"original")
    before = (source.read_bytes(), source.stat().st_size, source.stat().st_mtime_ns)
    calls: list[tuple[tuple[str, ...], float]] = []

    def runner(arguments, *, timeout):
        calls.append((tuple(arguments), timeout))
        return subprocess.CompletedProcess(arguments, 0, stdout=PNG_BYTES, stderr=b"")

    result = FfmpegThumbnailer(
        "custom ffmpeg",
        width=150,
        height=70,
        runner=runner,
    ).create(_analysis(str(source)))

    assert isinstance(result, ThumbnailSuccess)
    assert result.png_bytes == PNG_BYTES
    arguments, timeout = calls[0]
    assert arguments[0] == "custom ffmpeg"
    assert arguments[arguments.index("-ss") + 1] == "1.000000000"
    assert arguments[arguments.index("-i") + 1] == str(source)
    assert arguments[arguments.index("-map") + 1] == "0:2"
    assert "scale=150:70:force_original_aspect_ratio=decrease" in arguments
    assert arguments[-1] == "pipe:1"
    assert timeout == 15.0
    assert (source.read_bytes(), source.stat().st_size, source.stat().st_mtime_ns) == before


def test_photo_thumbnail_uses_first_frame_without_seek() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments, *, timeout):
        calls.append(tuple(arguments))
        return subprocess.CompletedProcess(arguments, 0, stdout=PNG_BYTES, stderr=b"")

    result = FfmpegThumbnailer(runner=runner).create(
        _analysis("C:/사진 폴더/제주 사진.png", MediaKind.PHOTO)
    )

    assert isinstance(result, ThumbnailSuccess)
    assert "-ss" not in calls[0]
    assert calls[0][calls[0].index("-i") + 1] == "C:/사진 폴더/제주 사진.png"


def test_audio_thumbnail_is_skipped_without_starting_ffmpeg() -> None:
    calls = 0

    def runner(arguments, *, timeout):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(arguments, 0, stdout=PNG_BYTES, stderr=b"")

    result = FfmpegThumbnailer(runner=runner).create(
        _analysis("C:/Music/song.wav", MediaKind.AUDIO)
    )

    assert isinstance(result, ThumbnailFailure)
    assert result.code is ThumbnailErrorCode.NOT_VISUAL
    assert calls == 0


@pytest.mark.parametrize(
    ("runner", "expected_code"),
    [
        (
            lambda _arguments, timeout: (_ for _ in ()).throw(FileNotFoundError("missing")),
            ThumbnailErrorCode.FFMPEG_NOT_FOUND,
        ),
        (
            lambda arguments, timeout: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(arguments, timeout)
            ),
            ThumbnailErrorCode.TIMEOUT,
        ),
        (
            lambda arguments, timeout: subprocess.CompletedProcess(
                arguments, 1, stdout=b"", stderr=b"decoder failure"
            ),
            ThumbnailErrorCode.PROCESS_FAILED,
        ),
        (
            lambda arguments, timeout: subprocess.CompletedProcess(
                arguments, 0, stdout=b"not a png", stderr=b""
            ),
            ThumbnailErrorCode.INVALID_OUTPUT,
        ),
    ],
)
def test_thumbnail_failures_are_typed_and_do_not_escape(runner, expected_code) -> None:
    result = FfmpegThumbnailer(runner=runner).create(_analysis("C:/Media/source.mp4"))

    assert isinstance(result, ThumbnailFailure)
    assert result.code is expected_code


def test_thumbnail_configuration_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="timeout"):
        FfmpegThumbnailer(timeout_seconds=0)
    with pytest.raises(ValueError, match="dimensions"):
        FfmpegThumbnailer(width=0)
