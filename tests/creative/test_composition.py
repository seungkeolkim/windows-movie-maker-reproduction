from __future__ import annotations

from dataclasses import replace

from movie_maker.creative.composition import (
    FontResolution,
    build_composition_plan,
    composition_video_filter,
    fit_geometry,
)
from movie_maker.project import (
    Canvas,
    Clip,
    FitMode,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    Project,
    ProjectTime,
    TextKind,
    TextOverlay,
    TimelineTrack,
    TrackKind,
    UserRotation,
)


def _project(content: str = "텍스트") -> Project:
    media = MediaReference(
        "photo",
        "photo.png",
        "C:/photo.png",
        MediaKind.PHOTO,
        None,
        1920,
        1080,
        0,
        (
            MediaStream(
                0,
                MediaStreamKind.VIDEO,
                "png",
                MediaTimeBase(1, 30),
                rotation_degrees=90,
            ),
        ),
    )
    visual = Clip(
        "visual",
        TrackKind.VISUAL,
        media.asset_id,
        "사진",
        ProjectTime.zero(),
        ProjectTime.from_seconds(4),
        fit_mode=FitMode.FILL,
        user_rotation=UserRotation.CLOCKWISE_90,
    )
    text = Clip(
        "text",
        TrackKind.TEXT,
        None,
        "캡션",
        ProjectTime.zero(),
        ProjectTime.from_seconds(2),
        text=TextOverlay(TextKind.CAPTION, content),
    )
    return Project(
        1,
        "composition",
        "합성",
        Canvas(1280, 720, media.asset_id),
        (media,),
        (
            TimelineTrack(TrackKind.VISUAL, (visual,)),
            TimelineTrack(TrackKind.MUSIC),
            TimelineTrack(TrackKind.NARRATION),
            TimelineTrack(TrackKind.TEXT, (text,)),
        ),
    )


def _font(family: str, bold: bool) -> FontResolution:
    assert bold
    return FontResolution(family, "Fallback", "C:/Fonts/fallback.ttf", True)


def test_fit_fill_geometry_and_metadata_then_user_rotation_are_deterministic() -> None:
    assert fit_geometry(1920, 1080, 1000, 1000, FitMode.FIT) == (1000, 562, 0, 219)
    assert fit_geometry(1920, 1080, 1000, 1000, FitMode.FILL) == (1778, 1000, -389, 0)

    plan = build_composition_plan(_project(), width=1280, height=720, font_resolver=_font)
    assert plan.sources[0].combined_rotation == 180
    assert plan.diagnostics and "Fallback" in plan.diagnostics[0]
    filter_graph = composition_video_filter(plan)
    assert "transpose=clock" in filter_graph
    assert "force_original_aspect_ratio=increase" in filter_graph
    assert "drawtext=" in filter_graph
    assert "shadowx=1" in filter_graph


def test_empty_text_is_not_composited_but_remains_in_project() -> None:
    project = _project("")
    plan = build_composition_plan(project, width=1280, height=720, font_resolver=_font)

    assert project.clip("text").text == TextOverlay(TextKind.CAPTION, "")
    assert plan.text_layers == ()
    assert "drawtext=" not in composition_video_filter(plan)


def test_project_edit_changes_composition_identity_for_stale_result_rejection() -> None:
    original = _project()
    edited_clip = replace(original.clip("visual"), fit_mode=FitMode.FIT)
    edited = replace(
        original,
        tracks=(
            TimelineTrack(TrackKind.VISUAL, (edited_clip,)),
            *original.tracks[1:],
        ),
    )

    first = build_composition_plan(original, width=1280, height=720, font_resolver=_font)
    second = build_composition_plan(edited, width=1280, height=720, font_resolver=_font)
    assert first != second
