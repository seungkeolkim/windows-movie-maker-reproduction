from __future__ import annotations

import pytest

from movie_maker.creative import AddTextClip, UpdateTextProperties, UpdateTransition
from movie_maker.project import (
    Canvas,
    Clip,
    CommandExecutor,
    CommandRejected,
    FitMode,
    InvalidProjectDocument,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    NormalizedPosition,
    Project,
    ProjectTime,
    TextAnimationPreset,
    TextKind,
    TextOverlay,
    TextStyle,
    TimelineTrack,
    TrackKind,
    TransitionPreset,
    project_from_document,
    project_to_document,
)
from movie_maker.timeline import MoveVisualClipGroup


def _photo(asset_id: str) -> MediaReference:
    return MediaReference(
        asset_id,
        f"{asset_id}.png",
        f"C:/{asset_id}.png",
        MediaKind.PHOTO,
        None,
        640,
        480,
        0,
        (
            MediaStream(
                0,
                MediaStreamKind.VIDEO,
                "png",
                MediaTimeBase(1, 30),
            ),
        ),
    )


def _project() -> Project:
    first = _photo("first")
    second = _photo("second")
    return Project(
        1,
        "creative-project",
        "창작 프로젝트",
        Canvas(1280, 720, first.asset_id),
        (first, second),
        (
            TimelineTrack(
                TrackKind.VISUAL,
                (
                    Clip(
                        "first-clip",
                        TrackKind.VISUAL,
                        first.asset_id,
                        "첫 장면",
                        ProjectTime.zero(),
                        ProjectTime.from_seconds(4),
                    ),
                    Clip(
                        "second-clip",
                        TrackKind.VISUAL,
                        second.asset_id,
                        "둘째 장면",
                        ProjectTime.from_seconds(4),
                        ProjectTime.from_seconds(4),
                    ),
                ),
            ),
            TimelineTrack(TrackKind.MUSIC),
            TimelineTrack(TrackKind.NARRATION),
            TimelineTrack(TrackKind.TEXT),
        ),
    )


def test_text_fields_round_trip_and_old_documents_receive_safe_defaults() -> None:
    executor = CommandExecutor(_project())
    executor.execute(
        AddTextClip(
            "caption",
            TextKind.CAPTION,
            ProjectTime.from_seconds(2),
        )
    )
    text = TextOverlay(
        TextKind.CAPTION,
        "제주 야시장",
        TextStyle("Malgun Gothic", 42, False, "#FFFF00", "#000000"),
        NormalizedPosition(6_000, 8_000),
        TextAnimationPreset.FADE,
    )
    executor.execute(
        UpdateTextProperties(
            "caption",
            text,
            ProjectTime.from_seconds(2),
            ProjectTime.from_seconds(3),
        )
    )

    document = project_to_document(executor.project)
    loaded = project_from_document(document)

    assert loaded.clip("caption").text == text
    assert executor.undo().clip("caption").text != text
    assert executor.redo().clip("caption").text == text

    old_document = project_to_document(_project())
    old_document.pop("mixer")
    old_document.pop("transitions")
    for clip in old_document["timeline"]["visual"]:
        clip.pop("visual")
        clip.pop("fade_in_ns")
        clip.pop("fade_out_ns")
        clip.pop("ducking_preset")
    old_loaded = project_from_document(old_document)
    assert old_loaded.clip("first-clip").fit_mode is FitMode.FIT


def test_unknown_preset_is_rejected_instead_of_silently_substituted() -> None:
    document = project_to_document(_project())
    document["timeline"]["visual"][0]["visual"]["effect_preset"] = "python:run()"

    with pytest.raises(InvalidProjectDocument, match="python:run"):
        project_from_document(document)


def test_transition_requires_adjacency_and_move_prunes_then_undo_restores() -> None:
    executor = CommandExecutor(_project())
    executor.execute(
        UpdateTransition(
            "first-clip",
            "second-clip",
            TransitionPreset.DISSOLVE,
            ProjectTime.from_milliseconds(750),
        )
    )
    with_transition = executor.project

    executor.execute(MoveVisualClipGroup(("first-clip",), 1))
    assert executor.project.transitions == ()

    restored = executor.undo()
    assert restored == with_transition
    assert executor.redo().transitions == ()


def test_transition_duration_cannot_consume_half_of_either_clip() -> None:
    with pytest.raises(CommandRejected, match="half"):
        UpdateTransition(
            "first-clip",
            "second-clip",
            TransitionPreset.FADE,
            ProjectTime.from_seconds(3),
        ).apply(_project())


def test_empty_text_content_is_persisted_without_placeholder() -> None:
    executor = CommandExecutor(_project())
    executor.execute(AddTextClip("title", TextKind.TITLE))
    title = executor.project.clip("title")

    assert title.text is not None
    assert title.text.content == ""
    assert "텍스트를 입력하세요" not in str(project_to_document(executor.project))


def test_text_kind_default_placements_are_deterministic() -> None:
    executor = CommandExecutor(_project())
    playhead = ProjectTime.from_seconds(5)

    executor.execute(AddTextClip("title", TextKind.TITLE, playhead))
    executor.execute(AddTextClip("caption", TextKind.CAPTION, playhead))
    executor.execute(AddTextClip("credits", TextKind.CREDITS, playhead))

    title = executor.project.clip("title")
    caption = executor.project.clip("caption")
    credits = executor.project.clip("credits")
    assert title.timeline_start == ProjectTime.from_seconds(4)
    assert title.text is not None and title.text.position.y == 5_000
    assert caption.timeline_start == playhead
    assert caption.text is not None and caption.text.position.y == 8_500
    assert credits.timeline_end == executor.project.duration
    assert credits.text is not None
    assert credits.text.position.y == 9_000
    assert credits.text.animation is TextAnimationPreset.SCROLL_UP
