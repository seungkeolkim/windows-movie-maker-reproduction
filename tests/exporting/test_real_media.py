from __future__ import annotations

import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from movie_maker.exporting import (
    ExportPreset,
    ExportProgress,
    ExportSucceeded,
    FfmpegExportRunner,
    build_export_plan,
)
from movie_maker.media import FfprobeAnalyzer, MediaAnalysisSuccess
from movie_maker.project import AudioLevel, CommandExecutor, Project, ProjectTime
from movie_maker.timeline import (
    AddMediaClip,
    DeleteTimelineClip,
    SetPhotoDuration,
    SplitClip,
    UpdateClipAudio,
    UpdateClipTiming,
)


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


def _generate_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    video = tmp_path / "한글 red video 44100.mp4"
    photo = tmp_path / "blue still photo.png"
    music = tmp_path / "music 32000 stereo.wav"
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x48:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
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
            "color=c=blue:s=80x60",
            "-frames:v",
            "1",
            "-y",
            str(photo),
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
            "sine=frequency=660:sample_rate=32000:duration=2",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(music),
        ),
        check=True,
        shell=False,
    )
    return video, photo, music


def _actual_project(paths: tuple[Path, Path, Path]) -> Project:
    analyses = [FfprobeAnalyzer().analyze(path) for path in paths]
    assert all(isinstance(result, MediaAnalysisSuccess) for result in analyses)
    references = tuple(
        result.analysis.to_media_reference(f"media-{index}")
        for index, result in enumerate(analyses)
        if isinstance(result, MediaAnalysisSuccess)
    )
    executor = CommandExecutor(
        replace(Project.empty(project_id="real-export"), media=references)
    )
    executor.execute(AddMediaClip("media-0", "video-clip"))
    executor.execute(AddMediaClip("media-1", "photo-clip"))
    executor.execute(SetPhotoDuration("photo-clip", ProjectTime.from_seconds(1)))
    executor.execute(
        AddMediaClip(
            "media-2",
            "music-clip",
            timeline_start=ProjectTime.from_milliseconds(250),
        )
    )
    executor.execute(
        UpdateClipTiming(
            "video-clip",
            source_in=ProjectTime.from_milliseconds(500),
            source_out=ProjectTime.from_milliseconds(1_500),
        )
    )
    executor.execute(UpdateClipAudio("video-clip", audio_muted=True))
    executor.execute(UpdateClipAudio("music-clip", audio_level=AudioLevel(50)))
    return executor.project


def test_real_mp4_contains_edited_visuals_audio_and_preserves_all_sources(tmp_path: Path) -> None:
    paths = _generate_sources(tmp_path)
    before = {path: _fingerprint(path) for path in paths}
    project = _actual_project(paths)
    target = tmp_path / "완성 output file.mp4"
    plan = build_export_plan(project, str(target), ExportPreset.ORIGINAL)
    progress: list[ExportProgress] = []

    result = FfmpegExportRunner(minimum_timeout=30.0).run(plan, Event(), progress.append)

    assert isinstance(result, ExportSucceeded), result
    assert target.is_file()
    assert (result.verification.width, result.verification.height) == (64, 48)
    assert result.verification.video_codec == "h264"
    assert result.verification.audio_codec == "aac"
    assert result.verification.frame_count == plan.output_frame_count
    assert abs(result.verification.duration.nanoseconds - 2_000_000_000) <= 100_000_000
    assert progress[-1].percent == 100
    assert [item.percent for item in progress] == sorted(item.percent for item in progress)
    assert not tuple(tmp_path.glob("*.partial.mp4"))

    def frame_at(seconds: str) -> np.ndarray:
        raw = subprocess.run(
            (
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                seconds,
                "-i",
                str(target),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ),
            check=True,
            capture_output=True,
            shell=False,
        ).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(48, 64, 3)

    red_frame = frame_at("0.25")
    blue_frame = frame_at("1.5")
    assert red_frame[:, :, 0].mean() > red_frame[:, :, 2].mean() + 100
    assert blue_frame[:, :, 2].mean() > blue_frame[:, :, 0].mean() + 100

    pcm = subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(target),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "pipe:1",
        ),
        check=True,
        capture_output=True,
        shell=False,
    ).stdout
    samples = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2)
    assert np.max(np.abs(samples[:4_800])) == 0
    assert np.max(np.abs(samples[16_800:])) > 0
    assert {path: _fingerprint(path) for path in paths} == before


def test_real_export_joins_kept_ranges_from_one_split_video(tmp_path: Path) -> None:
    source = tmp_path / "single source.mp4"
    target = tmp_path / "cut and joined.mp4"
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x48:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=64x48:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:r=30:d=1",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(source),
        ),
        check=True,
        shell=False,
    )
    analysis = FfprobeAnalyzer().analyze(source)
    assert isinstance(analysis, MediaAnalysisSuccess)
    media = analysis.analysis.to_media_reference("single-video")
    executor = CommandExecutor(replace(Project.empty(project_id="split-export"), media=(media,)))
    executor.execute(AddMediaClip(media.asset_id, "whole"))
    executor.execute(SplitClip("whole", ProjectTime.from_seconds(1), "middle"))
    executor.execute(SplitClip("middle", ProjectTime.from_seconds(2), "last"))
    executor.execute(DeleteTimelineClip("middle"))
    plan = build_export_plan(executor.project, str(target), ExportPreset.ORIGINAL)

    result = FfmpegExportRunner(minimum_timeout=30.0).run(plan, Event(), lambda _value: None)

    assert isinstance(result, ExportSucceeded), result
    assert result.verification.frame_count == 60

    def dominant_channel(seconds: str) -> int:
        raw = subprocess.run(
            (
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                seconds,
                "-i",
                str(target),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ),
            check=True,
            capture_output=True,
            shell=False,
        ).stdout
        return int(np.frombuffer(raw, dtype=np.uint8).reshape(48, 64, 3).mean(axis=(0, 1)).argmax())

    assert dominant_channel("0.25") == 0
    assert dominant_channel("1.25") == 2


@pytest.mark.parametrize(
    ("preset", "expected", "source_size"),
    (
        (ExportPreset.ORIGINAL, (81, 61), "81x61"),
        (ExportPreset.HD_720, (1280, 720), "80x60"),
        (ExportPreset.HD_1080, (1920, 1080), "80x60"),
    ),
)
def test_real_presets_are_decodable_with_silent_aac(
    tmp_path: Path,
    preset: ExportPreset,
    expected: tuple[int, int],
    source_size: str,
) -> None:
    photo = tmp_path / "preset photo.png"
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=green:s={source_size}",
            "-frames:v",
            "1",
            "-y",
            str(photo),
        ),
        check=True,
        shell=False,
    )
    analysis = FfprobeAnalyzer().analyze(photo)
    assert isinstance(analysis, MediaAnalysisSuccess)
    reference = analysis.analysis.to_media_reference("photo")
    executor = CommandExecutor(
        replace(Project.empty(project_id=f"real-{preset.value}"), media=(reference,))
    )
    executor.execute(AddMediaClip("photo", "photo-clip"))
    executor.execute(SetPhotoDuration("photo-clip", ProjectTime.from_seconds(1)))
    plan = build_export_plan(
        executor.project,
        str(tmp_path / f"{preset.value}.mp4"),
        preset,
    )

    result = FfmpegExportRunner(minimum_timeout=30.0).run(
        plan,
        Event(),
        lambda progress: None,
    )

    assert isinstance(result, ExportSucceeded), result
    assert (result.verification.width, result.verification.height) == expected
    assert result.verification.audio_codec == "aac"
