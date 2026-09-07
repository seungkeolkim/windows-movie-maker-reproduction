from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from movie_maker.project import (
    CommandExecutor,
    CommandRejected,
    FrameRate,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    PlaybackRate,
    Project,
    ProjectTime,
    TrackKind,
)
from movie_maker.timeline import (
    AddMediaClip,
    DeleteTimelineClip,
    MoveVisualClip,
    SetPhotoDuration,
    SetPlaybackRate,
    SplitClip,
    TrimClipEnd,
    TrimClipStart,
    UpdateClipTiming,
    snap_source_time,
)


def _video(
    asset_id: str = "video",
    *,
    duration_seconds: int = 20,
    frame_rate: FrameRate | None = None,
) -> MediaReference:
    frame_rate = frame_rate or FrameRate(30)
    return MediaReference(
        asset_id=asset_id,
        name=f"{asset_id}.mp4",
        source_path=f"D:/Media/{asset_id}.mp4",
        kind=MediaKind.VIDEO,
        duration=ProjectTime.from_seconds(duration_seconds),
        width=1920,
        height=1080,
        primary_stream_index=0,
        streams=(
            MediaStream(
                index=0,
                kind=MediaStreamKind.VIDEO,
                codec_name="h264",
                time_base=MediaTimeBase(1, 90_000),
                start_pts=-900,
                duration_ts=duration_seconds * 90_000,
                average_frame_rate=frame_rate,
            ),
        ),
    )


def _photo() -> MediaReference:
    return MediaReference(
        asset_id="photo",
        name="photo.jpg",
        source_path="D:/Media/photo.jpg",
        kind=MediaKind.PHOTO,
        duration=None,
        width=4032,
        height=3024,
        primary_stream_index=0,
        streams=(
            MediaStream(
                index=0,
                kind=MediaStreamKind.VIDEO,
                codec_name="mjpeg",
                time_base=MediaTimeBase(1, 25),
                average_frame_rate=FrameRate(25),
            ),
        ),
    )


def _audio(sample_rate: int = 48_000) -> MediaReference:
    return MediaReference(
        asset_id="audio",
        name="audio.wav",
        source_path="D:/Media/audio.wav",
        kind=MediaKind.AUDIO,
        duration=ProjectTime.from_seconds(30),
        primary_stream_index=1,
        streams=(
            MediaStream(
                index=1,
                kind=MediaStreamKind.AUDIO,
                codec_name="pcm_s16le",
                time_base=MediaTimeBase(1, sample_rate),
                start_pts=240,
                duration_ts=30 * sample_rate,
                sample_rate=sample_rate,
            ),
        ),
    )


def _project(*media: MediaReference) -> Project:
    return replace(Project.empty(project_id="project-w04"), media=media)


def _execute_round_trip(executor: CommandExecutor, command: object) -> Project:
    before = executor.project
    edited = executor.execute(command)  # type: ignore[arg-type]
    assert executor.undo() == before
    assert executor.redo() == edited
    return edited


def test_add_routes_media_to_fixed_tracks_and_reflows_visual_starts() -> None:
    executor = CommandExecutor(_project(_video(), _photo(), _audio()))

    executor.execute(AddMediaClip("video", "clip-video"))
    executor.execute(AddMediaClip("photo", "clip-photo"))
    executor.execute(AddMediaClip("audio", "clip-music"))
    result = executor.execute(AddMediaClip("audio", "clip-narration", narration=True))

    visual = result.track(TrackKind.VISUAL).clips
    assert tuple(clip.clip_id for clip in visual) == ("clip-video", "clip-photo")
    assert tuple(clip.timeline_start for clip in visual) == (
        ProjectTime.zero(),
        ProjectTime.from_seconds(20),
    )
    assert visual[1].source_out is None
    assert visual[1].playback_rate == PlaybackRate(1, 1)
    assert result.track(TrackKind.MUSIC).clips[0].clip_id == "clip-music"
    assert result.track(TrackKind.NARRATION).clips[0].clip_id == "clip-narration"
    assert result.canvas.reference_asset_id == "video"
    assert (result.canvas.width, result.canvas.height) == (1920, 1080)


def test_add_command_undo_restores_empty_timeline_and_canvas() -> None:
    executor = CommandExecutor(_project(_video()))

    edited = _execute_round_trip(executor, AddMediaClip("video", "clip-video"))

    assert edited.duration == ProjectTime.from_seconds(20)
    assert edited.canvas.reference_asset_id == "video"


