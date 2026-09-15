from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from threading import Event

import pytest

from movie_maker.media import (
    CacheKey,
    CacheKind,
    FfprobeAnalyzer,
    MediaAnalysisSuccess,
    MediaArtifactGenerator,
    MediaCache,
)


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


@pytest.mark.parametrize(
    ("size", "frame_rate", "sample_rate"),
    (("160x90", 24, 44_100), ("320x180", 30, 32_000)),
)
def test_real_thumbnail_waveform_and_proxy_are_verified_and_leave_source_unchanged(
    tmp_path: Path, size: str, frame_rate: int, sample_rate: int
) -> None:
    source = tmp_path / f"한글 source {size} {frame_rate}fps {sample_rate}.mp4"
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=green:s={size}:r={frame_rate}:d=1",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate={sample_rate}:duration=1",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(source),
        ),
        check=True,
        shell=False,
    )
    analysis = FfprobeAnalyzer().analyze(source)
    assert isinstance(analysis, MediaAnalysisSuccess)
    reference = analysis.analysis.to_media_reference("media-1")
    before = _fingerprint(source)
    cache = MediaCache(tmp_path / "cache")
    generator = MediaArtifactGenerator(cache, timeout_seconds=30)

    thumbnail = generator.create_thumbnail(reference, Event())
    waveform = generator.create_waveform(reference, Event(), bucket_count=32)
    proxy = generator.create_proxy(reference, Event())

    assert thumbnail.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    waveform_payload = json.loads(waveform.read_text(encoding="utf-8"))
    assert waveform_payload["sample_rate"] == 8000
    assert 1 <= len(waveform_payload["buckets"]) <= 32
    proxy_analysis = FfprobeAnalyzer().analyze(proxy)
    assert isinstance(proxy_analysis, MediaAnalysisSuccess)
    assert (proxy_analysis.analysis.width, proxy_analysis.analysis.height) == (960, 540)
    assert _fingerprint(source) == before
    assert generator.create_proxy(reference, Event()) == proxy

    changed = tmp_path / f"changed-{sample_rate}.mp4"
    changed.write_bytes(source.read_bytes())
    old_key = CacheKey.create(CacheKind.THUMBNAIL, changed, {"width": 320})
    cache.publish_bytes(old_key, b"old thumbnail")
    changed.write_bytes(changed.read_bytes() + b"user changed source")
    new_key = CacheKey.create(CacheKind.THUMBNAIL, changed, {"width": 320})
    assert old_key.digest != new_key.digest
    assert cache.get(old_key) is None
