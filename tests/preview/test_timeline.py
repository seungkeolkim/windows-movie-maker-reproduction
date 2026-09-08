from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from movie_maker.preview import (
    PlaybackClock,
    frame_at_project_time,
    step_project_frame,
)
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
    TimelineTrack,
    TrackKind,
)

DEFAULT_FRAME_RATE = FrameRate(24_000, 1_001)
DEFAULT_TIME_BASE = MediaTimeBase(1, 90_000)
DEFAULT_PLAYBACK_RATE = PlaybackRate()


def _video(
    asset_id: str,
    *,
    rate: FrameRate = DEFAULT_FRAME_RATE,
    time_base: MediaTimeBase = DEFAULT_TIME_BASE,
    start_pts: int = 9_000,
) -> MediaReference:
    return MediaReference(
        asset_id=asset_id,
        name=f"{asset_id}.mp4",
        source_path=f"C:/media/{asset_id}.mp4",
        kind=MediaKind.VIDEO,
        duration=ProjectTime.from_seconds(60),
        width=1920,
        height=1080,
        primary_stream_index=2,
        streams=(
            MediaStream(
                index=2,
                kind=MediaStreamKind.VIDEO,
                codec_name="h264",
                time_base=time_base,
                start_pts=start_pts,
                duration_ts=2_700_000,
                average_frame_rate=rate,
            ),
        ),
    )


def _photo() -> MediaReference:
    return MediaReference(
        asset_id="photo",
        name="photo.png",
        source_path="C:/media/photo.png",
        kind=MediaKind.PHOTO,
        duration=None,
        width=1600,
        height=900,
        primary_stream_index=0,
        streams=(
            MediaStream(
                index=0,
                kind=MediaStreamKind.VIDEO,
                codec_name="png",
                time_base=MediaTimeBase(1, 25),
                start_pts=0,
                duration_ts=1,
                average_frame_rate=FrameRate(25),
            ),
        ),
    )


def _project(media: tuple[MediaReference, ...], clips: tuple[Clip, ...]) -> Project:
    empty = Project.empty(project_id="preview-project")
    tracks = tuple(
        TimelineTrack(track.kind, clips if track.kind is TrackKind.VISUAL else ())
        for track in empty.tracks
    )
    return replace(
        empty,
        canvas=Canvas(width=1920, height=1080, reference_asset_id=media[0].asset_id),
        media=media,
        tracks=tracks,
    )


def _video_clip(
    asset_id: str,
    clip_id: str,
    *,
    start: ProjectTime,
    source_in: ProjectTime,
    source_out: ProjectTime,
    rate: PlaybackRate = DEFAULT_PLAYBACK_RATE,
) -> Clip:
    return Clip(
        clip_id=clip_id,
        track=TrackKind.VISUAL,
        asset_id=asset_id,
        label=clip_id,
        timeline_start=start,
        duration=ProjectTime.from_seconds(
            (source_out - source_in).to_fractional_seconds() / rate.fraction
        ),
        source_in=source_in,
        source_out=source_out,
        playback_rate=rate,
    )


@pytest.mark.parametrize(
    "rate",
    (FrameRate(24_000, 1_001), FrameRate(30_000, 1_001), FrameRate(60_000, 1_001)),
)
def test_source_in_speed_and_fractional_rates_use_absolute_frame_index(rate: FrameRate) -> None:
    media = _video("video", rate=rate)
    source_in = rate.time_at_frame(240)
    source_out = rate.time_at_frame(720)
    clip = _video_clip(
        "video",
        "clip",
        start=ProjectTime.zero(),
        source_in=source_in,
        source_out=source_out,
        rate=PlaybackRate(3, 2),
    )
    project = _project((media,), (clip,))
    timeline_position = ProjectTime.from_seconds(Fraction(1001, 1000))

    preview = frame_at_project_time(project, timeline_position)

    assert preview.target is not None
    requested = ProjectTime.from_seconds(
        source_in.to_fractional_seconds()
        + timeline_position.to_fractional_seconds() * Fraction(3, 2)
    )
    expected_index = rate.frame_at_or_before(requested)
    assert preview.target.source_time == rate.time_at_frame(expected_index)
    assert preview.target.source_time <= requested