def test_visual_move_and_delete_keep_ids_references_and_auxiliary_absolute_time() -> None:
    executor = CommandExecutor(_project(_video("one", duration_seconds=4), _video("two", duration_seconds=6), _photo(), _audio()))
    executor.execute(AddMediaClip("one", "clip-one"))
    executor.execute(AddMediaClip("two", "clip-two"))
    executor.execute(AddMediaClip("photo", "clip-photo"))
    executor.execute(
        AddMediaClip(
            "audio",
            "clip-music",
            timeline_start=ProjectTime.from_seconds(7),
        )
    )
    before_move = executor.project

    moved = executor.execute(MoveVisualClip("clip-photo", 0))

    visual = moved.track(TrackKind.VISUAL).clips
    assert tuple((clip.clip_id, clip.asset_id) for clip in visual) == (
        ("clip-photo", "photo"),
        ("clip-one", "one"),
        ("clip-two", "two"),
    )
    assert tuple(clip.timeline_start.nanoseconds for clip in visual) == (
        0,
        5_000_000_000,
        9_000_000_000,
    )
    assert moved.track(TrackKind.MUSIC).clips[0].timeline_start == ProjectTime.from_seconds(7)
    assert executor.undo() == before_move
    assert executor.redo() == moved

    deleted = executor.execute(DeleteTimelineClip("clip-one"))
    assert tuple(clip.clip_id for clip in deleted.track(TrackKind.VISUAL).clips) == (
        "clip-photo",
        "clip-two",
    )
    assert deleted.track(TrackKind.VISUAL).clips[1].timeline_start == ProjectTime.from_seconds(5)
    assert deleted.track(TrackKind.MUSIC).clips[0].timeline_start == ProjectTime.from_seconds(7)
    assert deleted.media_reference("one").name == "one.mp4"


def test_video_split_snaps_to_frame_and_round_trips_exactly() -> None:
    executor = CommandExecutor(_project(_video()))
    executor.execute(AddMediaClip("video", "clip-video"))
    before = executor.project

    result = executor.execute(
        SplitClip(
            "clip-video",
            ProjectTime.from_milliseconds(4_010),
            "clip-back",
        )
    )

    front, back = result.track(TrackKind.VISUAL).clips
    assert front.clip_id == "clip-video"
    assert back.clip_id == "clip-back"
    assert front.source_out == ProjectTime.from_seconds(4)
    assert back.source_in == front.source_out
    assert back.timeline_start == front.timeline_end
    assert executor.undo() == before
    assert executor.redo() == result


def test_audio_split_uses_sample_boundary_and_keeps_absolute_track_position() -> None:
    executor = CommandExecutor(_project(_audio()))
    executor.execute(
        AddMediaClip(
            "audio",
            "clip-audio",
            timeline_start=ProjectTime.from_seconds(2),
        )
    )
    requested = ProjectTime(2_000_000_000 + 3_000_010_000)

    result = executor.execute(SplitClip("clip-audio", requested, "clip-audio-back"))

    front, back = result.track(TrackKind.MUSIC).clips
    assert front.timeline_start == ProjectTime.from_seconds(2)
    assert front.source_out == ProjectTime(3_000_000_000)
    assert back.timeline_start == ProjectTime.from_seconds(5)
    assert back.source_in == front.source_out


def test_split_rejects_photo_edges_short_results_and_duplicate_ids() -> None:
    executor = CommandExecutor(_project(_video(), _photo()))
    executor.execute(AddMediaClip("video", "clip-video"))
    executor.execute(AddMediaClip("photo", "clip-photo"))
    before = executor.project
    history = executor.history

    with pytest.raises(CommandRejected, match="0.25초"):
        executor.execute(
            SplitClip("clip-video", ProjectTime.from_milliseconds(100), "short-back")
        )
    with pytest.raises(CommandRejected, match="사진"):
        executor.execute(
            SplitClip(
                "clip-photo",
                ProjectTime.from_seconds(21),
                "photo-back",
            )
        )
    with pytest.raises(CommandRejected, match="이미 사용"):
        executor.execute(
            SplitClip("clip-video", ProjectTime.from_seconds(4), "clip-photo")
        )

    assert executor.project is before
    assert executor.history == history


def test_trim_start_end_and_combined_timing_apply_are_atomic() -> None:
    executor = CommandExecutor(_project(_video()))
    executor.execute(AddMediaClip("video", "clip-video"))

    start_trimmed = _execute_round_trip(
        executor,
        TrimClipStart("clip-video", ProjectTime.from_milliseconds(1_010)),
    )
    assert start_trimmed.clip("clip-video").source_in == ProjectTime.from_seconds(1)

    end_trimmed = executor.execute(
        TrimClipEnd("clip-video", ProjectTime.from_milliseconds(7_010))
    )
    assert end_trimmed.clip("clip-video").source_out == ProjectTime.from_seconds(7)

    before_count = executor.history_count
    combined = executor.execute(
        UpdateClipTiming(
            "clip-video",
            source_in=ProjectTime.from_seconds(2),
            source_out=ProjectTime.from_seconds(6),
            playback_rate=PlaybackRate(3, 2),
        )
    )
    clip = combined.clip("clip-video")
    assert clip.source_in == ProjectTime.from_seconds(2)
    assert clip.source_out == ProjectTime.from_seconds(6)
    assert clip.playback_rate == PlaybackRate(3, 2)
    assert clip.duration == ProjectTime(2_666_666_667)
    assert executor.history_count == before_count + 1
    assert executor.undo_label == "클립 속성 적용"


