from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np

from movie_maker.creative import (
    AddTextClip,
    UpdateTextProperties,
    UpdateTransition,
    UpdateVisualProperties,
)
from movie_maker.exporting import (
    ExportPreset,
    ExportSucceeded,
    FfmpegExportRunner,
    build_export_plan,
)
from movie_maker.media import FfprobeAnalyzer, MediaAnalysisSuccess
from movie_maker.preview import DecodedFrame, FfmpegFrameDecoder, frame_at_project_time
from movie_maker.project import (
    Brightness,
    Canvas,
    CommandExecutor,
    FitMode,
    Project,
    ProjectTime,
    TextAnimationPreset,
    TextKind,
    TextOverlay,
    TransitionPreset,
    UserRotation,
    VisualEffectPreset,
)
from movie_maker.timeline import AddMediaClip, SetPhotoDuration


def _photo(path: Path, color: str, size: str) -> None:
    subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}",
            "-frames:v",
            "1",
            "-y",
            str(path),
        ),
        check=True,
        shell=False,
    )


def _rgb(source: Path, *, timestamp: str | None = None) -> np.ndarray:
    arguments = ["ffmpeg", "-v", "error"]
    if timestamp is not None:
        arguments.extend(("-ss", timestamp))
    arguments.extend(
        (
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        )
    )
    return np.frombuffer(
        subprocess.run(arguments, check=True, capture_output=True, shell=False).stdout,
        dtype=np.uint8,
    )


def test_preview_and_mp4_use_the_same_real_transition_text_and_effect_pixels(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "red.png"
    second_path = tmp_path / "blue.png"
    _photo(first_path, "red", "64x48")
    _photo(second_path, "blue", "80x60")
    analyzed = [FfprobeAnalyzer().analyze(path) for path in (first_path, second_path)]
    assert all(isinstance(result, MediaAnalysisSuccess) for result in analyzed)
    references = tuple(
        result.analysis.to_media_reference(f"media-{index}")
        for index, result in enumerate(analyzed)
        if isinstance(result, MediaAnalysisSuccess)
    )
    executor = CommandExecutor(
        replace(
            Project.empty(project_id="creative-real"),
            media=references,
            canvas=Canvas(1920, 1080, "media-0"),
        )
    )
    executor.execute(AddMediaClip("media-0", "first"))
    executor.execute(AddMediaClip("media-1", "second"))
    executor.execute(SetPhotoDuration("first", ProjectTime.from_seconds(1)))
    executor.execute(SetPhotoDuration("second", ProjectTime.from_seconds(1)))
    executor.execute(
        UpdateVisualProperties(
            "first",
            fit_mode=FitMode.FILL,
            user_rotation=UserRotation.CLOCKWISE_180,
            brightness=Brightness(10),
            effect_preset=VisualEffectPreset.WARM,
        )
    )
    executor.execute(
        AddTextClip(
            "caption",
            TextKind.CAPTION,
            ProjectTime.from_milliseconds(500),
            ProjectTime.from_seconds(1),
        )
    )
    executor.execute(
        UpdateTextProperties(
            "caption",
            TextOverlay(
                TextKind.CAPTION,
                    "TEST's: 100%",
                animation=TextAnimationPreset.FADE,
            ),
            ProjectTime.from_milliseconds(500),
            ProjectTime.from_seconds(1),
        )
    )
    executor.execute(
        UpdateTransition(
            "first",
            "second",
            TransitionPreset.FADE,
            ProjectTime.from_milliseconds(500),
        )
    )
    project = executor.project
    output = tmp_path / "creative.mp4"
    result = FfmpegExportRunner(minimum_timeout=30).run(
        build_export_plan(project, str(output), ExportPreset.HD_1080),
        Event(),
        lambda _progress: None,
    )
    assert isinstance(result, ExportSucceeded), result

    position = ProjectTime.from_milliseconds(1_250)
    target = frame_at_project_time(project, position).target
    assert target is not None
    decoded = FfmpegFrameDecoder(timeout=30).decode(target, Event())
    assert isinstance(decoded, DecodedFrame), decoded
    preview_png = tmp_path / "preview.png"
    preview_png.write_bytes(decoded.png_bytes)

    preview_pixels = _rgb(preview_png)
    export_pixels = _rgb(output, timestamp="1.25")
    assert preview_pixels.shape == export_pixels.shape
    difference = np.abs(
        preview_pixels.astype(np.int16) - export_pixels.astype(np.int16)
    )
    assert float(difference.mean()) < 8.0