def test_time_base_start_pts_and_clip_boundaries_are_preserved() -> None:
    rate = FrameRate(30_000, 1_001)
    first_media = _video("first", rate=rate, start_pts=4_500)
    second_media = _video("second", rate=rate, start_pts=18_000)
    first_out = rate.time_at_frame(60)
    first = _video_clip(
        "first",
        "first-clip",
        start=ProjectTime.zero(),
        source_in=ProjectTime.zero(),
        source_out=first_out,
    )
    second_in = rate.time_at_frame(300)
    second = _video_clip(
        "second",
        "second-clip",
        start=first.timeline_end,
        source_in=second_in,
        source_out=rate.time_at_frame(360),
    )
    project = _project((first_media, second_media), (first, second))

    boundary = frame_at_project_time(project, first.timeline_end)
    ending = frame_at_project_time(project, project.duration)

    assert boundary.target is not None
    assert boundary.target.clip_id == "second-clip"
    assert boundary.target.source_time == second_in
    assert boundary.target.start_pts == 18_000
    expected_pts = 18_000 + (
        second_in.to_fractional_seconds() // MediaTimeBase(1, 90_000).seconds_per_tick
    )
    assert boundary.target.source_pts == expected_pts
    assert ending.target is not None
    assert ending.target.clip_id == "second-clip"
    assert ending.target.source_time < second.source_out


def test_photo_uses_first_frame_and_empty_project_has_no_target() -> None:
    photo = _photo()
    clip = Clip(
        clip_id="photo-clip",
        track=TrackKind.VISUAL,
        asset_id="photo",
        label="photo",
        timeline_start=ProjectTime.zero(),
        duration=ProjectTime.from_seconds(5),
    )
    project = _project((photo,), (clip,))

    middle = frame_at_project_time(project, ProjectTime.from_seconds(3))

    assert middle.target is not None
    assert middle.target.source_time == ProjectTime.zero()
    assert frame_at_project_time(
        Project.empty(project_id="empty"), ProjectTime.from_seconds(2)
    ).target is None


def test_preview_never_decodes_before_a_non_aligned_source_in() -> None:
    rate = FrameRate(30)
    media = _video("video", rate=rate)
    source_in = ProjectTime.from_milliseconds(1)
    clip = _video_clip(
        "video",
        "clip",
        start=ProjectTime.zero(),
        source_in=source_in,
        source_out=ProjectTime.from_seconds(2),
    )
    project = _project((media,), (clip,))

    target = frame_at_project_time(project, ProjectTime.zero()).target

    assert target is not None
    assert target.source_time >= source_in
    assert target.source_time == rate.time_at_frame(1)


def test_frame_steps_cross_clip_boundaries_without_fixed_30fps_increment() -> None:
    rate = FrameRate(24_000, 1_001)
    first_media = _video("first", rate=rate)
    second_media = _video("second", rate=rate)
    first = _video_clip(
        "first",
        "first-clip",
        start=ProjectTime.zero(),
        source_in=rate.time_at_frame(10),
        source_out=rate.time_at_frame(12),
    )
    second = _video_clip(
        "second",
        "second-clip",
        start=first.timeline_end,
        source_in=rate.time_at_frame(20),
        source_out=rate.time_at_frame(23),
    )
    project = _project((first_media, second_media), (first, second))

    expected_first_step = ProjectTime.from_seconds(
        (rate.time_at_frame(11) - rate.time_at_frame(10)).to_fractional_seconds()
    )
    assert step_project_frame(project, ProjectTime.zero(), 1) == expected_first_step
    assert step_project_frame(project, first.timeline_end, -1) < first.timeline_end
    assert step_project_frame(project, first.timeline_end, 1) > first.timeline_end
    assert step_project_frame(project, project.duration, 1) == project.duration


def test_playback_clock_uses_elapsed_monotonic_time_and_handles_end() -> None:
    media = _video("video", rate=FrameRate(30))
    clip = _video_clip(
        "video",
        "clip",
        start=ProjectTime.zero(),
        source_in=ProjectTime.zero(),
        source_out=ProjectTime.from_seconds(2),
    )
    project = _project((media,), (clip,))
    now = 1_000_000_000
    clock = PlaybackClock(lambda: now)

    assert clock.toggle(project)
    now += 375_000_000
    assert clock.advance(project) == ProjectTime(375_000_000)
    assert clock.toggle(project) is False
    assert clock.position == ProjectTime(375_000_000)
    clock.seek(project, ProjectTime.from_seconds(10))
    assert clock.position == project.duration
    assert clock.toggle(project)
    assert clock.position == ProjectTime.zero()
    assert clock.advance_elapsed(project, 2_500_000_000) == project.duration
    assert not clock.is_playing
