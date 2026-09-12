from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from movie_maker.project import (
    CommandExecutor,
    MediaKind,
    MediaReference,
    Project,
    ProjectTime,
    TrackKind,
)
from movie_maker.timeline import (
    AddMediaClip,
    DeleteClipGroup,
    DuplicateTimelineClip,
    MoveAbsoluteClipGroup,
    MoveVisualClipGroup,
    TimelineEditErrorCode,
    TimelineEditRejected,
)


def _video(asset_id: str, seconds: int) -> MediaReference:
    return MediaReference(
        asset_id=asset_id,
        name=f"{asset_id}.mp4",
        source_path=f"D:/Media/{asset_id}.mp4",
        kind=MediaKind.VIDEO,
        duration=ProjectTime.from_seconds(seconds),
        width=1920,
        height=1080,
    )


def _audio() -> MediaReference:
    return MediaReference(
        asset_id="audio",
        name="audio.wav",
        source_path="D:/Media/audio.wav",
        kind=MediaKind.AUDIO,
        duration=ProjectTime.from_seconds(30),
    )


def _visual_executor(durations: tuple[int, ...] = (2, 3, 4, 5)) -> CommandExecutor:
    media = tuple(_video(f"video-{index}", seconds) for index, seconds in enumerate(durations))
    executor = CommandExecutor(replace(Project.empty(project_id="w08"), media=media))
    for index in range(len(durations)):
        executor.execute(AddMediaClip(f"video-{index}", f"clip-{index}"))
    return executor


def test_duplicate_preserves_persistent_properties_and_round_trips() -> None:
    executor = _visual_executor((7, 4))
    original_project = executor.project
    source = original_project.clip("clip-0")

    duplicated = executor.execute(DuplicateTimelineClip("clip-0", "clip-copy"))

    clips = duplicated.track(TrackKind.VISUAL).clips
    assert tuple(clip.clip_id for clip in clips) == ("clip-0", "clip-copy", "clip-1")
    copy = duplicated.clip("clip-copy")
    assert replace(
        copy,
        clip_id=source.clip_id,
        label=source.label,
        timeline_start=source.timeline_start,
    ) == source
    assert original_project.clip("clip-0") == source
    assert executor.undo() == original_project
    assert executor.redo() == duplicated


def test_duplicate_rejects_id_collision_without_history_change() -> None:
    executor = _visual_executor()
    before = executor.project
    position = executor.history_position

    with pytest.raises(TimelineEditRejected) as caught:
        executor.execute(DuplicateTimelineClip("clip-0", "clip-1"))

    assert caught.value.code is TimelineEditErrorCode.ID_COLLISION
    assert executor.project == before
    assert executor.history_position == position


def test_visual_group_move_preserves_order_reflows_and_leaves_auxiliary_time() -> None:
    executor = _visual_executor()
    project = replace(executor.project, media=(*executor.project.media, _audio()))
    executor = CommandExecutor(project)
    executor.execute(
        AddMediaClip("audio", "music", timeline_start=ProjectTime.from_seconds(6))
    )
    before = executor.project

    moved = executor.execute(MoveVisualClipGroup(("clip-1", "clip-2"), 2))

    visual = moved.track(TrackKind.VISUAL).clips
    assert tuple(clip.clip_id for clip in visual) == (
        "clip-0",
        "clip-3",
        "clip-1",
        "clip-2",
    )
    assert tuple(clip.timeline_start.to_milliseconds() for clip in visual) == (0, 2000, 7000, 10000)
    assert moved.clip("music").timeline_start == ProjectTime.from_seconds(6)
    assert moved.duration == before.duration
    assert executor.undo() == before
    assert executor.redo() == moved


def test_absolute_group_move_uses_one_delta_and_rejects_negative_result() -> None:
    project = replace(Project.empty(project_id="w08-audio"), media=(_audio(),))
    executor = CommandExecutor(project)
    executor.execute(
        AddMediaClip("audio", "music-1", timeline_start=ProjectTime.from_seconds(2))
    )
    executor.execute(
        AddMediaClip("audio", "music-2", timeline_start=ProjectTime.from_seconds(12))
    )
    before = executor.project

    moved = executor.execute(
        MoveAbsoluteClipGroup(
            ("music-2", "music-1"),
            ProjectTime.from_seconds(3),
        )
    )

    assert moved.clip("music-1").timeline_start == ProjectTime.from_seconds(5)
    assert moved.clip("music-2").timeline_start == ProjectTime.from_seconds(15)
    assert executor.undo() == before
    assert executor.redo() == moved

    position = executor.history_position
    with pytest.raises(TimelineEditRejected) as caught:
        executor.execute(
            MoveAbsoluteClipGroup(
                ("music-1", "music-2"),
                ProjectTime.from_seconds(-6),
            )
        )
    assert caught.value.code is TimelineEditErrorCode.OUT_OF_RANGE
    assert executor.project == moved
    assert executor.history_position == position


