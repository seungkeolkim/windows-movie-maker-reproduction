"""Shared visual composition planning for preview and MP4 export."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from movie_maker.audio import format_audio_time
from movie_maker.project import (
    FitMode,
    FrameRate,
    MediaKind,
    MediaStreamKind,
    PlaybackRate,
    Project,
    ProjectTime,
    TextAlignment,
    TextAnimationPreset,
    TextOverlay,
    TrackKind,
    Transition,
    UserRotation,
    VisualEffectPreset,
)

DEFAULT_COMPOSITION_FRAME_RATE = FrameRate(30, 1)
NEUTRAL_BACKGROUND = "0x20242b"
SAFE_AREA_FRACTION = 0.05


class CompositionError(ValueError):
    """Raised when persistent intent cannot form an executable visual plan."""


@dataclass(frozen=True, slots=True)
class FontResolution:
    """A runtime font choice and whether the requested family was substituted."""

    requested_family: str
    resolved_family: str
    font_file: str
    substituted: bool


@dataclass(frozen=True, slots=True)
class VisualSource:
    """One immutable visual input with W-09 presentation intent."""

    asset_id: str
    clip_id: str
    label: str
    source_path: str
    media_kind: MediaKind
    stream_index: int
    source_in: ProjectTime
    source_out: ProjectTime | None
    duration: ProjectTime
    playback_rate: PlaybackRate
    coded_width: int
    coded_height: int
    input_rotation: int
    fit_mode: FitMode
    user_rotation: UserRotation
    brightness_percent: int
    effect_preset: VisualEffectPreset

    @property
    def combined_rotation(self) -> int:
        """Return metadata rotation followed by the user edit."""

        return (self.input_rotation + int(self.user_rotation)) % 360


@dataclass(frozen=True, slots=True)
class TextLayer:
    """One non-empty generated overlay with a runtime-resolved font."""

    clip_id: str
    start: ProjectTime
    duration: ProjectTime
    overlay: TextOverlay
    font: FontResolution


@dataclass(frozen=True, slots=True)
class CompositionPlan:
    """Common, UI-independent visual meaning for preview and export."""

    project_id: str
    width: int
    height: int
    duration: ProjectTime
    frame_rate: FrameRate
    sources: tuple[VisualSource, ...]
    text_layers: tuple[TextLayer, ...]
    transitions: tuple[Transition, ...]
    diagnostics: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise CompositionError("Composition dimensions must be positive.")
        if self.duration.nanoseconds <= 0 or not self.sources:
            raise CompositionError("Composition requires a non-empty visual timeline.")


def _font_candidates(*, bold: bool) -> dict[str, tuple[str, ...]]:
    windows = os.environ.get("WINDIR", r"C:\Windows")
    return {
        "Malgun Gothic": (
            str(Path(windows) / "Fonts" / ("malgunbd.ttf" if bold else "malgun.ttf")),
            (
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
                if bold
                else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
            ),
            (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                if bold
                else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ),
        ),
        "Gulim": (
            str(Path(windows) / "Fonts" / "gulim.ttc"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ),
        "Batang": (
            str(Path(windows) / "Fonts" / "batang.ttc"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        ),
    }


def resolve_font(font_family: str, bold: bool = False) -> FontResolution:
    """Resolve a permitted family and report a deterministic fallback."""

    candidates = _font_candidates(bold=bold)
    requested = candidates.get(font_family, ())
    for candidate in requested:
        if Path(candidate).is_file():
            return FontResolution(font_family, font_family, candidate, False)
    for fallback_family in ("Malgun Gothic", "Gulim", "Batang"):
        for candidate in candidates[fallback_family]:
            if Path(candidate).is_file():
                return FontResolution(font_family, fallback_family, candidate, True)
    raise CompositionError(
        f"'{font_family}' 글꼴과 허용된 대체 글꼴을 찾을 수 없습니다. 글꼴을 설치하세요."
    )


def _video_stream(project: Project, asset_id: str) -> tuple[int, int]:
    media = project.media_reference(asset_id)
    stream = next(
        (
            item
            for item in media.streams
            if item.index == media.primary_stream_index
            and item.kind is MediaStreamKind.VIDEO
        ),
        None,
    )
    if stream is None:
        raise CompositionError(f"{media.name}에서 시각 스트림을 찾을 수 없습니다.")
    return stream.index, stream.rotation_degrees


def build_composition_plan(
    project: Project,
    *,
    width: int,
    height: int,
    frame_rate: FrameRate = DEFAULT_COMPOSITION_FRAME_RATE,
    font_resolver: Callable[[str, bool], FontResolution] = resolve_font,
) -> CompositionPlan:
    """Interpret one project snapshot into shared visual render values."""

    sources: list[VisualSource] = []
    for clip in project.track(TrackKind.VISUAL).clips:
        if clip.asset_id is None:
            raise CompositionError(f"{clip.label}의 원본 미디어 참조가 없습니다.")
        media = project.media_reference(clip.asset_id)
        stream_index, input_rotation = _video_stream(project, media.asset_id)
        assert media.width is not None and media.height is not None
        sources.append(
            VisualSource(
                asset_id=media.asset_id,
                clip_id=clip.clip_id,
                label=clip.label,
                source_path=media.source_path,
                media_kind=media.kind,
                stream_index=stream_index,
                source_in=clip.source_in,
                source_out=clip.source_out,
                duration=clip.duration,
                playback_rate=clip.playback_rate,
                coded_width=media.width,
                coded_height=media.height,
                input_rotation=input_rotation,
                fit_mode=clip.fit_mode,
                user_rotation=clip.user_rotation,
                brightness_percent=clip.brightness.percent,
                effect_preset=clip.effect_preset,
            )
        )
    if not sources:
        raise CompositionError("시각 타임라인이 비어 있습니다.")

    text_layers: list[TextLayer] = []
    diagnostics: list[str] = []
    for clip in project.track(TrackKind.TEXT).clips:
        assert clip.text is not None
        if not clip.text.content:
            continue
        font = font_resolver(clip.text.style.font_family, clip.text.style.bold)
        if font.substituted:
            diagnostics.append(
                f"{clip.label}: '{font.requested_family}' 대신 "
                f"'{font.resolved_family}' 글꼴을 사용합니다."
            )
        text_layers.append(
            TextLayer(clip.clip_id, clip.timeline_start, clip.duration, clip.text, font)
        )
    return CompositionPlan(
        project.project_id,
        width,
        height,
        project.duration,
        frame_rate,
        tuple(sources),
        tuple(text_layers),
        project.transitions,
        tuple(diagnostics),
    )


def oriented_source_size(source: VisualSource) -> tuple[int, int]:
    """Return display dimensions after metadata and user rotations."""

    if source.combined_rotation in {90, 270}:
        return source.coded_height, source.coded_width
    return source.coded_width, source.coded_height


def fit_geometry(
    source_width: int,
    source_height: int,
    canvas_width: int,
    canvas_height: int,
    mode: FitMode,
) -> tuple[int, int, int, int]:
    """Return scaled size and centered offset for fit or fill."""

    if min(source_width, source_height, canvas_width, canvas_height) <= 0:
        raise ValueError("Geometry dimensions must be positive.")
    if mode is FitMode.FIT:
        scale = min(canvas_width / source_width, canvas_height / source_height)
    elif mode is FitMode.FILL:
        scale = max(canvas_width / source_width, canvas_height / source_height)
    else:
        raise ValueError("Unknown visual fit mode.")
    width = max(1, round(source_width * scale))
    height = max(1, round(source_height * scale))
    return width, height, (canvas_width - width) // 2, (canvas_height - height) // 2


def _rate(rate: FrameRate) -> str:
    return f"{rate.numerator}/{rate.denominator}"


def _setpts(rate: PlaybackRate) -> str:
    return f"{rate.denominator}/{rate.numerator}"


def _escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace("'", r"'\''")
        .replace(":", r"\:")
        .replace("%", r"\%")
        .replace("\n", r"\n")
    )


def _visual_chain(plan: CompositionPlan, source: VisualSource, index: int) -> str:
    if source.media_kind is MediaKind.PHOTO:
        timing = f"trim=duration={format_audio_time(source.duration)}"
    else:
        if source.source_out is None:
            raise CompositionError(f"{source.label}의 원본 끝 위치가 없습니다.")
        timing = (
            f"trim=start={format_audio_time(source.source_in)}:"
            f"end={format_audio_time(source.source_out)},"
            f"setpts=(PTS-STARTPTS)*{_setpts(source.playback_rate)}"
        )
    filters = [
        f"[{index}:{source.stream_index}]{timing}",
        f"fps=fps={_rate(plan.frame_rate)}:start_time=0:round=near",
        "settb=AVTB",
    ]
    rotation = int(source.user_rotation)
    if rotation == 90:
        filters.append("transpose=clock")
    elif rotation == 180:
        filters.extend(("hflip", "vflip"))
    elif rotation == 270:
        filters.append("transpose=cclock")
    if source.fit_mode is FitMode.FIT:
        filters.extend(
            (
                (
                    f"scale={plan.width}:{plan.height}:"
                    "force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1"
                ),
                (
                    f"pad={plan.width}:{plan.height}:(ow-iw)/2:(oh-ih)/2:"
                    f"color={NEUTRAL_BACKGROUND}"
                ),
            )
        )
    elif source.fit_mode is FitMode.FILL:
        filters.extend(
            (
                (
                    f"scale={plan.width}:{plan.height}:"
                    "force_original_aspect_ratio=increase:force_divisible_by=2:reset_sar=1"
                ),
                f"crop={plan.width}:{plan.height}",
            )
        )
    else:
        raise CompositionError("알 수 없는 화면 배치 프리셋입니다.")
    if source.brightness_percent:
        filters.append(f"eq=brightness={source.brightness_percent / 100:.2f}")
    if source.effect_preset is VisualEffectPreset.WARM:
        filters.append("colorbalance=rs=.10:gs=.03:bs=-.06")
    elif source.effect_preset is VisualEffectPreset.MONOCHROME:
        filters.append("hue=s=0")
    elif source.effect_preset is VisualEffectPreset.VIVID:
        filters.append("eq=saturation=1.25:contrast=1.08")
    elif source.effect_preset is not VisualEffectPreset.NONE:
        raise CompositionError("알 수 없는 시각 효과 프리셋입니다.")
    outgoing = next(
        (item for item in plan.transitions if item.left_clip_id == source.clip_id),
        None,
    )
    if outgoing is not None:
        filters.append(
            "tpad=stop_mode=clone:stop_duration="
            f"{format_audio_time(outgoing.duration)}"
        )
    filters.extend(("setsar=1", "format=yuv444p"))
    return ",".join(filters) + f"[v{index}]"


_XFADE_NAME = {
    "fade": "fade",
    "dissolve": "dissolve",
    "wipe_left": "wipeleft",
}


def _text_position(layer: TextLayer) -> tuple[str, str]:
    position = layer.overlay.position
    x_anchor = f"w*{position.x}/10000"
    if layer.overlay.style.alignment is TextAlignment.CENTER:
        x_anchor = f"{x_anchor}-text_w/2"
    elif layer.overlay.style.alignment is TextAlignment.RIGHT:
        x_anchor = f"{x_anchor}-text_w"
    safe_x = f"w*{SAFE_AREA_FRACTION:g}"
    x = f"max({safe_x},min(w-text_w-{safe_x},{x_anchor}))"
    y = f"h*{position.y}/10000-text_h/2"
    if layer.overlay.animation is TextAnimationPreset.SCROLL_UP:
        start = format_audio_time(layer.start)
        duration = format_audio_time(layer.duration)
        y = f"h-(h+text_h)*(t-{start})/{duration}"
    safe_y = f"h*{SAFE_AREA_FRACTION:g}"
    if layer.overlay.animation is not TextAnimationPreset.SCROLL_UP:
        y = f"max({safe_y},min(h-text_h-{safe_y},{y}))"
    return x.replace(",", r"\,"), y.replace(",", r"\,")


def _text_alpha(layer: TextLayer) -> str | None:
    if layer.overlay.animation is not TextAnimationPreset.FADE:
        return None
    start = format_audio_time(layer.start)
    end = format_audio_time(layer.start + layer.duration)
    fade = min(ProjectTime.from_milliseconds(300), ProjectTime(layer.duration.nanoseconds // 2))
    fade_text = format_audio_time(fade)
    expression = (
        f"if(lt(t-{start},{fade_text}),(t-{start})/{fade_text},"
        f"if(gt(t,{end}-{fade_text}),({end}-t)/{fade_text},1))"
    )
    return expression.replace(",", r"\,")


def composition_video_filter(plan: CompositionPlan, *, output_label: str = "vout") -> str:
    """Build the common visual graph, including transition and text layers."""

    if not output_label.isalnum():
        raise ValueError("Output label must be alphanumeric.")
    filters = [_visual_chain(plan, source, index) for index, source in enumerate(plan.sources)]
    current = "v0"
    elapsed = plan.sources[0].duration
    for index, source in enumerate(plan.sources[1:], start=1):
        previous = plan.sources[index - 1]
        transition = next(
            (
                item
                for item in plan.transitions
                if item.boundary == (previous.clip_id, source.clip_id)
            ),
            None,
        )
        next_label = f"joined{index}"
        if transition is None:
            filters.append(f"[{current}][v{index}]concat=n=2:v=1:a=0[{next_label}]")
        else:
            preset = _XFADE_NAME.get(transition.preset.value)
            if preset is None:
                raise CompositionError("알 수 없는 전환 프리셋입니다.")
            filters.append(
                f"[{current}][v{index}]xfade=transition={preset}:"
                f"duration={format_audio_time(transition.duration)}:"
                f"offset={format_audio_time(elapsed)}[{next_label}]"
            )
        current = next_label
        elapsed += source.duration

    for index, layer in enumerate(plan.text_layers):
        x, y = _text_position(layer)
        style = layer.overlay.style
        font_size = max(12, round(style.size * plan.height / 1080))
        start = format_audio_time(layer.start)
        end = format_audio_time(layer.start + layer.duration)
        next_label = f"text{index}"
        drawtext = (
            f"[{current}]drawtext=fontfile='{_escape_drawtext(layer.font.font_file)}':"
            f"text='{_escape_drawtext(layer.overlay.content)}':fontsize={font_size}:"
            f"fontcolor={style.color}:borderw=2:bordercolor={style.outline_color}:"
            rf"x='{x}':y='{y}':enable='between(t\,{start}\,{end})'"
        )
        alpha = _text_alpha(layer)
        if alpha is not None:
            drawtext += f":alpha='{alpha}'"
        if style.bold:
            drawtext += f":shadowx=1:shadowcolor={style.color}"
        filters.append(f"{drawtext}[{next_label}]")
        current = next_label

    filters.append(
        f"[{current}]trim=duration={format_audio_time(plan.duration)},"
        f"setpts=PTS-STARTPTS[{output_label}]"
    )
    return ";".join(filters)
