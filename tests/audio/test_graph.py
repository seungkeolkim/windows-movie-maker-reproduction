from dataclasses import replace

import pytest

from movie_maker.audio import (
    AudioGraphError,
    AudioGraphErrorCode,
    AudioSourceKind,
    build_audio_graph,
    ffmpeg_audio_arguments,
    ffmpeg_audio_filter,
)
from movie_maker.project import (
    AudioLevel,
    Canvas,
    Clip,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    PlaybackRate,
    Project,
    ProjectTime,
    TimelineTrack,
    TrackKind,
)


def _project(*, video_has_audio: bool = True, music_has_stream: bool = True) -> Project:
    video_streams = (
        MediaStream(0, MediaStreamKind.VIDEO, "h264", MediaTimeBase(1, 30)),
    )
    if video_has_audio:
        video_streams += (
            MediaStream(
                1,
                MediaStreamKind.AUDIO,
                "aac",
                MediaTimeBase(1, 44_100),
                sample_rate=44_100,
            ),
        )
    video = MediaReference(
        "video",
        "video with audio.mp4",
        "D:/Media/video with audio.mp4",
        MediaKind.VIDEO,
        ProjectTime.from_seconds(10),
        640,
        360,
        0,
        video_streams,
    )
    music_streams = (
        MediaStream(
            2,
            MediaStreamKind.AUDIO,
            "pcm_s16le",
            MediaTimeBase(1, 32_000),
            sample_rate=32_000,
        ),
    ) if music_has_stream else ()
    music = MediaReference(
        "music",
        "music file.wav",
        "D:/Media/music file.wav",
        MediaKind.AUDIO,
        ProjectTime.from_seconds(6),
        primary_stream_index=2 if music_has_stream else None,
        streams=music_streams,
    )
    visual_clip = Clip(
        "visual",
        TrackKind.VISUAL,
        video.asset_id,
        "visual",
        ProjectTime.zero(),
        ProjectTime.from_seconds(4),
        source_in=ProjectTime.from_seconds(2),
        source_out=ProjectTime.from_seconds(10),
        playback_rate=PlaybackRate(2),
        audio_level=AudioLevel(80),
    )
    music_clip = Clip(
        "music-clip",
        TrackKind.MUSIC,
        music.asset_id,
        "music",
        ProjectTime.from_milliseconds(500),
        ProjectTime.from_seconds(6),
        source_out=ProjectTime.from_seconds(6),
        audio_level=AudioLevel(50),
    )
    empty = Project.empty(project_id="graph")
    return replace(
        empty,
        canvas=Canvas(640, 360, video.asset_id),
        media=(video, music),
        tracks=(
            TimelineTrack(TrackKind.VISUAL, (visual_clip,)),
            TimelineTrack(TrackKind.MUSIC, (music_clip,)),
            empty.track(TrackKind.NARRATION),
            empty.track(TrackKind.TEXT),
        ),
    )


def test_graph_maps_project_range_to_video_speed_and_music_source_time() -> None:
    graph = build_audio_graph(
        _project(),
        start=ProjectTime.from_seconds(1),
        duration=ProjectTime.from_seconds(2),
    )

    original, music = graph.sources
    assert original.kind is AudioSourceKind.ORIGINAL
    assert (original.source_in, original.source_out) == (
        ProjectTime.from_seconds(4),
        ProjectTime.from_seconds(8),
    )
    assert original.duration == ProjectTime.from_seconds(2)
    assert original.playback_rate == PlaybackRate(2)
    assert original.stream_index == 1
    assert music.kind is AudioSourceKind.MUSIC
    assert (music.source_in, music.source_out) == (
        ProjectTime.from_milliseconds(500),
        ProjectTime.from_milliseconds(2_500),
    )
    assert music.stream_index == 2


def test_music_is_clipped_to_visual_project_length_without_extending_it() -> None:
    graph = build_audio_graph(_project())
    music = graph.sources[1]

    assert graph.duration == ProjectTime.from_seconds(4)
    assert music.timeline_delay == ProjectTime.from_milliseconds(500)
    assert music.duration == ProjectTime.from_milliseconds(3_500)
    assert music.source_out == ProjectTime.from_milliseconds(3_500)


def test_filter_normalizes_inputs_mixes_without_auto_gain_and_limits_clipping() -> None:
    graph = build_audio_graph(_project())
    filter_graph = ffmpeg_audio_filter(graph)
    arguments = ffmpeg_audio_arguments("custom ffmpeg", graph)

    assert "[0:1]atrim=start=2:end=10" in filter_graph
    assert "atempo=2" in filter_graph
    assert "volume=0.8" in filter_graph
    assert "[1:2]atrim=start=0:end=3.5" in filter_graph
    assert "aresample=48000" in filter_graph
    assert "channel_layouts=stereo" in filter_graph
    assert "amix=inputs=2:normalize=0" in filter_graph
    assert "alimiter=limit=0.95" in filter_graph
    assert arguments[0] == "custom ffmpeg"
    assert arguments.count("-i") == 2
    assert arguments[arguments.index("-i") + 1] == "D:/Media/video with audio.mp4"
    assert "D:/Media/music file.wav" in arguments
    assert "shell" not in arguments


def test_video_without_audio_is_normal_silence() -> None:
    project = _project(video_has_audio=False)
    project = replace(
        project,
        tracks=(
            project.track(TrackKind.VISUAL),
            TimelineTrack(TrackKind.MUSIC),
            project.track(TrackKind.NARRATION),
            project.track(TrackKind.TEXT),
        ),
    )

    graph = build_audio_graph(project)

    assert graph.sources == ()
    assert ffmpeg_audio_filter(graph).startswith("anullsrc=r=48000:cl=stereo")


def test_audio_asset_without_an_explicit_stream_is_rejected() -> None:
    with pytest.raises(AudioGraphError) as raised:
        build_audio_graph(_project(music_has_stream=False))

    assert raised.value.code is AudioGraphErrorCode.NO_AUDIO_STREAM


def test_unsupported_original_audio_speed_is_not_silently_changed() -> None:
    project = _project()
    visual = replace(
        project.clip("visual"),
        duration=ProjectTime.from_seconds(2),
        playback_rate=PlaybackRate(4),
    )
    project = replace(
        project,
        tracks=(
            TimelineTrack(TrackKind.VISUAL, (visual,)),
            *project.tracks[1:],
        ),
    )

    with pytest.raises(AudioGraphError) as raised:
        build_audio_graph(project)

    assert raised.value.code is AudioGraphErrorCode.UNSUPPORTED_SPEED