def test_group_delete_is_atomic_and_has_one_undo_entry() -> None:
    executor = _visual_executor()
    before = executor.project
    position = executor.history_position

    deleted = executor.execute(DeleteClipGroup(("clip-1", "clip-3")))

    assert tuple(clip.clip_id for clip in deleted.track(TrackKind.VISUAL).clips) == (
        "clip-0",
        "clip-2",
    )
    assert executor.history_position == position + 1
    assert executor.undo() == before
    assert executor.redo() == deleted

    position = executor.history_position
    with pytest.raises(TimelineEditRejected) as caught:
        executor.execute(DeleteClipGroup(("clip-0", "missing")))
    assert caught.value.code is TimelineEditErrorCode.CLIP_NOT_FOUND
    assert executor.project == deleted
    assert executor.history_position == position


def test_group_edit_after_undo_discards_redo_branch() -> None:
    executor = _visual_executor()
    executor.execute(MoveVisualClipGroup(("clip-1", "clip-2"), 2))
    executor.undo()
    assert executor.can_redo

    executor.execute(DeleteClipGroup(("clip-3",)))

    assert not executor.can_redo
    assert tuple(clip.clip_id for clip in executor.project.track(TrackKind.VISUAL).clips) == (
        "clip-0",
        "clip-1",
        "clip-2",
    )


@pytest.mark.parametrize(
    ("command", "code"),
    (
        (DeleteClipGroup(()), TimelineEditErrorCode.EMPTY_SELECTION),
        (
            MoveVisualClipGroup(("clip-0",), 99),
            TimelineEditErrorCode.OUT_OF_RANGE,
        ),
        (
            MoveAbsoluteClipGroup(("clip-0",), ProjectTime.from_seconds(1)),
            TimelineEditErrorCode.INCOMPATIBLE_SELECTION,
        ),
    ),
)
def test_advanced_commands_report_typed_boundary_errors(
    command: object,
    code: TimelineEditErrorCode,
) -> None:
    executor = _visual_executor()
    before = executor.project
    position = executor.history_position

    with pytest.raises(TimelineEditRejected) as caught:
        executor.execute(command)  # type: ignore[arg-type]

    assert caught.value.code is code
    assert executor.project == before
    assert executor.history_position == position


def test_mixed_track_group_is_rejected_as_a_whole() -> None:
    base = _visual_executor((2,)).project
    executor = CommandExecutor(replace(base, media=(*base.media, _audio())))
    executor.execute(AddMediaClip("audio", "music"))
    before = executor.project
    position = executor.history_position

    with pytest.raises(TimelineEditRejected) as caught:
        executor.execute(DeleteClipGroup(("clip-0", "music")))

    assert caught.value.code is TimelineEditErrorCode.MIXED_TRACKS
    assert executor.project == before
    assert executor.history_position == position


@given(
    durations=st.lists(st.integers(min_value=1, max_value=20), min_size=2, max_size=8),
    data=st.data(),
)
def test_arbitrary_visual_group_edits_keep_contiguous_unique_round_trip(
    durations: list[int],
    data: st.DataObject,
) -> None:
    executor = _visual_executor(tuple(durations))
    ids = tuple(f"clip-{index}" for index in range(len(durations)))
    selected = tuple(
        data.draw(
            st.lists(st.sampled_from(ids), min_size=1, max_size=len(ids), unique=True),
            label="selected",
        )
    )
    remaining_count = len(ids) - len(selected)
    target = data.draw(st.integers(min_value=0, max_value=remaining_count), label="target")
    before = executor.project

    try:
        moved = executor.execute(MoveVisualClipGroup(selected, target))
    except TimelineEditRejected as error:
        assert error.code is TimelineEditErrorCode.NO_CHANGE
        assert executor.project == before
        return

    visual = moved.track(TrackKind.VISUAL).clips
    assert len({clip.clip_id for clip in visual}) == len(visual)
    cursor = ProjectTime.zero()
    for clip in visual:
        assert clip.timeline_start == cursor
        cursor = clip.timeline_end
    assert executor.undo() == before
    assert executor.redo() == moved
