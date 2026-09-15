import struct
from pathlib import Path
from threading import Event

import pytest

from movie_maker.media import (
    ArtifactGenerationError,
    MediaArtifactGenerator,
    MediaCache,
    summarize_waveform,
)
from movie_maker.media.process import ProcessCancelled
from movie_maker.project import (
    FrameRate,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    ProjectTime,
)


def test_waveform_summary_is_deterministic_min_max() -> None:
    payload = b"".join(struct.pack("<f", value) for value in (-0.5, 0.25, 0.75, -1.0))
    first = summarize_waveform(payload, sample_rate=8000, bucket_count=2)
    second = summarize_waveform(payload, sample_rate=8000, bucket_count=2)

    assert first == second
    assert [(bucket.minimum, bucket.maximum) for bucket in first.buckets] == [
        (-0.5, 0.25),
        (-1.0, 0.75),
    ]
    assert first.to_bytes() == second.to_bytes()


def test_waveform_rejects_partial_or_non_finite_samples() -> None:
    with pytest.raises(ArtifactGenerationError):
        summarize_waveform(b"bad", sample_rate=8000, bucket_count=2)
    with pytest.raises(ArtifactGenerationError):
        summarize_waveform(struct.pack("<f", float("nan")), sample_rate=8000, bucket_count=2)


class _Process:
    returncode = -15

    def __init__(self) -> None:
        self.terminated = False

    def communicate(self, input=None, timeout=None):
        return b"", b""

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        raise AssertionError("a responsive process must not be killed")


def test_thumbnail_process_is_terminated_when_job_is_cancelled(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    stream = MediaStream(
        0,
        MediaStreamKind.VIDEO,
        "h264",
        MediaTimeBase(1, 1000),
        duration_ts=1000,
        average_frame_rate=FrameRate(30),
    )
    media = MediaReference(
        "media-1",
        source.name,
        str(source),
        MediaKind.VIDEO,
        ProjectTime.from_seconds(1),
        1920,
        1080,
        0,
        (stream,),
    )
    process = _Process()
    cancel = Event()
    cancel.set()
    generator = MediaArtifactGenerator(
        MediaCache(tmp_path / "cache"),
        ffmpeg="ffmpeg",
        launcher=lambda _arguments: process,
    )

    with pytest.raises(ProcessCancelled):
        generator.create_thumbnail(media, cancel)

    assert process.terminated
