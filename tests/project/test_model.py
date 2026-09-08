from dataclasses import FrozenInstanceError, replace

import pytest

from movie_maker.project import (
    Canvas,
    Clip,
    FrameRate,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    PlaybackRate,
    Project,
    ProjectTime,
    ProjectValidationError,
    TimelineTrack,
    TrackKind,
)


def _video() -> MediaReference:
    return MediaReference(
        asset_id="media-video",
        name="바다.mp4",
        source_path="D:/Media/바다.mp4",
        kind=MediaKind.VIDEO,
        duration=ProjectTime.from_seconds(12),
        width=1920,
        height=1080,
    )


def _visual_clip(*, start_seconds: int = 0, clip_id: str = "clip-video") -> Clip:
    return Clip(
        clip_id=clip_id,
        track=TrackKind.VISUAL,
        asset_id="media-video",
        label="바다",
        timeline_start=ProjectTime.from_seconds(start_seconds),
        duration=ProjectTime.from_seconds(5),
        source_in=ProjectTime.from_seconds(2),
        source_out=ProjectTime.from_seconds(7),
    )


def _project_with_video(*clips: Clip) -> Project:
    empty = Project.empty(project_id="project-1", name="여행")
    tracks = tuple(
        TimelineTrack(track.kind, clips if track.kind is TrackKind.VISUAL else ())
        for track in empty.tracks
    )
    return replace(
        empty,
        canvas=Canvas(width=1920, height=1080, reference_asset_id="media-video"),
        media=(_video(),),
        tracks=tracks,
    )


def test_empty_project_has_fixed_immutable_track_structure() -> None:
    project = Project.empty(project_id="project-1")

    assert tuple(track.kind for track in project.tracks) == tuple(TrackKind)
    assert project.duration == ProjectTime.zero()
    with pytest.raises(FrozenInstanceError):
        project.name = "변경"  # type: ignore[misc]
    with pytest.raises(ProjectValidationError, match="immutable tuple"):
        replace(project, media=[])  # type: ignore[arg-type]


def test_project_accepts_contiguous_non_destructive_visual_clips() -> None:
    first = _visual_clip()
    second = replace(
        first,
        clip_id="clip-video-2",
        timeline_start=ProjectTime.from_seconds(5),
        source_in=ProjectTime.from_seconds(7),
        source_out=ProjectTime.from_seconds(12),
    )
    project = _project_with_video(first, second)

    assert project.duration == ProjectTime.from_seconds(10)
    assert project.media_reference("media-video") == _video()
    assert project.clip("clip-video-2") == second


def test_playback_rate_is_a_normalised_positive_fraction() -> None:
    assert PlaybackRate(4, 2) == PlaybackRate(2, 1)
    assert PlaybackRate(3, 2).fraction.numerator == 3

    with pytest.raises(ProjectValidationError):
        PlaybackRate(0, 1)


def test_project_rejects_visual_gaps_and_duplicate_clip_ids() -> None:
    with pytest.raises(ProjectValidationError, match="contiguous"):
        _project_with_video(_visual_clip(start_seconds=1))

    duplicate = _visual_clip()
    text_clip = Clip(
        clip_id=duplicate.clip_id,
        track=TrackKind.TEXT,
        asset_id=None,
        label="캡션",
        timeline_start=ProjectTime.zero(),
        duration=ProjectTime.from_seconds(2),
    )
    empty = Project.empty(project_id="project-1")
    tracks = tuple(
        TimelineTrack(
            track.kind,
            (duplicate,) if track.kind is TrackKind.VISUAL else (text_clip,)
            if track.kind is TrackKind.TEXT
            else (),
        )
        for track in empty.tracks
    )
    with pytest.raises(ProjectValidationError, match="unique"):
        replace(empty, media=(_video(),), tracks=tracks)


def test_project_rejects_missing_or_incompatible_media_references() -> None:
    project = Project.empty(project_id="project-1")
    visual = TimelineTrack(TrackKind.VISUAL, (_visual_clip(),))
    tracks = (visual, *project.tracks[1:])

    with pytest.raises(ProjectValidationError, match="existing media"):
        replace(project, tracks=tracks)

    audio = MediaReference(
        asset_id="media-video",
        name="음악.wav",
        source_path="D:/Media/음악.wav",
        kind=MediaKind.AUDIO,
        duration=ProjectTime.from_seconds(12),
    )
    with pytest.raises(ProjectValidationError, match="not compatible"):
        replace(project, media=(audio,), tracks=tracks)


def test_project_rejects_source_ranges_outside_media() -> None:
    too_long = replace(_visual_clip(), source_out=ProjectTime.from_seconds(13))

    with pytest.raises(ProjectValidationError, match="exceeds"):
        _project_with_video(too_long)


def test_project_rejects_inconsistent_timed_duration_and_photo_playback_rate() -> None:
    inconsistent = replace(_visual_clip(), duration=ProjectTime.from_seconds(4))
    with pytest.raises(ProjectValidationError, match="source span"):
        _project_with_video(inconsistent)

    photo = MediaReference(
        asset_id="photo",
        name="사진.jpg",
        source_path="D:/Media/사진.jpg",
        kind=MediaKind.PHOTO,
        duration=None,
        width=1920,
        height=1080,
    )
    photo_clip = replace(
        _visual_clip(),
        asset_id=photo.asset_id,
        playback_rate=PlaybackRate(2, 1),
    )
    project = Project.empty(project_id="project-photo")
    tracks = (
        TimelineTrack(TrackKind.VISUAL, (photo_clip,)),
        *project.tracks[1:],
    )
    with pytest.raises(ProjectValidationError, match="Photo clips"):
        replace(project, media=(photo,), tracks=tracks)


def test_media_and_canvas_validate_dimensions_and_types() -> None:
    with pytest.raises(ProjectValidationError, match="present together"):
        replace(_video(), height=None)
    with pytest.raises(ProjectValidationError, match="Audio media"):
        replace(_video(), kind=MediaKind.AUDIO)
    with pytest.raises(ProjectValidationError, match="reference media"):
        replace(
            Project.empty(project_id="project-1"),
            canvas=Canvas(width=1280, height=720, reference_asset_id="missing"),
        )


def test_analyzed_media_preserves_validated_source_timing_metadata() -> None:
    video_stream = MediaStream(
        index=0,
        kind=MediaStreamKind.VIDEO,
        codec_name="h264",
        time_base=MediaTimeBase(2, 60_000),
        start_pts=-1001,
        duration_ts=300_300,
        average_frame_rate=FrameRate(30_000, 1001),
    )
    media = replace(
        _video(),
        primary_stream_index=0,
        streams=(video_stream,),
    )

    assert video_stream.time_base == MediaTimeBase(1, 30_000)
    assert video_stream.time_base.seconds_per_tick.denominator == 30_000
    assert media.streams == (video_stream,)

    with pytest.raises(ProjectValidationError, match="identify"):
        replace(media, primary_stream_index=1)
    with pytest.raises(ProjectValidationError, match="unique"):
        replace(media, streams=(video_stream, video_stream))
    with pytest.raises(ProjectValidationError, match="match"):
        replace(media, kind=MediaKind.AUDIO, width=None, height=None)
