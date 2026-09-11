from __future__ import annotations

import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event

import numpy as np

from movie_maker.audio import AudioGraph, DecodedAudio, FfmpegAudioDecoder, build_audio_graph
from movie_maker.media import FfprobeAnalyzer, MediaAnalysisSuccess
from movie_maker.project import AudioLevel, CommandExecutor, Project, ProjectTime
from movie_maker.timeline import AddMediaClip, UpdateClipAudio


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


def _generate_sources(tmp_path: Path) -> tuple[Path, Path]:
    video = tmp_path / "video 44100 mono.mp4"
    music = tmp_path / "music 32000 stereo.wav"
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1",
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
            "sine=frequency=660:sample_rate=32000:duration=1",
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
    return video, music


def test_real_ffmpeg_mix_normalizes_places_and_preserves_mixed_sources(tmp_path: Path) -> None:
    video_path, music_path = _generate_sources(tmp_path)
    before = {path: _fingerprint(path) for path in (video_path, music_path)}
    analyses = [FfprobeAnalyzer().analyze(path) for path in (video_path, music_path)]
    assert all(isinstance(result, MediaAnalysisSuccess) for result in analyses)
    references = tuple(
        result.analysis.to_media_reference(f"media-{index}")
        for index, result in enumerate(analyses)
        if isinstance(result, MediaAnalysisSuccess)
    )
    executor = CommandExecutor(
        replace(Project.empty(project_id="real-audio"), media=references)
    )
    executor.execute(AddMediaClip("media-0", "video-clip"))
    executor.execute(
        AddMediaClip(
            "media-1",
            "music-clip",
            timeline_start=ProjectTime.from_milliseconds(250),
        )
    )
    executor.execute(UpdateClipAudio("video-clip", audio_muted=True))
    executor.execute(UpdateClipAudio("music-clip", audio_level=AudioLevel(50)))
    graph = build_audio_graph(executor.project)
    decoder = FfmpegAudioDecoder(timeout=20.0)

    first = decoder.decode(graph, Event())
    second = decoder.decode(graph, Event())

    assert isinstance(first, DecodedAudio)
    assert isinstance(second, DecodedAudio)
    assert first.pcm_bytes == second.pcm_bytes
    samples = np.frombuffer(first.pcm_bytes, dtype="<i2").reshape(-1, 2)
    assert len(samples) == graph.output_frames
    assert np.max(np.abs(samples[: 9_600])) == 0
    assert np.max(np.abs(samples[14_400:])) > 0
    assert np.array_equal(samples[:, 0], samples[:, 1])
    assert {path: _fingerprint(path) for path in before} == before


def test_real_ffmpeg_graph_without_sources_produces_exact_silence() -> None:
    graph = AudioGraph(
        "silent-project",
        ProjectTime.zero(),
        ProjectTime.from_milliseconds(20),
        (),
    )

    result = FfmpegAudioDecoder(timeout=10.0).decode(graph, Event())

    assert isinstance(result, DecodedAudio)
    assert result.pcm_bytes == bytes(graph.output_frames * 4)
