from dataclasses import replace
from pathlib import Path

import pytest

from movie_maker.exporting import (
    ExportPlanError,
    ExportPlanErrorCode,
    ExportPreset,
    build_export_plan,
    ffmpeg_export_arguments,
    ffmpeg_export_filter,
)
from movie_maker.project import (
    AudioLevel,
    Canvas,
    Clip,
    CommandExecutor,
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
from movie_maker.timeline import SetPlaybackRate


def export_project(video_path: str, photo_path: str, music_path: str) -> Project:
    video = MediaReference(
        "video",
        "한글 video.mp4",
        video_path,
        MediaKind.VIDEO,
        ProjectTime.from_seconds(6),
        640,
        480,
        0,
        (
            MediaStream(
                0,
                MediaStreamKind.VIDEO,
                "h264",
                MediaTimeBase(1, 30),
            ),
            MediaStream(
                1,
                MediaStreamKind.AUDIO,
                "aac",
                MediaTimeBase(1, 48_000),
                sample_rate=48_000,
            ),
        ),
    )
    photo = MediaReference(
        "photo",
        "photo.png",
        photo_path,
        MediaKind.PHOTO,
        None,
        800,
        600,
        2,
        (
            MediaStream(
                2,
                MediaStreamKind.VIDEO,
                "png",
                MediaTimeBase(1, 25),
            ),
        ),
    )
    music = MediaReference(
        "music",
        "music.wav",
        music_path,
        MediaKind.AUDIO,
        ProjectTime.from_seconds(5),
        primary_stream_index=3,
        streams=(
            MediaStream(
                3,
                MediaStreamKind.AUDIO,
                "pcm_s16le",
                MediaTimeBase(1, 32_000),
                sample_rate=32_000,
            ),
        ),
    )
    first = Clip(
        "first",
        TrackKind.VISUAL,
        video.asset_id,
        "video",
        ProjectTime.zero(),
        ProjectTime.from_seconds(2),
        source_in=ProjectTime.from_seconds(1),
        source_out=ProjectTime.from_seconds(5),
        playback_rate=PlaybackRate(2),
        audio_level=AudioLevel(70),
    )
    second = Clip(
        "second",
        TrackKind.VISUAL,
        photo.asset_id,
        "photo",
        ProjectTime.from_seconds(2),
        ProjectTime.from_seconds(1),
    )
    music_clip = Clip(
        "music-clip",
        TrackKind.MUSIC,
        music.asset_id,
        "music",
        ProjectTime.from_milliseconds(500),
        ProjectTime.from_seconds(2),
        source_in=ProjectTime.from_seconds(1),
        source_out=ProjectTime.from_seconds(3),
        audio_level=AudioLevel(40),
    )
    empty = Project.empty(project_id="export-project")
    return replace(
        empty,
        canvas=Canvas(640, 480, video.asset_id),
        media=(video, photo, music),
        tracks=(
            TimelineTrack(TrackKind.VISUAL, (first, second)),
            TimelineTrack(TrackKind.MUSIC, (music_clip,)),
            empty.track(TrackKind.NARRATION),
            empty.track(TrackKind.TEXT),
        ),
    )


@pytest.mark.parametrize(
    ("preset", "size"),
    (
        (ExportPreset.ORIGINAL, (640, 480)),
        (ExportPreset.HD_720, (1280, 720)),
        (ExportPreset.HD_1080, (1920, 1080)),
    ),
)
def test_presets_keep_the_snapshot_and_have_deterministic_sizes(
    tmp_path: Path,
    preset: ExportPreset,
    size: tuple[int, int],
) -> None:
    project = export_project("video.mp4", "photo.png", "music.wav")
    plan = build_export_plan(project, str(tmp_path / "result.mp4"), preset)

    assert plan.project is project
    assert (plan.width, plan.height) == size
    assert plan.duration == ProjectTime.from_seconds(3)
    assert plan.output_frame_count == 90


def test_filter_reuses_audio_graph_and_maps_video_photo_speed_and_fit(tmp_path: Path) -> None:
    plan = build_export_plan(
        export_project("video.mp4", "photo.png", "music.wav"),
        str(tmp_path / "result.mp4"),
        ExportPreset.ORIGINAL,
    )
    graph = ffmpeg_export_filter(plan)

    assert "[0:0]trim=start=1:end=5,setpts=(PTS-STARTPTS)*1/2" in graph
    assert "[1:2]trim=duration=1" in graph
    assert "scale=640:480:force_original_aspect_ratio=decrease" in graph
    assert "pad=640:480" in graph
    assert "[v0][v1]concat=n=2:v=1:a=0" in graph
    assert "[2:1]atrim=start=1:end=5" in graph
    assert "[3:3]atrim=start=1:end=3" in graph
    assert "amix=inputs=2:normalize=0" in graph


def test_argv_preserves_unicode_space_paths_and_selects_h264_aac(tmp_path: Path) -> None:
    project = export_project(
        "D:/긴 경로/한글 video source.mp4",
        "D:/긴 경로/photo source.png",
        "D:/긴 경로/music source.wav",
    )
    plan = build_export_plan(
        project,
        str(tmp_path / "한글 output file.mp4"),
        ExportPreset.HD_720,
    )
    temporary = str(tmp_path / ".temporary output.partial.mp4")
    arguments = ffmpeg_export_arguments("custom ffmpeg", plan, temporary)

    assert arguments[0] == "custom ffmpeg"
    assert project.media_reference("video").source_path in arguments
    assert project.media_reference("photo").source_path in arguments
    assert project.media_reference("music").source_path in arguments
    assert arguments[-1] == temporary
    assert arguments[arguments.index("-c:v") + 1] == "libx264"
    assert arguments[arguments.index("-c:a") + 1] == "aac"
    assert "-progress" in arguments
    assert "shell" not in arguments


def test_invalid_timeline_target_canvas_and_stream_are_typed(tmp_path: Path) -> None:
    empty = Project.empty(project_id="empty")
    with pytest.raises(ExportPlanError) as no_timeline:
        build_export_plan(empty, str(tmp_path / "x.mp4"), ExportPreset.ORIGINAL)
    assert no_timeline.value.code is ExportPlanErrorCode.EMPTY_TIMELINE

    project = export_project("video.mp4", "photo.png", "music.wav")
    with pytest.raises(ExportPlanError) as invalid_target:
        build_export_plan(project, str(tmp_path / "x.mov"), ExportPreset.ORIGINAL)
    assert invalid_target.value.code is ExportPlanErrorCode.INVALID_TARGET

    odd_canvas = replace(project, canvas=Canvas(641, 479, "video"))
    odd_plan = build_export_plan(odd_canvas, str(tmp_path / "odd.mp4"), ExportPreset.ORIGINAL)
    odd_arguments = ffmpeg_export_arguments("ffmpeg", odd_plan, str(tmp_path / "odd.partial.mp4"))
    assert (odd_plan.width, odd_plan.height) == (641, 479)
    assert odd_arguments[odd_arguments.index("-pix_fmt") + 1] == "yuv444p"

    video = replace(
        project.media_reference("video"),
        primary_stream_index=None,
        streams=(),
    )
    missing_stream = replace(project, media=(video, *project.media[1:]))
    with pytest.raises(ExportPlanError) as no_stream:
        build_export_plan(missing_stream, str(tmp_path / "x.mp4"), ExportPreset.ORIGINAL)
    assert no_stream.value.code is ExportPlanErrorCode.NO_VIDEO_STREAM

    executor = CommandExecutor(project)
    executor.execute(SetPlaybackRate("first", PlaybackRate(4)))
    with pytest.raises(ExportPlanError) as audio_graph:
        build_export_plan(executor.project, str(tmp_path / "x.mp4"), ExportPreset.ORIGINAL)
    assert audio_graph.value.code is ExportPlanErrorCode.INVALID_AUDIO_GRAPH
