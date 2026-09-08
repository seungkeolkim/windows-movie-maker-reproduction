from __future__ import annotations

import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event

from PySide6.QtGui import QImage

from movie_maker.media import FfprobeAnalyzer, MediaAnalysisSuccess
from movie_maker.preview import DecodedFrame, FfmpegFrameDecoder, frame_at_project_time
from movie_maker.project import CommandExecutor, Project, ProjectTime
from movie_maker.timeline import AddMediaClip


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


def _generate_sources(tmp_path: Path) -> tuple[Path, Path]:
    video = tmp_path / "fractional video.mp4"
    photo = tmp_path / "still photo.png"
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x48:r=24000/1001:d=0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ),
        check=True,
        shell=False,
    )
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48",
            "-frames:v",
            "1",
            "-y",
            str(photo),
        ),
        check=True,
        shell=False,
    )
    return video, photo


def test_actual_video_and_photo_frames_decode_without_modifying_sources(tmp_path: Path) -> None:
    video_path, photo_path = _generate_sources(tmp_path)
    before = {path: _fingerprint(path) for path in (video_path, photo_path)}
    analyzer = FfprobeAnalyzer()
    analyses = [analyzer.analyze(path) for path in (video_path, photo_path)]
    assert all(isinstance(result, MediaAnalysisSuccess) for result in analyses)
    references = tuple(
        result.analysis.to_media_reference(f"media-{index}")
        for index, result in enumerate(analyses)
        if isinstance(result, MediaAnalysisSuccess)
    )
    assert len(references) == 2
    executor = CommandExecutor(
        replace(Project.empty(project_id="real-preview"), media=references)
    )
    executor.execute(AddMediaClip("media-0", "video-clip"))
    executor.execute(AddMediaClip("media-1", "photo-clip"))
    decoder = FfmpegFrameDecoder(timeout=15.0)

    video_target = frame_at_project_time(executor.project, ProjectTime.zero()).target
    photo_clip = executor.project.clip("photo-clip")
    photo_target = frame_at_project_time(executor.project, photo_clip.timeline_start).target

    assert video_target is not None
    assert photo_target is not None
    for target in (video_target, photo_target):
        decoded = decoder.decode(target, Event())
        assert isinstance(decoded, DecodedFrame)
        assert not QImage.fromData(decoded.png_bytes).isNull()

    assert {path: _fingerprint(path) for path in before} == before
