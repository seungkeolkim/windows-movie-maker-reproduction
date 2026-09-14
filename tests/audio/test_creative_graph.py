from __future__ import annotations

import math
import subprocess
from array import array
from dataclasses import replace
from pathlib import Path
from threading import Event

from movie_maker.audio import (
    AudioSourceKind,
    DecodedAudio,
    FfmpegAudioDecoder,
    build_audio_graph,
    ffmpeg_audio_filter,
)
from movie_maker.project import (
    AudioLevel,
    Canvas,
    Clip,
    CommandExecutor,
    DuckingPreset,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    MixerSettings,
    Project,
    ProjectTime,
    TimelineTrack,
    TrackKind,
)
from movie_maker.timeline import SplitClip


def _audio(asset_id: str, duration: int) -> MediaReference:
    return MediaReference(
        asset_id,
        f"{asset_id}.wav",
        f"C:/{asset_id}.wav",
        MediaKind.AUDIO,
        ProjectTime.from_seconds(duration),
        primary_stream_index=0,
        streams=(
            MediaStream(
                0,
                MediaStreamKind.AUDIO,
                "pcm_s16le",
                MediaTimeBase(1, 48_000),
                duration_ts=duration * 48_000,
                sample_rate=48_000,
            ),
        ),
    )


def _project() -> Project:
    photo = MediaReference(
        "photo",
        "photo.png",
        "C:/photo.png",
        MediaKind.PHOTO,
        None,
        1280,
        720,
    )
    music = _audio("music", 5)
    narration = _audio("narration", 2)
    return Project(
        1,
        "audio-creative",
        "오디오 창작",
        Canvas(1280, 720, photo.asset_id),
        (photo, music, narration),
        (
            TimelineTrack(
                TrackKind.VISUAL,
                (
                    Clip(
                        "visual",
                        TrackKind.VISUAL,
                        photo.asset_id,
                        "사진",
                        ProjectTime.zero(),
                        ProjectTime.from_seconds(5),
                    ),
                ),
            ),
            TimelineTrack(
                TrackKind.MUSIC,
                (
                    Clip(
                        "music-clip",
                        TrackKind.MUSIC,
                        music.asset_id,
                        "음악",
                        ProjectTime.zero(),
                        ProjectTime.from_seconds(5),
                        source_out=ProjectTime.from_seconds(5),
                        audio_level=AudioLevel(50),
                        fade_in=ProjectTime.from_seconds(1),
                        fade_out=ProjectTime.from_seconds(1),
                    ),
                ),
            ),
            TimelineTrack(
                TrackKind.NARRATION,
                (
                    Clip(
                        "narration-clip",
                        TrackKind.NARRATION,
                        narration.asset_id,
                        "내레이션",
                        ProjectTime.from_seconds(1),
                        ProjectTime.from_seconds(2),
                        source_out=ProjectTime.from_seconds(2),
                        audio_level=AudioLevel(90),
                        ducking=DuckingPreset.MEDIUM,
                    ),
                ),
            ),
            TimelineTrack(TrackKind.TEXT),
        ),
        mixer=MixerSettings(AudioLevel(100), AudioLevel(80), AudioLevel(70)),
    )


def test_narration_fades_bus_gain_and_ducking_share_one_audio_graph() -> None:
    graph = build_audio_graph(_project())
    assert [source.kind for source in graph.sources] == [
        AudioSourceKind.MUSIC,
        AudioSourceKind.NARRATION,
    ]
    music, narration = graph.sources
    assert music.level == AudioLevel(50)
    assert music.bus_level == AudioLevel(80)
    assert music.ducking_windows[0].preset is DuckingPreset.MEDIUM
    assert narration.bus_level == AudioLevel(70)

    filter_graph = ffmpeg_audio_filter(graph)
    assert "0.5*0.8" in filter_graph
    assert "volume=0.63" in filter_graph
    assert "eval=frame" in filter_graph
    assert "0.4" in filter_graph


def test_partial_preview_range_keeps_absolute_fade_meaning() -> None:
    graph = build_audio_graph(
        _project(),
        start=ProjectTime.from_milliseconds(500),
        duration=ProjectTime.from_seconds(1),
    )
    filter_graph = ffmpeg_audio_filter(graph)

    assert "(t+0.5)/1" in filter_graph
    assert graph.sources[0].timeline_delay == ProjectTime.zero()


def test_split_preserves_only_the_outer_fades_and_undo_restores_original() -> None:
    executor = CommandExecutor(_project())

    executor.execute(
        SplitClip("music-clip", ProjectTime.from_seconds(2), "music-clip-back")
    )
    front = executor.project.clip("music-clip")
    back = executor.project.clip("music-clip-back")
    assert (front.fade_in, front.fade_out) == (ProjectTime.from_seconds(1), ProjectTime.zero())
    assert (back.fade_in, back.fade_out) == (ProjectTime.zero(), ProjectTime.from_seconds(1))

    assert executor.undo() == _project()


def _tone(path: Path, frequency: int) -> None:
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration=5",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(path),
        ),
        check=True,
        shell=False,
    )


def _frequency_amplitude(pcm: bytes, frequency: int, start_seconds: float) -> float:
    samples = array("h")
    samples.frombytes(pcm)
    frame_start = round(start_seconds * 48_000)
    frame_count = round(0.2 * 48_000)
    left = samples[frame_start * 2 : (frame_start + frame_count) * 2 : 2]
    real = 0.0
    imaginary = 0.0
    for index, sample in enumerate(left):
        angle = 2 * math.pi * frequency * index / 48_000
        real += sample * math.cos(angle)
        imaginary -= sample * math.sin(angle)
    return math.hypot(real, imaginary) / frame_count


def _real_audio_project(music_path: Path, narration_path: Path) -> Project:
    project = _project()
    media = tuple(
        replace(
            reference,
            source_path=(
                str(music_path)
                if reference.asset_id == "music"
                else str(narration_path)
                if reference.asset_id == "narration"
                else reference.source_path
            ),
        )
        for reference in project.media
    )
    return replace(project, media=media)


def _without_ducking(project: Project) -> Project:
    tracks = tuple(
        replace(
            track,
            clips=tuple(replace(clip, ducking=DuckingPreset.OFF) for clip in track.clips),
        )
        if track.kind is TrackKind.NARRATION
        else track
        for track in project.tracks
    )
    return replace(project, tracks=tracks)


def test_real_wav_samples_apply_linear_fade_and_documented_ducking(tmp_path: Path) -> None:
    music_path = tmp_path / "music.wav"
    narration_path = tmp_path / "narration.wav"
    _tone(music_path, 1_000)
    _tone(narration_path, 440)
    project = _real_audio_project(music_path, narration_path)
    decoder = FfmpegAudioDecoder(timeout=30)

    ducked = decoder.decode(build_audio_graph(project), Event())
    unducked = decoder.decode(build_audio_graph(_without_ducking(project)), Event())
    assert isinstance(ducked, DecodedAudio), ducked
    assert isinstance(unducked, DecodedAudio), unducked

    early = _frequency_amplitude(unducked.pcm_bytes, 1_000, 0.2)
    late = _frequency_amplitude(unducked.pcm_bytes, 1_000, 0.7)
    assert 2.5 < late / early < 3.5

    ducked_music = _frequency_amplitude(ducked.pcm_bytes, 1_000, 1.4)
    unducked_music = _frequency_amplitude(unducked.pcm_bytes, 1_000, 1.4)
    assert 0.35 < ducked_music / unducked_music < 0.45