def test_trim_rejects_negative_reversed_too_short_and_source_overflow() -> None:
    executor = CommandExecutor(_project(_video()))
    executor.execute(AddMediaClip("video", "clip-video"))
    before = executor.project
    history = executor.history

    rejected = (
        TrimClipStart("clip-video", ProjectTime(-1)),
        TrimClipStart("clip-video", ProjectTime.from_seconds(20)),
        TrimClipEnd("clip-video", ProjectTime.from_seconds(21)),
        UpdateClipTiming(
            "clip-video",
            source_in=ProjectTime.from_milliseconds(19_800),
        ),
    )
    for command in rejected:
        with pytest.raises(CommandRejected):
            executor.execute(command)

    assert executor.project is before
    assert executor.history == history


def test_photo_duration_and_video_fractional_rate_reflow_visual_track() -> None:
    executor = CommandExecutor(_project(_video(), _photo()))
    executor.execute(AddMediaClip("photo", "clip-photo"))
    executor.execute(AddMediaClip("video", "clip-video"))

    photo_changed = executor.execute(
        SetPhotoDuration("clip-photo", ProjectTime.from_seconds(9))
    )
    assert photo_changed.clip("clip-photo").duration == ProjectTime.from_seconds(9)
    assert photo_changed.clip("clip-video").timeline_start == ProjectTime.from_seconds(9)

    speed_changed = _execute_round_trip(
        executor,
        SetPlaybackRate("clip-video", PlaybackRate(24_000, 1_001)),
    )
    expected = ProjectTime.from_seconds(
        Fraction(20, 1) / PlaybackRate(24_000, 1_001).fraction
    )
    assert speed_changed.clip("clip-video").duration == expected

    with pytest.raises(CommandRejected, match="1초에서 30초"):
        executor.execute(SetPhotoDuration("clip-photo", ProjectTime.zero()))
    with pytest.raises(CommandRejected, match="사진"):
        executor.execute(SetPlaybackRate("clip-photo", PlaybackRate(2, 1)))


@pytest.mark.parametrize(
    "rate",
    (FrameRate(24_000, 1_001), FrameRate(30_000, 1_001), FrameRate(60_000, 1_001)),
)
def test_long_fractional_frame_boundaries_are_computed_from_absolute_index(
    rate: FrameRate,
) -> None:
    media = _video(duration_seconds=100_000, frame_rate=rate)
    boundary = rate.time_at_frame(1_000_000)

    assert snap_source_time(media, boundary) == boundary
    assert snap_source_time(media, ProjectTime(boundary.nanoseconds + 1)) == boundary


def test_audio_sample_boundaries_use_absolute_sample_index() -> None:
    media = _audio(44_100)
    sample_index = 1_234_567
    boundary = ProjectTime.from_seconds(Fraction(sample_index, 44_100))

    assert snap_source_time(media, boundary) == boundary
    midpoint_or_later = ProjectTime(boundary.nanoseconds + 1)
    assert snap_source_time(media, midpoint_or_later) == boundary


def test_history_order_labels_and_new_branch_after_undo() -> None:
    executor = CommandExecutor(_project(_video(), _photo()))
    executor.execute(AddMediaClip("video", "clip-video"))
    executor.execute(AddMediaClip("photo", "clip-photo"))
    executor.execute(MoveVisualClip("clip-photo", 0, history_label="클립 앞으로 이동"))
    executor.execute(SetPhotoDuration("clip-photo", ProjectTime.from_seconds(6)))

    assert tuple(entry.label for entry in executor.history[-2:]) == (
        "클립 앞으로 이동",
        "사진 표시 시간 변경",
    )
    executor.undo()
    executor.execute(DeleteTimelineClip("clip-video"))

    assert not executor.can_redo
    assert executor.history_position == executor.history_count
    assert executor.undo_label == "클립 삭제"


def test_invalid_reference_and_track_commands_leave_executor_unchanged() -> None:
    executor = CommandExecutor(_project(_video(), _audio()))
    executor.execute(AddMediaClip("audio", "clip-audio"))
    before = executor.project
    history = executor.history

    with pytest.raises(CommandRejected, match="찾을 수 없습니다"):
        executor.execute(AddMediaClip("missing", "clip-missing"))
    with pytest.raises(CommandRejected, match="영상 또는 사진"):
        executor.execute(MoveVisualClip("clip-audio", 0))

    assert executor.project is before
    assert executor.history == history
