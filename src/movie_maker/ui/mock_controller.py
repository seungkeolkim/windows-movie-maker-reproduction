"""In-memory command boundary for the interactive product mock-up."""

from __future__ import annotations

import wave
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from itertools import count
from pathlib import Path
from threading import Event
from time import monotonic_ns
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from movie_maker.creative import (
    AddRecordedNarration,
    AddTextClip,
    UpdateMixerSettings,
    UpdateTextProperties,
    UpdateTransition,
    UpdateVisualProperties,
)
from movie_maker.media import (
    CacheKind,
    FfprobeAnalyzer,
    ImportedMedia,
    JobHandle,
    JobPriority,
    JobState,
    MediaAnalysis,
    MediaAnalysisErrorCode,
    MediaAnalysisFailure,
    MediaAnalysisResult,
    MediaImportReport,
    MediaLibrary,
    MediaRelinker,
    MediaRemovalFailure,
    RelinkComparison,
    RelinkErrorCode,
    RelinkFailure,
    RelinkMatch,
    RelinkSuccess,
)
from movie_maker.media.process import run_cancellable_process
from movie_maker.preview import PlaybackClock, ui_milliseconds
from movie_maker.project import (
    AudioLevel,
    AutosaveState,
    Brightness,
    Canvas,
    Clip,
    CommandError,
    DuckingPreset,
    FitMode,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    MixerSettings,
    NormalizedPosition,
    PlaybackRate,
    Project,
    ProjectCommand,
    ProjectFileStore,
    ProjectPersistenceError,
    ProjectTime,
    RecentProject,
    RecoveryCandidate,
    RecoveryChoice,
    RenameProject,
    TextAlignment,
    TextAnimationPreset,
    TextKind,
    TextOverlay,
    TextStyle,
    TimelineTrack,
    Transition,
    TransitionPreset,
    UserRotation,
    VisualEffectPreset,
)
from movie_maker.project.model import MediaKind as CoreMediaKind
from movie_maker.project.model import TrackKind as CoreTrackKind
from movie_maker.runtime import W10Runtime
from movie_maker.timeline import (
    AddMediaClip,
    DeleteClipGroup,
    DuplicateTimelineClip,
    MoveAbsoluteClipGroup,
    MoveVisualClipGroup,
    SplitClip,
    UpdateClipTiming,
)
from movie_maker.timeline.session import (
    TimelineSelection,
    TimelineViewMode,
    normalise_timeline_zoom,
)
from movie_maker.ui.mock_model import (
    AssetStatus,
    ExportState,
    MediaKind,
    MockAsset,
    MockClip,
    MockHistoryEntry,
    MockProjectState,
    TrackKind,
)

CORE_TO_UI_MEDIA_KIND = {
    CoreMediaKind.VIDEO: MediaKind.VIDEO,
    CoreMediaKind.PHOTO: MediaKind.PHOTO,
    CoreMediaKind.AUDIO: MediaKind.AUDIO,
}
UI_TO_CORE_MEDIA_KIND = {value: key for key, value in CORE_TO_UI_MEDIA_KIND.items()}
MEDIA_COLORS = {
    CoreMediaKind.VIDEO: "#2f79b9",
    CoreMediaKind.PHOTO: "#4d8a66",
    CoreMediaKind.AUDIO: "#8563b8",
}
CORE_TO_UI_TRACK_KIND = {
    CoreTrackKind.VISUAL: TrackKind.VISUAL,
    CoreTrackKind.MUSIC: TrackKind.MUSIC,
    CoreTrackKind.NARRATION: TrackKind.NARRATION,
    CoreTrackKind.TEXT: TrackKind.TEXT,
}
UI_TO_CORE_TRACK_KIND = {value: key for key, value in CORE_TO_UI_TRACK_KIND.items()}
FIT_TO_CORE = {"맞춤": FitMode.FIT, "채움": FitMode.FILL}
CORE_TO_FIT = {value: key for key, value in FIT_TO_CORE.items()}
EFFECT_TO_CORE = {
    "없음": VisualEffectPreset.NONE,
    "따뜻하게": VisualEffectPreset.WARM,
    "흑백": VisualEffectPreset.MONOCHROME,
    "선명하게": VisualEffectPreset.VIVID,
    "밝게": VisualEffectPreset.VIVID,
}
CORE_TO_EFFECT = {
    VisualEffectPreset.NONE: "없음",
    VisualEffectPreset.WARM: "따뜻하게",
    VisualEffectPreset.MONOCHROME: "흑백",
    VisualEffectPreset.VIVID: "선명하게",
}
DUCKING_TO_CORE = {
    "꺼짐": DuckingPreset.OFF,
    "약하게": DuckingPreset.LIGHT,
    "보통": DuckingPreset.MEDIUM,
    "강하게": DuckingPreset.STRONG,
}
CORE_TO_DUCKING = {value: key for key, value in DUCKING_TO_CORE.items()}
TEXT_KIND_TO_CORE = {
    "제목": TextKind.TITLE,
    "캡션": TextKind.CAPTION,
    "크레딧": TextKind.CREDITS,
}
CORE_TO_TEXT_KIND = {value: key for key, value in TEXT_KIND_TO_CORE.items()}
ALIGNMENT_TO_CORE = {
    "왼쪽": TextAlignment.LEFT,
    "가운데": TextAlignment.CENTER,
    "오른쪽": TextAlignment.RIGHT,
}
CORE_TO_ALIGNMENT = {value: key for key, value in ALIGNMENT_TO_CORE.items()}
ANIMATION_TO_CORE = {
    "없음": TextAnimationPreset.NONE,
    "페이드": TextAnimationPreset.FADE,
    "위로 흐르기": TextAnimationPreset.SCROLL_UP,
}
CORE_TO_ANIMATION = {value: key for key, value in ANIMATION_TO_CORE.items()}
POSITION_Y = {"위": 1_500, "가운데": 5_000, "아래": 8_500}
COLOR_TO_HEX = {"흰색": "#FFFFFF", "검정": "#000000", "노랑": "#FFFF00", "하늘색": "#66CCFF"}
HEX_TO_COLOR = {value: key for key, value in COLOR_TO_HEX.items()}
FONT_TO_CORE = {"맑은 고딕": "Malgun Gothic", "굴림": "Gulim", "바탕": "Batang"}
CORE_TO_FONT = {value: key for key, value in FONT_TO_CORE.items()}
TRANSITION_TO_CORE = {
    "페이드": TransitionPreset.FADE,
    "디졸브": TransitionPreset.DISSOLVE,
    "닦아내기": TransitionPreset.WIPE_LEFT,
}
CORE_TO_TRANSITION = {value: key for key, value in TRANSITION_TO_CORE.items()}


@dataclass(slots=True)
class _ControllerHistoryEntry:
    label: str
    core: bool
    mock: MockHistoryEntry | None = None


def _sample_assets() -> dict[str, MockAsset]:
    """Return deterministic media used by every mock-up session."""

    assets = (
        MockAsset(
            "media-beach",
            "해변 산책.mp4",
            MediaKind.VIDEO,
            12_000,
            1920,
            1080,
            "#2f79b9",
            r"C:\MockMedia\해변 산책.mp4",
        ),
        MockAsset(
            "media-market",
            "야시장 세로영상.mp4",
            MediaKind.VIDEO,
            9_000,
            1080,
            1920,
            "#c75d3a",
            r"C:\MockMedia\야시장 세로영상.mp4",
        ),
        MockAsset(
            "media-photo",
            "한라산 사진.jpg",
            MediaKind.PHOTO,
            None,
            4032,
            3024,
            "#4d8a66",
            r"C:\MockMedia\한라산 사진.jpg",
        ),
        MockAsset(
            "media-music",
            "여행 배경음악.mp3",
            MediaKind.AUDIO,
            30_000,
            None,
            None,
            "#8563b8",
            r"C:\MockMedia\여행 배경음악.mp3",
        ),
        MockAsset(
            "media-narration",
            "장소 설명.wav",
            MediaKind.AUDIO,
            8_000,
            None,
            None,
            "#b17635",
            r"C:\MockMedia\장소 설명.wav",
        ),
    )
    return {asset.asset_id: asset for asset in assets}


def _source_name(source_path: str) -> str:
    return Path(source_path).name or source_path


def _stream_summary(reference: MediaReference) -> str:
    parts = []
    for stream in reference.streams:
        detail = f"{stream.kind.value} · {stream.codec_name or '코덱 미상'}"
        if stream.sample_rate is not None:
            detail += f" · {stream.sample_rate} Hz"
        parts.append(detail)
    return ", ".join(parts) or "스트림 정보 없음"


def _import_warning_text(report: MediaImportReport) -> str | None:
    lines: list[str] = []
    for import_failure in report.failures:
        lines.append(
            f"실패 · {_source_name(import_failure.source_path)}: {import_failure.message}"
        )
    for duplicate in report.duplicates:
        lines.append(f"중복 · {_source_name(duplicate.source_path)}: {duplicate.message}")
    for thumbnail_failure in report.thumbnail_failures:
        lines.append(
            "썸네일 · "
            f"{_source_name(thumbnail_failure.source_path)}: {thumbnail_failure.message}"
        )
    return "\n".join(lines) or None


def _text_overlay_from_mock(clip: MockClip) -> TextOverlay:
    kind = TEXT_KIND_TO_CORE.get(clip.text_kind or "캡션", TextKind.CAPTION)
    return TextOverlay(
        kind=kind,
        content=clip.text_content,
        style=TextStyle(
            font_family=FONT_TO_CORE.get(clip.text_font, "Malgun Gothic"),
            size=clip.text_size,
            bold=clip.text_bold,
            color=COLOR_TO_HEX.get(clip.text_color, "#FFFFFF"),
            outline_color="#000000" if clip.text_color != "검정" else "#FFFFFF",
            alignment=ALIGNMENT_TO_CORE.get(
                clip.text_alignment, TextAlignment.CENTER
            ),
        ),
        position=NormalizedPosition(5_000, POSITION_Y.get(clip.text_position, 8_500)),
        animation=ANIMATION_TO_CORE.get(
            clip.text_animation, TextAnimationPreset.NONE
        ),
    )


def _transition_from_mock(boundary: str, value: str) -> Transition | None:
    parts = boundary.split("|", maxsplit=1)
    if len(parts) != 2:
        return None
    name, _, duration_text = value.partition(" · ")
    preset = TRANSITION_TO_CORE.get(name)
    if preset is None:
        return None
    try:
        seconds = Decimal(duration_text.removesuffix("초"))
    except InvalidOperation:
        return None
    return Transition(parts[0], parts[1], preset, ProjectTime.from_seconds(seconds))


def _position_name(y: int) -> str:
    return min(POSITION_Y, key=lambda name: abs(POSITION_Y[name] - y))


def _sync_creative_values(projected: MockClip, clip: Clip) -> None:
    projected.fade_in_ms = clip.fade_in.to_milliseconds()
    projected.fade_out_ms = clip.fade_out.to_milliseconds()
    projected.ducking = CORE_TO_DUCKING[clip.ducking]
    projected.fit_mode = CORE_TO_FIT[clip.fit_mode]
    projected.rotation = int(clip.user_rotation)
    projected.brightness = clip.brightness.percent
    projected.effect = CORE_TO_EFFECT[clip.effect_preset]
    if clip.text is None:
        return
    projected.text_kind = CORE_TO_TEXT_KIND[clip.text.kind]
    projected.text_content = clip.text.content
    projected.text_font = CORE_TO_FONT.get(
        clip.text.style.font_family, clip.text.style.font_family
    )
    projected.text_size = clip.text.style.size
    projected.text_bold = clip.text.style.bold
    projected.text_color = HEX_TO_COLOR.get(clip.text.style.color, "흰색")
    projected.text_alignment = CORE_TO_ALIGNMENT[clip.text.style.alignment]
    projected.text_position = _position_name(clip.text.position.y)
    projected.text_animation = CORE_TO_ANIMATION[clip.text.animation]


class MockController(QObject):
    """Own mock state and expose user intents independently of Qt widgets."""

    state_changed = Signal()
    status_changed = Signal(str)
    export_changed = Signal()
    relink_confirmation_requested = Signal(str, object)

    def __init__(
        self,
        media_library: MediaLibrary | None = None,
        project_store: ProjectFileStore | None = None,
        *,
        runtime: W10Runtime | None = None,
        clip_id_factory: Callable[[], str] | None = None,
        playback_clock_ns: Callable[[], int] = monotonic_ns,
    ) -> None:
        super().__init__()
        self.state = MockProjectState()
        self._media_library = media_library or MediaLibrary.create_default()
        self._project_store = project_store or ProjectFileStore()
        self._runtime = runtime
        self._recovery_candidates = list(runtime.recovery_candidates) if runtime else []
        self._background_handles: dict[tuple[str, CacheKind], JobHandle] = {}
        self._protected_proxy_paths: dict[str, Path] = {}
        self._pending_import: Future[tuple[MediaAnalysisResult, ...]] | None = None
        self._import_cancel = Event()
        self._pending_relink: Future[tuple[str, str, object]] | None = None
        self._relink_cancel = Event()
        self._pending_relink_confirmation: (
            tuple[str, MediaAnalysis, RelinkComparison] | None
        ) = None
        self.last_relink_result: RelinkSuccess | RelinkFailure | None = None
        self.last_persistence_error: str | None = None
        self._history: list[_ControllerHistoryEntry] = []
        self._history_position = 0
        self._id_counter = count(1)
        self._clip_id_factory = clip_id_factory
        self._playback_clock_ns = playback_clock_ns
        self._playback = PlaybackClock(playback_clock_ns)
        self._saved_project = self._media_library.project
        self._last_autosave_project = self._media_library.project
        self._sync_from_core(self._media_library.project)

    @property
    def history_position(self) -> int:
        return self._history_position

    @property
    def history_count(self) -> int:
        return len(self._history)

    @property
    def media_project(self) -> Project:
        """Return the W-01 project currently backing real W-02 library items."""

        return self._media_library.project

    @property
    def preview_project(self) -> Project:
        """Return a transient preview snapshot that may substitute verified proxies."""

        project = self._media_library.project
        replacements = {
            asset_id: asset.proxy_path
            for asset_id, asset in self.state.assets.items()
            if asset.proxy_enabled and asset.proxy_path is not None
        }
        if not replacements:
            return project
        return replace(
            project,
            media=tuple(
                replace(media, source_path=replacements[media.asset_id])
                if media.asset_id in replacements
                else media
                for media in project.media
            ),
        )

    @property
    def media_history_count(self) -> int:
        """Return successful real media commands in this project session."""

        return self._media_library.history_count

    @property
    def pending_recovery(self) -> RecoveryCandidate | None:
        return self._recovery_candidates[0] if self._recovery_candidates else None

    @property
    def recent_projects(self) -> tuple[RecentProject, ...]:
        return self._runtime.recent_projects.load() if self._runtime is not None else ()

    def remove_recent_project(self, path: str) -> None:
        if self._runtime is None:
            return
        try:
            self._runtime.recent_projects.remove(path)
        except OSError:
            self._set_status("최근 프로젝트 목록을 정리하지 못했습니다")
            return
        self._set_status("최근 프로젝트 목록에서 제거했습니다")

    @property
    def preview_position(self) -> ProjectTime:
        """Return the exact non-persistent project playback position."""

        return self._playback.position

    @property
    def can_undo(self) -> bool:
        return self._history_position > 0

    @property
    def can_redo(self) -> bool:
        return self._history_position < len(self._history)

    @property
    def undo_label(self) -> str | None:
        if not self.can_undo:
            return None
        return self._history[self._history_position - 1].label

    @property
    def redo_label(self) -> str | None:
        if not self.can_redo:
            return None
        return self._history[self._history_position].label

    @property
    def selected_asset(self) -> MockAsset | None:
        selected_id = self.state.selected_asset_id
        if selected_id is None:
            return None
        return self.state.assets.get(selected_id)

    @property
    def selected_clip(self) -> MockClip | None:
        selected_id = self.state.selected_clip_id
        if selected_id is None:
            return None
        return next(
            (clip for clip in self.state.all_clips if clip.clip_id == selected_id),
            None,
        )

    @property
    def selected_clips(self) -> list[MockClip]:
        selected_ids = set(self.state.selected_clip_ids)
        return [clip for clip in self.state.all_clips if clip.clip_id in selected_ids]

    def asset_for_clip(self, clip: MockClip) -> MockAsset | None:
        if clip.asset_id is None:
            return None
        return self.state.assets.get(clip.asset_id)

    def clips_for_track(self, track: TrackKind) -> list[MockClip]:
        if track is TrackKind.VISUAL:
            return self.state.visual_clips
        if track is TrackKind.MUSIC:
            return self.state.music_clips
        if track is TrackKind.NARRATION:
            return self.state.narration_clips
        return self.state.text_clips

    def _next_id(self, prefix: str) -> str:
        existing = set(self.state.assets)
        existing.update(clip.clip_id for clip in self.state.all_clips)
        while True:
            candidate = f"{prefix}-{next(self._id_counter):04d}"
            if candidate not in existing:
                return candidate

    def _next_clip_id(self) -> str:
        if self._clip_id_factory is None:
            return self._next_id("clip")
        candidate = self._clip_id_factory()
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError("클립 ID 생성기가 비어 있지 않은 문자열을 반환해야 합니다.")
        return candidate

    def _publish(self) -> None:
        self._note_autosave_change()
        self.state_changed.emit()
        self.status_changed.emit(self.state.status_message)

    def _note_autosave_change(self) -> None:
        if self._runtime is None or not self.state.is_dirty:
            return
        project = self._media_library.project
        if project == self._last_autosave_project:
            return
        self._runtime.autosave.note_change(
            project,
            normal_path=self.state.project_path,
            history_position=self._history_position,
            summary=self.state.status_message,
        )
        self._last_autosave_project = project

    def _set_status(self, message: str) -> None:
        self.state.status_message = message
        self.status_changed.emit(message)

    def report_status(self, message: str) -> None:
        """Publish non-persistent user feedback requested by the view adapter."""

        self._set_status(message)

    def _execute_edit(self, label: str, operation: Callable[[], None]) -> None:
        """Run one mock editing command and append it to the session history."""

        before = deepcopy(self.state)
        operation()
        self._normalise_timeline()
        self.state.is_dirty = True
        self.state.status_message = f"{label} · 목업"
        after = deepcopy(self.state)
        del self._history[self._history_position :]
        self._media_library.discard_redo()
        self._history.append(
            _ControllerHistoryEntry(
                label,
                core=False,
                mock=MockHistoryEntry(label, before, after),
            )
        )
        self._history_position = len(self._history)
        self._publish()

    def _record_core_history(self, previous_position: int) -> None:
        del self._history[self._history_position :]
        for entry in self._media_library.history[previous_position:]:
            self._history.append(_ControllerHistoryEntry(entry.label, core=True))
        self._history_position = len(self._history)

    def _execute_core(self, command: ProjectCommand, success_message: str) -> Project | None:
        previous_position = self._media_library.history_position
        try:
            project = self._media_library.execute(command)
        except (CommandError, TypeError, ValueError) as error:
            self._set_status(f"편집할 수 없습니다 · {error}")
            return None
        self._record_core_history(previous_position)
        self._sync_from_core(project)
        self.state.is_dirty = project != self._saved_project
        self.state.status_message = success_message
        self._publish()
        return project

    def _normalise_timeline(self) -> None:
        cursor = 0
        for clip in self.state.visual_clips:
            clip.start_ms = cursor
            cursor += clip.duration_ms
        total = self.state.total_duration_ms
        self.state.playhead_ms = min(max(self.state.playhead_ms, 0), total)
        existing_ids = {clip.clip_id for clip in self.state.all_clips}
        if self.state.selected_clip_id not in existing_ids:
            self.state.selected_clip_id = None
        self.state.selected_clip_ids = [
            clip_id for clip_id in self.state.selected_clip_ids if clip_id in existing_ids
        ]
        if self.state.selected_clip_id is None:
            self.state.selected_clip_ids.clear()

    def _reset_playback(self, project: Project) -> None:
        self._playback = PlaybackClock(self._playback_clock_ns)
        self._playback.sync_project(project)
        self.state.playhead_ms = 0
        self.state.is_playing = False

    def _seek_preview(self, position: ProjectTime) -> ProjectTime:
        result = self._playback.seek(self._media_library.project, position)
        self.state.playhead_ms = ui_milliseconds(result)
        self.state.is_playing = self._playback.is_playing
        return result

    def _reset_history(self) -> None:
        self._history.clear()
        self._history_position = 0

    def new_project(self) -> None:
        self._cancel_media_import()
        self._cancel_relink()
        previous_project = self._media_library.project
        project = Project.empty(project_id=str(uuid4()))
        self._media_library.reset(project)
        self.state = MockProjectState(status_message="새 프로젝트를 만들었습니다")
        self._reset_playback(project)
        self._saved_project = project
        self._last_autosave_project = project
        if self._runtime is not None:
            self._runtime.autosave.normal_save_completed(previous_project)
            self._cancel_background_handles()
            self._runtime.media_queue.replace_project()
        self.last_persistence_error = None
        self._reset_history()
        self._publish()

    def _persistent_project(self) -> Project:
        base_project = self._media_library.project
        core_media = {media.asset_id: media for media in base_project.media}
        media: list[MediaReference] = []
        for asset in self.state.assets.values():
            reference = core_media.get(asset.asset_id)
            if reference is None:
                reference = MediaReference(
                    asset_id=asset.asset_id,
                    name=asset.name,
                    source_path=asset.source_path,
                    kind=UI_TO_CORE_MEDIA_KIND[asset.kind],
                    duration=(
                        ProjectTime.from_milliseconds(asset.duration_ms)
                        if asset.duration_ms is not None
                        else None
                    ),
                    width=asset.width,
                    height=asset.height,
                )
            media.append(reference)

        clips_by_track: dict[CoreTrackKind, list[Clip]] = {
            kind: [] for kind in CoreTrackKind
        }
        for mock_clip in self.state.all_clips:
            try:
                exact_clip = base_project.clip(mock_clip.clip_id)
            except KeyError:
                exact_clip = None
            if exact_clip is not None and self._mock_matches_exact_clip(mock_clip, exact_clip):
                clips_by_track[exact_clip.track].append(exact_clip)
                continue
            rate = Fraction(str(mock_clip.speed))
            core_track = UI_TO_CORE_TRACK_KIND[mock_clip.track]
            clips_by_track[core_track].append(
                Clip(
                    clip_id=mock_clip.clip_id,
                    track=core_track,
                    asset_id=mock_clip.asset_id,
                    label=mock_clip.label,
                    timeline_start=ProjectTime.from_milliseconds(mock_clip.start_ms),
                    duration=ProjectTime.from_milliseconds(mock_clip.duration_ms),
                    source_in=ProjectTime.from_milliseconds(mock_clip.source_in_ms),
                    source_out=(
                        ProjectTime.from_milliseconds(mock_clip.source_out_ms)
                        if mock_clip.source_out_ms is not None
                        else None
                    ),
                    playback_rate=PlaybackRate(rate.numerator, rate.denominator),
                    audio_level=(
                        AudioLevel(mock_clip.volume)
                        if core_track
                        in {
                            CoreTrackKind.VISUAL,
                            CoreTrackKind.MUSIC,
                            CoreTrackKind.NARRATION,
                        }
                        else AudioLevel()
                    ),
                    audio_muted=(
                        mock_clip.muted
                        if core_track
                        in {
                            CoreTrackKind.VISUAL,
                            CoreTrackKind.MUSIC,
                            CoreTrackKind.NARRATION,
                        }
                        else False
                    ),
                    fade_in=ProjectTime.from_milliseconds(mock_clip.fade_in_ms),
                    fade_out=ProjectTime.from_milliseconds(mock_clip.fade_out_ms),
                    ducking=(
                        DUCKING_TO_CORE.get(mock_clip.ducking, DuckingPreset.OFF)
                        if core_track is CoreTrackKind.NARRATION
                        else DuckingPreset.OFF
                    ),
                    fit_mode=(
                        FIT_TO_CORE.get(mock_clip.fit_mode, FitMode.FIT)
                        if core_track is CoreTrackKind.VISUAL
                        else FitMode.FIT
                    ),
                    user_rotation=(
                        UserRotation(mock_clip.rotation)
                        if core_track is CoreTrackKind.VISUAL
                        else UserRotation.NONE
                    ),
                    brightness=(
                        Brightness(mock_clip.brightness)
                        if core_track is CoreTrackKind.VISUAL
                        else Brightness()
                    ),
                    effect_preset=(
                        EFFECT_TO_CORE.get(
                            mock_clip.effect, VisualEffectPreset.NONE
                        )
                        if core_track is CoreTrackKind.VISUAL
                        else VisualEffectPreset.NONE
                    ),
                    text=(
                        _text_overlay_from_mock(mock_clip)
                        if core_track is CoreTrackKind.TEXT
                        else None
                    ),
                )
            )
        transitions = tuple(
            transition
            for boundary, value in self.state.transitions.items()
            if (transition := _transition_from_mock(boundary, value)) is not None
        )
        return Project(
            schema_version=base_project.schema_version,
            project_id=base_project.project_id,
            name=self.state.project_name,
            canvas=Canvas(
                self.state.canvas_width,
                self.state.canvas_height,
                self.state.reference_asset_id,
            ),
            media=tuple(media),
              tracks=tuple(
                  TimelineTrack(kind, tuple(clips_by_track[kind])) for kind in CoreTrackKind
              ),
              mixer=MixerSettings(
                  original=AudioLevel(self.state.original_bus_volume),
                  music=AudioLevel(self.state.music_bus_volume),
                  narration=AudioLevel(self.state.narration_bus_volume),
              ),
              transitions=transitions,
          )

    @staticmethod
    def _mock_matches_exact_clip(mock_clip: MockClip, exact_clip: Clip) -> bool:
        return (
            UI_TO_CORE_TRACK_KIND[mock_clip.track] is exact_clip.track
            and mock_clip.asset_id == exact_clip.asset_id
            and mock_clip.label == exact_clip.label
            and mock_clip.start_ms == exact_clip.timeline_start.to_milliseconds()
            and mock_clip.duration_ms == exact_clip.duration.to_milliseconds()
            and mock_clip.source_in_ms == exact_clip.source_in.to_milliseconds()
            and mock_clip.source_out_ms
            == (
                exact_clip.source_out.to_milliseconds()
                if exact_clip.source_out is not None
                else None
            )
            and mock_clip.speed == float(exact_clip.playback_rate.fraction)
            and mock_clip.volume == exact_clip.audio_level.percent
            and mock_clip.muted is exact_clip.audio_muted
            and mock_clip.fade_in_ms == exact_clip.fade_in.to_milliseconds()
            and mock_clip.fade_out_ms == exact_clip.fade_out.to_milliseconds()
            and mock_clip.ducking == CORE_TO_DUCKING[exact_clip.ducking]
            and mock_clip.fit_mode == CORE_TO_FIT[exact_clip.fit_mode]
            and mock_clip.rotation == int(exact_clip.user_rotation)
            and mock_clip.brightness == exact_clip.brightness.percent
            and mock_clip.effect == CORE_TO_EFFECT[exact_clip.effect_preset]
            and (
                exact_clip.text is None
                or _text_overlay_from_mock(mock_clip) == exact_clip.text
            )
        )

    @staticmethod
    def _state_from_project(project: Project, path: str) -> MockProjectState:
        assets: dict[str, MockAsset] = {}
        for media in project.media:
            assets[media.asset_id] = MockAsset(
                asset_id=media.asset_id,
                name=media.name,
                kind=CORE_TO_UI_MEDIA_KIND[media.kind],
                duration_ms=(
                    media.duration.to_milliseconds() if media.duration is not None else None
                ),
                width=media.width,
                height=media.height,
                color=MEDIA_COLORS[media.kind],
                source_path=media.source_path,
                status=(
                    AssetStatus.READY if Path(media.source_path).is_file() else AssetStatus.MISSING
                ),
                thumbnail_status=(
                    "대기"
                    if media.kind in {CoreMediaKind.VIDEO, CoreMediaKind.PHOTO}
                    else "해당 없음"
                ),
                waveform_status=(
                    "대기"
                    if any(stream.kind is MediaStreamKind.AUDIO for stream in media.streams)
                    else "해당 없음"
                ),
                stream_summary=_stream_summary(media),
                is_real_media=True,
            )

        state = MockProjectState(
            project_name=project.name,
            project_path=path,
            assets=assets,
            canvas_width=project.canvas.width,
            canvas_height=project.canvas.height,
            reference_asset_id=project.canvas.reference_asset_id,
            original_bus_volume=project.mixer.original.percent,
            music_bus_volume=project.mixer.music.percent,
            narration_bus_volume=project.mixer.narration.percent,
            status_message="프로젝트를 열었습니다",
        )
        target_lists = {
            TrackKind.VISUAL: state.visual_clips,
            TrackKind.MUSIC: state.music_clips,
            TrackKind.NARRATION: state.narration_clips,
            TrackKind.TEXT: state.text_clips,
        }
        for track in project.tracks:
            for clip in track.clips:
                projected = MockClip(
                    clip_id=clip.clip_id,
                    track=CORE_TO_UI_TRACK_KIND[track.kind],
                    asset_id=clip.asset_id,
                    label=clip.label,
                    start_ms=clip.timeline_start.to_milliseconds(),
                    duration_ms=clip.duration.to_milliseconds(),
                    source_in_ms=clip.source_in.to_milliseconds(),
                    source_out_ms=(
                        clip.source_out.to_milliseconds()
                        if clip.source_out is not None
                        else None
                    ),
                    speed=float(clip.playback_rate.fraction),
                    volume=clip.audio_level.percent,
                    muted=clip.audio_muted,
                )
                _sync_creative_values(projected, clip)
                target_lists[CORE_TO_UI_TRACK_KIND[track.kind]].append(projected)
        state.transitions = {
            f"{transition.left_clip_id}|{transition.right_clip_id}": (
                f"{CORE_TO_TRANSITION[transition.preset]} · "
                f"{float(transition.duration.to_fractional_seconds()):g}초"
            )
            for transition in project.transitions
        }
        missing_count = sum(asset.status is AssetStatus.MISSING for asset in assets.values())
        if missing_count:
            state.status_message = f"프로젝트를 열었습니다 · 누락 미디어 {missing_count}개"
        return state

    def _sync_from_core(self, project: Project) -> None:
        """Project persistent fields into the existing Qt presentation state."""

        previous_assets = self.state.assets
        assets: dict[str, MockAsset] = {}
        for media in project.media:
            existing = previous_assets.get(media.asset_id)
            if existing is None:
                thumbnail = self._media_library.thumbnail(media.asset_id)
                thumbnail_failure = self._media_library.thumbnail_failure(media.asset_id)
                existing = MockAsset(
                    asset_id=media.asset_id,
                    name=media.name,
                    kind=CORE_TO_UI_MEDIA_KIND[media.kind],
                    duration_ms=(
                        media.duration.to_milliseconds()
                        if media.duration is not None
                        else None
                    ),
                    width=media.width,
                    height=media.height,
                    color=MEDIA_COLORS[media.kind],
                    source_path=media.source_path,
                    status=(
                        AssetStatus.READY
                        if Path(media.source_path).is_file()
                        else AssetStatus.MISSING
                    ),
                    thumbnail_png=(thumbnail.png_bytes if thumbnail is not None else None),
                    thumbnail_error=(
                        thumbnail_failure.message
                        if thumbnail_failure is not None
                        else None
                    ),
                    thumbnail_status=(
                        "준비됨"
                        if thumbnail is not None
                        else (
                            "실패"
                            if thumbnail_failure is not None
                            else (
                                "대기"
                                if media.kind in {CoreMediaKind.VIDEO, CoreMediaKind.PHOTO}
                                else "해당 없음"
                            )
                        )
                    ),
                    waveform_status=(
                        "대기"
                        if any(stream.kind is MediaStreamKind.AUDIO for stream in media.streams)
                        else "해당 없음"
                    ),
                    stream_summary=_stream_summary(media),
                    is_real_media=True,
                )
            if existing.is_real_media:
                existing.name = media.name
                existing.kind = CORE_TO_UI_MEDIA_KIND[media.kind]
                existing.duration_ms = (
                    media.duration.to_milliseconds() if media.duration is not None else None
                )
                existing.width = media.width
                existing.height = media.height
                existing.source_path = media.source_path
                existing.status = (
                    AssetStatus.READY
                    if Path(media.source_path).is_file()
                    else AssetStatus.MISSING
                )
                existing.stream_summary = _stream_summary(media)
            assets[media.asset_id] = existing
        self.state.assets = assets

        previous_clips = {clip.clip_id: clip for clip in self.state.all_clips}
        target_lists: dict[TrackKind, list[MockClip]] = {
            kind: [] for kind in TrackKind
        }
        for track in project.tracks:
            ui_track = CORE_TO_UI_TRACK_KIND[track.kind]
            for clip in track.clips:
                projected = previous_clips.get(clip.clip_id)
                if projected is None:
                    projected = MockClip(
                        clip_id=clip.clip_id,
                        track=ui_track,
                        asset_id=clip.asset_id,
                        label=clip.label,
                        start_ms=clip.timeline_start.to_milliseconds(),
                        duration_ms=clip.duration.to_milliseconds(),
                    )
                projected.track = ui_track
                projected.asset_id = clip.asset_id
                projected.label = clip.label
                projected.start_ms = clip.timeline_start.to_milliseconds()
                projected.duration_ms = clip.duration.to_milliseconds()
                projected.source_in_ms = clip.source_in.to_milliseconds()
                projected.source_out_ms = (
                    clip.source_out.to_milliseconds()
                    if clip.source_out is not None
                    else None
                )
                projected.speed = float(clip.playback_rate.fraction)
                projected.volume = clip.audio_level.percent
                projected.muted = clip.audio_muted
                _sync_creative_values(projected, clip)
                target_lists[ui_track].append(projected)

        self.state.visual_clips = target_lists[TrackKind.VISUAL]
        self.state.music_clips = target_lists[TrackKind.MUSIC]
        self.state.narration_clips = target_lists[TrackKind.NARRATION]
        self.state.text_clips = target_lists[TrackKind.TEXT]
        self.state.project_name = project.name
        self.state.canvas_width = project.canvas.width
        self.state.canvas_height = project.canvas.height
        self.state.reference_asset_id = project.canvas.reference_asset_id
        self.state.original_bus_volume = project.mixer.original.percent
        self.state.music_bus_volume = project.mixer.music.percent
        self.state.narration_bus_volume = project.mixer.narration.percent

        selection = TimelineSelection(
            tuple(self.state.selected_clip_ids),
            self.state.selected_clip_id,
        ).reconcile(project)
        self._apply_selection(selection)
        if self.state.selected_asset_id not in self.state.assets:
            self.state.selected_asset_id = None
        self.state.playhead_ms = min(
            max(ui_milliseconds(self._playback.sync_project(project)), 0),
            ui_milliseconds(project.duration),
        )
        self.state.is_playing = self._playback.is_playing
        self.state.transitions = {
            f"{transition.left_clip_id}|{transition.right_clip_id}": (
                f"{CORE_TO_TRANSITION[transition.preset]} · "
                f"{float(transition.duration.to_fractional_seconds()):g}초"
            )
            for transition in project.transitions
        }

    def import_sample_media(self) -> None:
        samples = _sample_assets()
        added = [asset_id for asset_id in samples if asset_id not in self.state.assets]
        if not added:
            self.state.selected_asset_id = "media-beach"
            self.state.selected_clip_id = None
            self.state.selected_clip_ids.clear()
            self._set_status("샘플 미디어가 이미 보관함에 있습니다 · 목업")
            self.state_changed.emit()
            return

        def operation() -> None:
            self.state.assets.update({asset_id: samples[asset_id] for asset_id in added})
            self.state.selected_asset_id = "media-beach"
            self.state.selected_clip_id = None
            self.state.selected_clip_ids.clear()
            self.state.import_warning = None

        self._execute_edit(f"{len(added)}개 미디어 가져오기", operation)
        self._media_library.reset(self._persistent_project())

    def import_media_files(self, source_paths: Sequence[str]) -> MediaImportReport:
        """Analyze selected local files and publish their project-backed library views."""

        previous_position = self._media_library.history_position
        report = self._media_library.import_paths(source_paths)
        return self._apply_import_report(report, previous_position)

    def request_media_import(self, source_paths: Sequence[str]) -> bool:
        """Analyze media off the UI thread when the W-10 runtime is available."""

        if self._runtime is None:
            self.import_media_files(source_paths)
            return True
        if not source_paths:
            self.import_media_files(())
            return False
        if self._pending_import is not None and not self._pending_import.done():
            self._set_status("이미 미디어를 분석하고 있습니다")
            return False

        paths = tuple(source_paths)
        self._import_cancel = Event()
        cancel = self._import_cancel

        def analyze() -> tuple[MediaAnalysisResult, ...]:
            results: list[MediaAnalysisResult] = []
            analyzer = self._media_library.analyzer
            if isinstance(analyzer, FfprobeAnalyzer):
                analyzer = FfprobeAnalyzer(
                    runner=lambda arguments, *, timeout: run_cancellable_process(
                        arguments,
                        cancel,
                        timeout=timeout,
                    )
                )
            for path in paths:
                if cancel.is_set():
                    break
                try:
                    results.append(analyzer.analyze(path))
                except (OSError, RuntimeError, TypeError, ValueError) as error:
                    results.append(
                        MediaAnalysisFailure(
                            path,
                            MediaAnalysisErrorCode.PROCESS_FAILED,
                            "미디어 분석 중 예기치 않은 오류가 발생했습니다.",
                            str(error),
                        )
                    )
            return tuple(results)

        self._pending_import = self._runtime.import_executor.submit(analyze)
        self._set_status(f"미디어 {len(paths)}개를 백그라운드에서 분석 중입니다")
        return True

    def _poll_media_import(self) -> None:
        future = self._pending_import
        if future is None or not future.done():
            return
        self._pending_import = None
        try:
            results = future.result()
        except Exception as error:  # noqa: BLE001 - isolate the analysis worker boundary
            self._set_status(f"미디어 분석 작업 실패 · {error}")
            return
        previous_position = self._media_library.history_position
        report = self._media_library.import_analyzed(results)
        self._apply_import_report(report, previous_position)

    def _cancel_media_import(self) -> None:
        self._import_cancel.set()
        if self._pending_import is not None:
            self._pending_import.cancel()
            self._pending_import = None

    def _cancel_relink(self) -> None:
        self._relink_cancel.set()
        if self._pending_relink is not None:
            self._pending_relink.cancel()
            self._pending_relink = None
        self._pending_relink_confirmation = None

    def _apply_import_report(
        self,
        report: MediaImportReport,
        previous_position: int,
    ) -> MediaImportReport:
        if report.cancelled:
            self._set_status("미디어 가져오기를 취소했습니다")
            self.state_changed.emit()
            return report

        for imported in report.imported:
            asset = self._asset_view(imported)
            self.state.assets[asset.asset_id] = asset

        selected_id: str | None = None
        if report.imported:
            selected_id = report.imported[0].reference.asset_id
            self.state.is_dirty = True
        elif report.duplicates:
            selected_id = report.duplicates[0].existing_asset_id
        if selected_id in self.state.assets:
            self.state.selected_asset_id = selected_id
            self.state.selected_clip_id = None
            self.state.selected_clip_ids.clear()

        if self._media_library.history_position > previous_position:
            self._record_core_history(previous_position)
            self._sync_from_core(self._media_library.project)
            self.state.is_dirty = self._media_library.project != self._saved_project

        self.state.import_warning = _import_warning_text(report)
        summary: list[str] = []
        if report.imported:
            summary.append(f"{len(report.imported)}개 가져오기 성공")
        if report.failures:
            summary.append(f"{len(report.failures)}개 실패")
        if report.duplicates:
            summary.append(f"{len(report.duplicates)}개 중복 건너뜀")
        if report.thumbnail_failures:
            summary.append(f"썸네일 {len(report.thumbnail_failures)}개 기본 아이콘 사용")
        self.state.status_message = " · ".join(summary) or "가져올 미디어가 없습니다"
        self._publish()
        for imported in report.imported:
            self._schedule_background_artifacts(imported.reference)
        return report

    def _asset_view(self, imported: ImportedMedia) -> MockAsset:
        reference = imported.reference
        thumbnail_failure = self._media_library.thumbnail_failure(reference.asset_id)
        return MockAsset(
            asset_id=reference.asset_id,
            name=reference.name,
            kind=CORE_TO_UI_MEDIA_KIND[reference.kind],
            duration_ms=(
                reference.duration.to_milliseconds()
                if reference.duration is not None
                else None
            ),
            width=reference.width,
            height=reference.height,
            color=MEDIA_COLORS[reference.kind],
            source_path=reference.source_path,
            thumbnail_png=(
                imported.thumbnail.png_bytes if imported.thumbnail is not None else None
            ),
            thumbnail_error=(
                thumbnail_failure.message if thumbnail_failure is not None else None
            ),
            thumbnail_status=(
                "준비됨"
                if imported.thumbnail is not None
                else (
                    "실패"
                    if thumbnail_failure is not None
                    else (
                        "대기"
                        if reference.kind in {CoreMediaKind.VIDEO, CoreMediaKind.PHOTO}
                        else "해당 없음"
                    )
                )
            ),
            waveform_status=(
                "대기"
                if any(stream.kind is MediaStreamKind.AUDIO for stream in reference.streams)
                else "해당 없음"
            ),
            stream_summary=_stream_summary(reference),
            is_real_media=True,
        )

    def load_sample_project(self) -> None:
        self._media_library.reset()
        assets = _sample_assets()
        state = MockProjectState(
            project_name="제주 여행 목업",
            project_path=r"C:\MockProjects\제주 여행 목업.mmrproj",
            assets=assets,
            canvas_width=1920,
            canvas_height=1080,
            reference_asset_id="media-beach",
            status_message="샘플 편집 프로젝트를 열었습니다 · 목업",
        )
        state.visual_clips = [
            MockClip(
                "clip-beach",
                TrackKind.VISUAL,
                "media-beach",
                "해변 산책",
                0,
                8_000,
                source_out_ms=8_000,
            ),
            MockClip(
                "clip-market",
                TrackKind.VISUAL,
                "media-market",
                "야시장",
                8_000,
                6_000,
                source_in_ms=1_000,
                source_out_ms=7_000,
            ),
            MockClip(
                "clip-photo",
                TrackKind.VISUAL,
                "media-photo",
                "한라산 사진",
                14_000,
                5_000,
            ),
        ]
        state.music_clips = [
            MockClip(
                "clip-music",
                TrackKind.MUSIC,
                "media-music",
                "여행 배경음악",
                0,
                19_000,
                source_out_ms=19_000,
                volume=55,
                fade_in_ms=1_000,
                fade_out_ms=1_000,
            )
        ]
        state.narration_clips = [
            MockClip(
                "clip-narration",
                TrackKind.NARRATION,
                "media-narration",
                "장소 설명",
                4_000,
                8_000,
                source_out_ms=8_000,
                volume=90,
                ducking="보통",
            )
        ]
        state.text_clips = [
            MockClip(
                "clip-caption",
                TrackKind.TEXT,
                None,
                "캡션 · 협재 해변",
                3_000,
                3_000,
                text_kind="캡션",
                text_content="협재 해변",
            )
        ]
        state.transitions = {"clip-beach|clip-market": "페이드 · 0.75초"}
        self.state = state
        project = self._persistent_project()
        self._media_library.reset(project)
        self._reset_playback(project)
        self._saved_project = project
        self._reset_history()
        self._publish()

    def save_project(self, path: str | None = None) -> bool:
        target = path or self.state.project_path
        if target is None:
            self.last_persistence_error = None
            self._set_status("프로젝트 저장을 취소했습니다")
            return False
        try:
            project = self._media_library.project
            self._project_store.save(project, target)
        except (ProjectPersistenceError, ValueError) as error:
            self.last_persistence_error = str(error)
            self._set_status(f"프로젝트 저장 실패 · {error}")
            return False
        self.state.project_path = str(Path(target))
        self._saved_project = project
        self.state.is_dirty = False
        self.last_persistence_error = None
        if self._runtime is not None:
            self._runtime.autosave.normal_save_completed(project)
            try:
                self._runtime.recent_projects.record(target, project.name)
            except OSError:
                pass
        self._set_status("프로젝트를 저장했습니다")
        self.state_changed.emit()
        return True

    def open_project(self, path: str) -> bool:
        self._cancel_media_import()
        self._cancel_relink()
        previous_project = self._media_library.project
        try:
            project = self._project_store.load(path)
            next_state = self._state_from_project(project, str(Path(path)))
        except (ProjectPersistenceError, ValueError) as error:
            self.last_persistence_error = str(error)
            self._set_status(f"프로젝트 열기 실패 · {error}")
            return False

        self._media_library.reset(project)
        self._saved_project = project
        self._last_autosave_project = project
        self.state = next_state
        self._reset_playback(project)
        self._reset_history()
        self._id_counter = count(1)
        self.last_persistence_error = None
        if self._runtime is not None:
            self._runtime.autosave.normal_save_completed(previous_project)
            self._cancel_background_handles()
            self._runtime.media_queue.replace_project()
            try:
                self._runtime.recent_projects.record(path, project.name)
            except OSError:
                pass
        self._publish()
        for reference in project.media:
            self._schedule_background_artifacts(reference)
        return True

    def discard_unsaved_changes(self) -> None:
        """Clear the dirty flag through the controller boundary."""

        self.state.is_dirty = False
        self.state_changed.emit()

    def apply_recovery_choice(self, choice: str) -> None:
        """Apply a validated recovery choice or retain the legacy mock scenario."""

        candidate = self.pending_recovery
        if self._runtime is not None and candidate is not None:
            choices = {
                "자동 저장본": RecoveryChoice.AUTOSAVE,
                "정상 저장본": RecoveryChoice.NORMAL,
                "나중에 결정": RecoveryChoice.LATER,
            }
            selected = choices.get(choice)
            if selected is None:
                self._set_status("알 수 없는 복구 선택입니다")
                return
            try:
                result = self._runtime.autosave_store.choose(candidate, selected)
            except RuntimeError as error:
                self._set_status(f"프로젝트 복구 실패 · {error}")
                return
            if result.deferred:
                self._set_status("복구 결정을 미뤘습니다 · 다음 시작에도 복구본을 유지합니다")
                return
            if result.project is None:
                return
            if selected is RecoveryChoice.NORMAL:
                self._runtime.autosave_store.discard(candidate)
            previous_project = self._media_library.project
            self._runtime.autosave.normal_save_completed(previous_project)
            self._media_library.reset(result.project)
            self._saved_project = (
                candidate.normal_project if result.dirty and candidate.normal_project else result.project
            )
            self._last_autosave_project = result.project
            self.state = self._state_from_project(result.project, result.normal_path or "")
            self.state.project_path = result.normal_path
            self.state.is_dirty = result.dirty
            self.state.status_message = (
                f"{choice}을 열었습니다 · 정상 저장본을 덮어쓰지 않았습니다"
            )
            self._reset_playback(result.project)
            self._reset_history()
            self._recovery_candidates.pop(0)
            self._cancel_background_handles()
            self._runtime.media_queue.replace_project()
            self._publish()
            for reference in result.project.media:
                self._schedule_background_artifacts(reference)
            return

        self.load_sample_project()
        self.state.is_dirty = choice == "자동 저장본"
        self.state.status_message = f"{choice}을 열었습니다 · 정상 저장본을 덮어쓰지 않음"
        self._publish()

    def rename_project(self, name: str) -> bool:
        clean_name = name.strip()
        if not clean_name:
            self._set_status("프로젝트 이름은 비워 둘 수 없습니다")
            return False
        if clean_name == self.state.project_name:
            return True

        return (
            self._execute_core(
                RenameProject(clean_name),
                "프로젝트 이름을 변경했습니다",
            )
            is not None
        )

    def select_asset(self, asset_id: str | None) -> None:
        if asset_id is not None and asset_id not in self.state.assets:
            return
        self.state.selected_asset_id = asset_id
        self.state.selected_clip_id = None
        self.state.selected_clip_ids.clear()
        if asset_id is None:
            self._set_status("보관함 선택을 해제했습니다")
        else:
            self._set_status(f"{self.state.assets[asset_id].name} 선택")
        self.state_changed.emit()

    def select_clip(self, clip_id: str | None) -> None:
        clip = next((item for item in self.state.all_clips if item.clip_id == clip_id), None)
        if clip_id is not None and clip is None:
            return
        selection = (
            TimelineSelection()
            if clip_id is None
            else TimelineSelection.from_project(
                self._media_library.project,
                (clip_id,),
                clip_id,
            )
        )
        self._apply_selection(selection)
        self.state.selected_asset_id = None
        if clip is None:
            self._set_status("타임라인 선택을 해제했습니다")
        else:
            if clip.track is TrackKind.VISUAL:
                try:
                    exact_clip = self._media_library.project.clip(clip.clip_id)
                except KeyError:
                    self.state.playhead_ms = clip.start_ms
                else:
                    self._seek_preview(exact_clip.timeline_start)
            self._set_status(f"{clip.label} 클립 선택")
        self.state_changed.emit()

    def _apply_selection(self, selection: TimelineSelection) -> None:
        self.state.selected_clip_ids = list(selection.clip_ids)
        self.state.selected_clip_id = selection.active_clip_id

    def select_clips(self, clip_ids: list[str], active_clip_id: str | None) -> bool:
        existing = {clip.clip_id: clip for clip in self.state.all_clips}
        try:
            selection = TimelineSelection.from_project(
                self._media_library.project,
                tuple(clip_ids),
                active_clip_id,
            )
        except ValueError as error:
            self._set_status(f"여러 클립을 선택할 수 없습니다 · {error}")
            self.state_changed.emit()
            return False
        self._apply_selection(selection)
        self.state.selected_asset_id = None
        if not selection.clip_ids:
            self._set_status("타임라인 선택을 해제했습니다")
        elif len(selection.clip_ids) == 1 and selection.active_clip_id is not None:
            clip = existing[selection.active_clip_id]
            if clip.track is TrackKind.VISUAL:
                try:
                    exact_clip = self._media_library.project.clip(clip.clip_id)
                except KeyError:
                    self.state.playhead_ms = clip.start_ms
                else:
                    self._seek_preview(exact_clip.timeline_start)
            self._set_status(f"{clip.label} 클립 선택")
        else:
            self._set_status(
                f"클립 {len(selection.clip_ids)}개 선택 · 공통 명령만 사용할 수 있습니다"
            )
        self.state_changed.emit()
        return True

    def add_selected_to_timeline(self, *, narration: bool = False) -> bool:
        asset = self.selected_asset
        if asset is None:
            self._set_status("먼저 보관함에서 미디어를 선택하세요")
            return False
        return self.add_asset_to_timeline(asset.asset_id, narration=narration)

    def can_drop_visual_asset(self, asset_id: str) -> bool:
        asset = self.state.assets.get(asset_id)
        return (
            asset is not None
            and asset.status is AssetStatus.READY
            and asset.kind in {MediaKind.VIDEO, MediaKind.PHOTO}
        )

    def add_asset_to_timeline(
        self, asset_id: str, *, narration: bool = False, visual_index: int | None = None
    ) -> bool:
        """Add the dragged asset by identity, independent of the current selection."""
        asset = self.state.assets.get(asset_id)
        if asset is None:
            return False
        if visual_index is not None and not self.can_drop_visual_asset(asset_id):
            return False
        if asset.status is not AssetStatus.READY:
            self._set_status("누락되거나 읽을 수 없는 미디어는 추가할 수 없습니다")
            return False

        explicit_narration = narration or asset.asset_id == "media-narration"
        try:
            clip_id = self._next_clip_id()
        except ValueError as error:
            self._set_status(f"미디어를 추가할 수 없습니다 · {error}")
            return False
        result = self._execute_core(
            AddMediaClip(
                asset.asset_id,
                clip_id,
                narration=explicit_narration,
                visual_index=visual_index,
            ),
            "타임라인에 미디어를 추가했습니다",
        )
        if result is None:
            return False
        added = result.clip(clip_id)
        self.state.selected_asset_id = None
        self.state.selected_clip_id = clip_id
        self.state.selected_clip_ids = [clip_id]
        self._seek_preview(added.timeline_start)
        self._publish()
        return True

    def asset_usage_count(self, asset_id: str) -> int:
        return sum(clip.asset_id == asset_id for clip in self.state.all_clips)

    def update_mixer(self, *, original: int, music: int, narration: int) -> bool:
        """Commit all three project buses together so undo never exposes a partial mix."""

        try:
            settings = MixerSettings(
                original=AudioLevel(original),
                music=AudioLevel(music),
                narration=AudioLevel(narration),
            )
        except (TypeError, ValueError) as error:
            self._set_status(f"전체 오디오 믹서를 적용할 수 없습니다 · {error}")
            return False
        result = self._execute_core(
            UpdateMixerSettings(settings),
            "전체 오디오 믹서를 적용했습니다",
        )
        if result is None:
            return False
        self._publish()
        return True

    def remove_selected_asset(self) -> bool:
        asset = self.selected_asset
        if asset is None:
            self._set_status("보관함에서 제거할 미디어를 선택하세요")
            return False
        usage_count = self.asset_usage_count(asset.asset_id)

        if usage_count:
            self._set_status(
                f"타임라인에서 사용 중인 미디어는 제거할 수 없습니다 · "
                f"관련 클립 {usage_count}개를 먼저 제거하세요"
            )
            self.state_changed.emit()
            return False

        if self._media_library.contains(asset.asset_id):
            previous_position = self._media_library.history_position
            result = self._media_library.remove(asset.asset_id)
            if isinstance(result, MediaRemovalFailure):
                suffix = (
                    f" · 관련 클립 {result.usage_count}개"
                    if result.usage_count
                    else ""
                )
                self._set_status(f"{result.message}{suffix}")
                self.state_changed.emit()
                return False
            self._record_core_history(previous_position)
            self._sync_from_core(self._media_library.project)
            self.state.is_dirty = self._media_library.project != self._saved_project
            self.state.status_message = (
                "보관함에서 미디어를 제거했습니다 · 컴퓨터의 원본 파일은 유지됩니다"
            )
            self._publish()
            return True

        def operation() -> None:
            del self.state.assets[asset.asset_id]
            self.state.selected_asset_id = None

        self._execute_edit("보관함에서 제거", operation)
        return True

    def inject_missing_media(self) -> None:
        if "media-missing" not in self.state.assets:
            self.state.assets["media-missing"] = MockAsset(
                "media-missing",
                "찾을 수 없는 영상.mp4",
                MediaKind.VIDEO,
                7_000,
                1920,
                1080,
                "#6c7078",
                r"D:\Moved\찾을 수 없는 영상.mp4",
                AssetStatus.MISSING,
            )
        missing_asset = self.state.assets["media-missing"]
        missing_asset.status = AssetStatus.MISSING
        missing_asset.source_path = r"D:\Moved\찾을 수 없는 영상.mp4"
        missing_clip = next(
            (clip for clip in self.state.visual_clips if clip.asset_id == "media-missing"),
            None,
        )
        if missing_clip is None:
            missing_clip = MockClip(
                "clip-missing",
                TrackKind.VISUAL,
                "media-missing",
                "찾을 수 없는 영상",
                self.state.total_duration_ms,
                7_000,
                source_out_ms=7_000,
            )
            self.state.visual_clips.append(missing_clip)
            self._normalise_timeline()
        if self.state.canvas_width is None or self.state.canvas_height is None:
            self.state.canvas_width = 1920
            self.state.canvas_height = 1080
            self.state.reference_asset_id = "media-missing"
        project = self._persistent_project()
        self._media_library.reset(project)
        self._saved_project = project
        self._reset_history()
        self._reset_playback(project)
        self._seek_preview(project.clip(missing_clip.clip_id).timeline_start)
        self.state.selected_asset_id = "media-missing"
        self.state.selected_clip_id = None
        self.state.selected_clip_ids.clear()
        self.state.status_message = "누락 미디어와 영향받는 클립 1개를 추가했습니다 · 목업 상태"
        self._publish()

    def inject_import_failure(self) -> None:
        if "media-broken" not in self.state.assets:
            self.state.assets["media-broken"] = MockAsset(
                "media-broken",
                "손상된 파일.mov",
                MediaKind.VIDEO,
                None,
                None,
                None,
                "#873f46",
                r"C:\MockMedia\손상된 파일.mov",
                AssetStatus.ERROR,
            )
        self.state.import_warning = "5개 성공 · 1개 실패: 파일 헤더를 읽을 수 없습니다"
        self.state.selected_asset_id = "media-broken"
        self.state.selected_clip_id = None
        self.state.selected_clip_ids.clear()
        self.state.status_message = "일부 파일을 가져오지 못했습니다 · 상세 정보를 확인하세요"
        self._publish()

    def relink_selected_asset(self) -> bool:
        asset = self.selected_asset
        if asset is None or asset.status is AssetStatus.READY:
            self._set_status("다시 연결할 누락 미디어를 선택하세요")
            return False

        def operation() -> None:
            asset.status = AssetStatus.READY
            asset.source_path = rf"C:\MockMedia\다시 연결됨\{asset.name}"

        self._execute_edit("누락 미디어 다시 연결", operation)
        return True

    def relink_selected_asset_to(self, candidate_path: str, *, confirmed: bool = False) -> bool:
        asset = self.selected_asset
        if asset is None or asset.status is AssetStatus.READY:
            self._set_status("다시 연결할 누락 미디어를 선택하세요")
            return False
        previous_position = self._media_library.history_position
        result = MediaRelinker(
            self._media_library.executor,
            self._media_library.analyzer,
        ).relink(asset.asset_id, candidate_path, confirmed=confirmed)
        self.last_relink_result = result
        if isinstance(result, RelinkFailure):
            differences = " ".join(result.comparison.differences) if result.comparison else ""
            self._set_status(f"다시 연결할 수 없습니다 · {result.message} {differences}".strip())
            return False
        return self._commit_relink_result(result, asset.asset_id, previous_position)

    def request_relink_selected_asset_to(self, candidate_path: str) -> bool:
        """Analyze a replacement off the UI thread and publish a later result."""

        asset = self.selected_asset
        if asset is None or asset.status is AssetStatus.READY:
            self._set_status("다시 연결할 누락 미디어를 선택하세요")
            return False
        if self._runtime is None:
            return self.relink_selected_asset_to(candidate_path)
        if self._pending_relink is not None and not self._pending_relink.done():
            self._set_status("이미 새 원본을 분석하고 있습니다")
            return False
        self._relink_cancel = Event()
        self._pending_relink_confirmation = None
        cancel = self._relink_cancel
        analyzer = self._media_library.analyzer
        if isinstance(analyzer, FfprobeAnalyzer):
            analyzer = analyzer.with_runner(
                lambda arguments, *, timeout: run_cancellable_process(
                    arguments,
                    cancel,
                    timeout=timeout,
                )
            )
        asset_id = asset.asset_id

        def inspect() -> tuple[str, str, object]:
            result = MediaRelinker(self._media_library.executor, analyzer).inspect(
                asset_id, candidate_path
            )
            return asset_id, candidate_path, result

        self._pending_relink = self._runtime.import_executor.submit(inspect)
        self._set_status("선택한 새 원본을 백그라운드에서 분석 중입니다")
        return True

    def _poll_relink(self) -> None:
        future = self._pending_relink
        if future is None or not future.done():
            return
        self._pending_relink = None
        try:
            asset_id, candidate_path, inspected = future.result()
        except Exception as error:  # noqa: BLE001 - isolate the analysis worker boundary
            self._set_status(f"새 원본 분석 실패 · {error}")
            return
        if isinstance(inspected, RelinkFailure):
            self.last_relink_result = inspected
            self._set_status(f"다시 연결할 수 없습니다 · {inspected.message}")
            return
        if not isinstance(inspected, tuple) or len(inspected) != 2:
            self._set_status("새 원본 분석 결과가 올바르지 않습니다")
            return
        analysis, comparison = inspected
        if not isinstance(analysis, MediaAnalysis) or not isinstance(
            comparison, RelinkComparison
        ):
            self._set_status("새 원본 분석 결과가 올바르지 않습니다")
            return
        relinker = MediaRelinker(self._media_library.executor, self._media_library.analyzer)
        if comparison.match is RelinkMatch.CONFIRM:
            failure = RelinkFailure(
                code=RelinkErrorCode.CONFIRMATION_REQUIRED,
                message="원본과 차이가 있어 사용자 확인이 필요합니다.",
                comparison=comparison,
            )
            self.last_relink_result = failure
            self._pending_relink_confirmation = (asset_id, analysis, comparison)
            self._set_status("새 원본에 확인이 필요한 차이가 있습니다")
            self.relink_confirmation_requested.emit(candidate_path, comparison.differences)
            return
        previous_position = self._media_library.history_position
        result = relinker.apply_inspected(asset_id, analysis, comparison)
        self.last_relink_result = result
        self._commit_relink_result(result, asset_id, previous_position)

    def confirm_pending_relink(self) -> bool:
        pending = self._pending_relink_confirmation
        self._pending_relink_confirmation = None
        if pending is None:
            self._set_status("확인할 새 원본이 없습니다")
            return False
        asset_id, analysis, comparison = pending
        previous_position = self._media_library.history_position
        result = MediaRelinker(
            self._media_library.executor, self._media_library.analyzer
        ).apply_inspected(asset_id, analysis, comparison, confirmed=True)
        self.last_relink_result = result
        return self._commit_relink_result(result, asset_id, previous_position)

    def cancel_pending_relink_confirmation(self) -> None:
        self._pending_relink_confirmation = None
        self._set_status("원본 다시 연결을 취소했습니다 · 프로젝트는 변경되지 않았습니다")

    def _commit_relink_result(
        self,
        result: RelinkSuccess | RelinkFailure,
        asset_id: str,
        previous_position: int,
    ) -> bool:
        if isinstance(result, RelinkFailure):
            differences = " ".join(result.comparison.differences) if result.comparison else ""
            self._set_status(f"다시 연결할 수 없습니다 · {result.message} {differences}".strip())
            return False
        self._record_core_history(previous_position)
        self._sync_from_core(self._media_library.project)
        self.state.is_dirty = self._media_library.project != self._saved_project
        self.state.selected_asset_id = asset_id
        self.state.status_message = "원본을 검증해 다시 연결했습니다"
        if self._runtime is not None:
            self._cancel_background_handles()
            self._runtime.media_queue.replace_project()
        self._publish()
        self._schedule_background_artifacts(result.replacement)
        return True

    def toggle_selected_proxy(self) -> bool:
        asset = self.selected_asset
        if asset is None:
            self._set_status("프록시를 사용할 영상 미디어를 선택하세요")
            return False
        if asset.kind is not MediaKind.VIDEO:
            self._set_status("목업 프록시는 영상 미디어에서만 사용할 수 있습니다")
            return False
        if asset.status is not AssetStatus.READY:
            self._set_status("누락되거나 읽을 수 없는 미디어의 프록시는 만들 수 없습니다")
            return False

        enabling = not asset.proxy_enabled
        if self._runtime is not None and asset.is_real_media:
            asset.proxy_enabled = enabling
            if enabling:
                asset.proxy_status = "대기 중"
                reference = self._media_library.project.media_reference(asset.asset_id)
                self._schedule_proxy(reference)
                self._set_status("프록시 생성을 백그라운드에서 시작했습니다")
            else:
                handle = self._background_handles.pop((asset.asset_id, CacheKind.PROXY), None)
                if handle is not None:
                    handle.cancel()
                self._release_proxy(asset.asset_id)
                asset.proxy_path = None
                asset.proxy_status = "사용 안 함"
                self._set_status("프록시 사용을 해제했습니다 · 원본과 캐시는 보존됩니다")
            self.state_changed.emit()
            return True

        def operation() -> None:
            asset.proxy_enabled = enabling
            asset.proxy_status = "준비됨 · 목업 대체본" if enabling else "사용 안 함"

        label = "프록시 사용" if enabling else "프록시 사용 해제"
        self._execute_edit(label, operation)
        return True

    def autosave_tick(self) -> None:
        if self._runtime is None:
            return
        self._runtime.autosave.tick()
        self._poll_media_import()
        self._poll_relink()
        status = {
            AutosaveState.IDLE: "대기",
            AutosaveState.WAITING: "변경 대기 중",
            AutosaveState.SAVING: "저장 중",
            AutosaveState.SAVED: "자동 저장됨",
            AutosaveState.FAILED: "자동 저장 실패",
            AutosaveState.CLOSED: "종료됨",
        }[self._runtime.autosave.state]
        if status != self.state.autosave_status:
            self.state.autosave_status = status
            if self._runtime.autosave.state is AutosaveState.FAILED:
                self.state.status_message = (
                    "자동 저장에 실패했습니다 · 편집은 계속할 수 있으며 저장 위치를 확인하세요"
                )
            self.state_changed.emit()
        self.poll_background_jobs()

    def poll_background_jobs(self) -> None:
        changed = False
        for identity, handle in tuple(self._background_handles.items()):
            result = handle.result
            if result is None:
                asset_id, kind = identity
                asset = self.state.assets.get(asset_id)
                if asset is not None and handle.state is JobState.RUNNING:
                    if kind is CacheKind.THUMBNAIL and asset.thumbnail_status != "생성 중":
                        asset.thumbnail_status = "생성 중"
                        changed = True
                    elif kind is CacheKind.WAVEFORM and asset.waveform_status != "생성 중":
                        asset.waveform_status = "생성 중"
                        changed = True
                    elif kind is CacheKind.PROXY and asset.proxy_enabled:
                        if asset.proxy_status != "생성 중":
                            asset.proxy_status = "생성 중"
                            changed = True
                continue
            asset_id, kind = identity
            asset = self.state.assets.get(asset_id)
            self._background_handles.pop(identity, None)
            if asset is None:
                continue
            if result.state is JobState.READY and result.path is not None:
                if kind is CacheKind.THUMBNAIL:
                    try:
                        asset.thumbnail_png = result.path.read_bytes()
                        asset.thumbnail_error = None
                        asset.thumbnail_status = "준비됨"
                    except OSError as error:
                        asset.thumbnail_error = str(error)
                        asset.thumbnail_status = "실패"
                elif kind is CacheKind.WAVEFORM:
                    asset.waveform_path = str(result.path)
                    asset.waveform_status = "준비됨"
                elif kind is CacheKind.PROXY and asset.proxy_enabled:
                    self._release_proxy(asset.asset_id)
                    protected = self._runtime.cache.protect(result.path) if self._runtime else result.path
                    self._protected_proxy_paths[asset.asset_id] = protected
                    asset.proxy_path = str(result.path)
                    asset.proxy_status = "준비됨 · 미리 보기 전용"
            elif kind is CacheKind.THUMBNAIL:
                asset.thumbnail_status = (
                    "취소됨" if result.state is JobState.CANCELLED else "실패"
                )
                asset.thumbnail_error = "썸네일 생성 작업이 실패했습니다."
            elif kind is CacheKind.WAVEFORM:
                asset.waveform_status = "취소됨" if result.state is JobState.CANCELLED else "실패"
            elif kind is CacheKind.PROXY and asset.proxy_enabled:
                asset.proxy_status = "취소됨" if result.state is JobState.CANCELLED else "실패"
            if result.state is JobState.FAILED:
                summary = {
                    CacheKind.THUMBNAIL: "썸네일을 만들지 못했습니다. 캐시 다시 생성을 시도하세요.",
                    CacheKind.WAVEFORM: "파형을 만들지 못했습니다. 원본 오디오를 확인하세요.",
                    CacheKind.PROXY: "프록시를 만들지 못했습니다. 원본 영상을 확인하세요.",
                }[kind]
                detail = (result.error or "").replace(asset.source_path, asset.name).strip()[:240]
                asset.background_error = f"{summary} {detail}".strip()
            changed = True
        if changed:
            if self._runtime is not None:
                try:
                    self._runtime.prune_cache()
                except OSError:
                    pass
            self.state_changed.emit()

    def _cancel_background_handles(self) -> None:
        for handle in self._background_handles.values():
            handle.cancel()
        self._background_handles.clear()
        for asset_id in tuple(self._protected_proxy_paths):
            self._release_proxy(asset_id)

    def _release_proxy(self, asset_id: str) -> None:
        path = self._protected_proxy_paths.pop(asset_id, None)
        if path is not None and self._runtime is not None:
            self._runtime.cache.unprotect(path)

    def regenerate_selected_cache(self) -> bool:
        """Remove only the selected item's owned cache entries and enqueue fresh work."""

        asset = self.selected_asset
        if self._runtime is None or asset is None or not asset.is_real_media:
            self._set_status("캐시를 다시 만들 실제 미디어를 선택하세요")
            return False
        try:
            reference = self._media_library.project.media_reference(asset.asset_id)
            keys = [self._runtime.artifacts.thumbnail_key(reference)] if reference.kind in {
                CoreMediaKind.VIDEO,
                CoreMediaKind.PHOTO,
            } else []
            if any(stream.kind is MediaStreamKind.AUDIO for stream in reference.streams):
                keys.append(self._runtime.artifacts.waveform_key(reference))
            if reference.kind is CoreMediaKind.VIDEO:
                keys.append(self._runtime.artifacts.proxy_key(reference))
        except (KeyError, OSError, ValueError):
            self._set_status("원본 상태를 확인할 수 없어 캐시를 다시 만들 수 없습니다")
            return False
        for kind in CacheKind:
            handle = self._background_handles.pop((asset.asset_id, kind), None)
            if handle is not None:
                handle.cancel()
        self._release_proxy(asset.asset_id)
        for key in keys:
            self._runtime.cache.remove(key)
        asset.thumbnail_png = None
        asset.thumbnail_error = None
        asset.background_error = None
        asset.thumbnail_status = "대기"
        asset.waveform_path = None
        asset.waveform_status = "대기"
        asset.proxy_path = None
        asset.proxy_status = "대기 중" if asset.proxy_enabled else "사용 안 함"
        self._schedule_background_artifacts(reference)
        if asset.proxy_enabled:
            self._schedule_proxy(reference)
        self._set_status("선택한 미디어의 캐시를 안전하게 다시 생성합니다")
        self.state_changed.emit()
        return True

    def _schedule_background_artifacts(self, reference: MediaReference) -> None:
        if self._runtime is None or not Path(reference.source_path).is_file():
            return
        generation = self._runtime.media_queue.project_generation
        scheduled = False
        try:
            if reference.kind in {CoreMediaKind.VIDEO, CoreMediaKind.PHOTO}:
                key = self._runtime.artifacts.thumbnail_key(reference)
                asset = self.state.assets.get(reference.asset_id)
                if asset is not None:
                    asset.thumbnail_status = "대기 중"
                self._background_handles[(reference.asset_id, CacheKind.THUMBNAIL)] = (
                    self._runtime.media_queue.submit(
                        key,
                        JobPriority.BACKGROUND_THUMBNAIL,
                        lambda cancel, media=reference: self._runtime.artifacts.create_thumbnail(
                            media, cancel
                        ),
                        project_generation=generation,
                    )
                )
                scheduled = True
            if any(stream.kind is MediaStreamKind.AUDIO for stream in reference.streams):
                key = self._runtime.artifacts.waveform_key(reference)
                asset = self.state.assets.get(reference.asset_id)
                if asset is not None:
                    asset.waveform_status = "대기 중"
                self._background_handles[(reference.asset_id, CacheKind.WAVEFORM)] = (
                    self._runtime.media_queue.submit(
                        key,
                        JobPriority.VISIBLE_WAVEFORM,
                        lambda cancel, media=reference: self._runtime.artifacts.create_waveform(
                            media, cancel
                        ),
                        project_generation=generation,
                    )
                )
                scheduled = True
        except (OSError, ValueError):
            return
        if scheduled:
            self.state_changed.emit()

    def _schedule_proxy(self, reference: MediaReference) -> None:
        if self._runtime is None:
            return
        try:
            key = self._runtime.artifacts.proxy_key(reference)
        except (OSError, ValueError):
            return
        self._background_handles[(reference.asset_id, CacheKind.PROXY)] = (
            self._runtime.media_queue.submit(
                key,
                JobPriority.PROXY,
                lambda cancel, media=reference: self._runtime.artifacts.create_proxy(media, cancel),
            )
        )

    def close_runtime(self, *, clean_exit: bool) -> None:
        self._cancel_media_import()
        self._cancel_relink()
        if self._runtime is not None:
            self._cancel_background_handles()
            self._runtime.close(clean_exit=clean_exit)

    def move_selected_visual(self, offset: int) -> bool:
        selected = self.selected_clips
        if not selected or any(clip.track is not TrackKind.VISUAL for clip in selected):
            self._set_status("이동할 영상 또는 사진 클립을 선택하세요")
            return False
        if offset not in {-1, 1}:
            self._set_status("클립 이동은 한 단계씩 수행해야 합니다")
            return False
        selected_ids = {clip.clip_id for clip in selected}
        first = min(self.state.visual_clips.index(clip) for clip in selected)
        current_insertion = sum(
            clip.clip_id not in selected_ids for clip in self.state.visual_clips[:first]
        )
        target = current_insertion + offset
        remaining_count = len(self.state.visual_clips) - len(selected)
        if target < 0 or target > remaining_count:
            self._set_status("선택한 클립은 더 이동할 수 없습니다")
            return False

        direction = "앞으로" if offset < 0 else "뒤로"
        return (
            self._execute_core(
                MoveVisualClipGroup(
                    tuple(clip.clip_id for clip in selected),
                    target,
                    history_label=(
                        f"클립 {direction} 이동"
                        if len(selected) == 1
                        else f"클립 {len(selected)}개 {direction} 이동"
                    ),
                ),
                f"클립 {len(selected)}개를 {direction} 이동했습니다",
            )
            is not None
        )

    def move_selected_absolute(self, delta_ms: int) -> bool:
        selected = self.selected_clips
        if not selected or any(clip.track is TrackKind.VISUAL for clip in selected):
            self._set_status("시간으로 이동할 보조 트랙 클립을 선택하세요")
            return False
        if type(delta_ms) is not int or delta_ms == 0:
            self._set_status("0이 아닌 정수 밀리초 이동값이 필요합니다")
            return False
        return (
            self._execute_core(
                MoveAbsoluteClipGroup(
                    tuple(clip.clip_id for clip in selected),
                    ProjectTime.from_milliseconds(delta_ms),
                    history_label=f"클립 {len(selected)}개 시간 이동",
                ),
                f"클립 {len(selected)}개를 {delta_ms:+d}ms 이동했습니다",
            )
            is not None
        )

    def move_selected_to_index(self, track: TrackKind, target_index: int) -> bool:
        """Commit one validated visual drag/drop result."""

        selected = self.selected_clips
        if track is not TrackKind.VISUAL or not selected:
            self._set_status("시각 클립만 삽입 위치로 끌어 이동할 수 있습니다")
            return False
        if any(clip.track is not track for clip in selected):
            self._set_status("선택과 놓기 위치의 트랙이 일치하지 않습니다")
            return False
        return (
            self._execute_core(
                MoveVisualClipGroup(
                    tuple(clip.clip_id for clip in selected),
                    target_index,
                    history_label=f"클립 {len(selected)}개 끌어 이동",
                ),
                f"클립 {len(selected)}개를 새 삽입 위치로 이동했습니다",
            )
            is not None
        )

    def move_selected_absolute_to_index(self, track: TrackKind, target_index: int) -> bool:
        """Translate one auxiliary-track selection to a drag insertion anchor."""

        selected = self.selected_clips
        if track is TrackKind.VISUAL or not selected or any(clip.track is not track for clip in selected):
            self._set_status("선택과 놓기 위치의 보조 트랙이 일치하지 않습니다")
            return False
        selected_ids = {clip.clip_id for clip in selected}
        remaining = [
            clip for clip in self.clips_for_track(track) if clip.clip_id not in selected_ids
        ]
        if type(target_index) is not int or not 0 <= target_index <= len(remaining):
            self._set_status("그룹을 놓을 위치가 트랙 범위를 벗어났습니다")
            return False
        if not remaining:
            self._set_status("클립이 이미 이 트랙의 유일한 그룹입니다")
            return False
        target_start = (
            remaining[target_index].start_ms
            if target_index < len(remaining)
            else max(clip.start_ms + clip.duration_ms for clip in remaining)
        )
        anchor_start = min(clip.start_ms for clip in selected)
        return self.move_selected_absolute(target_start - anchor_start)

    def delete_selected_clip(self) -> bool:
        selected = self.selected_clips
        if not selected:
            self._set_status("삭제할 타임라인 클립을 선택하세요")
            return False
        label = "클립 삭제" if len(selected) == 1 else f"클립 {len(selected)}개 삭제"
        if self._execute_core(
            DeleteClipGroup(
                tuple(clip.clip_id for clip in selected),
                history_label=label,
            ),
            f"{label} · 보관함과 원본 파일은 유지됩니다",
        ) is None:
            return False
        self._playback.sync_project(self._media_library.project)
        self.state.playhead_ms = ui_milliseconds(self._playback.position)
        self._publish()
        return True

    def split_selected_clip(self) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is TrackKind.TEXT:
            self._set_status("분할할 영상 또는 오디오 클립을 선택하세요")
            return False
        asset = self.asset_for_clip(clip)
        if asset is not None and asset.kind is MediaKind.PHOTO:
            self._set_status("사진은 분할 대신 표시 시간을 조절하세요")
            return False
        try:
            trailing_clip_id = self._next_clip_id()
        except ValueError as error:
            self._set_status(f"클립을 분할할 수 없습니다 · {error}")
            return False
        result = self._execute_core(
            SplitClip(
                clip.clip_id,
                self._playback.position,
                trailing_clip_id,
            ),
            "재생 위치에서 클립을 분할했습니다",
        )
        if result is None:
            return False
        trailing = result.clip(trailing_clip_id)
        self.state.selected_clip_id = trailing_clip_id
        self.state.selected_clip_ids = [trailing_clip_id]
        self._seek_preview(trailing.timeline_start)
        self._publish()
        return True

    def duplicate_selected_clip(self) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None:
            self._set_status("복제할 클립을 선택하세요")
            return False
        try:
            duplicate_id = self._next_clip_id()
        except ValueError as error:
            self._set_status(f"클립을 복제할 수 없습니다 · {error}")
            return False
        if self._execute_core(
            DuplicateTimelineClip(clip.clip_id, duplicate_id),
            "클립을 원본 바로 뒤에 복제했습니다",
        ) is None:
            return False
        self._apply_selection(
            TimelineSelection.from_project(
                self._media_library.project,
                (duplicate_id,),
                duplicate_id,
            )
        )
        self._publish()
        return True

    def update_selected_clip(
        self,
        *,
        duration_ms: int,
        speed: float,
        volume: int,
        muted: bool,
        fit_mode: str | None = None,
        effect: str | None = None,
        brightness: int | None = None,
        source_in_ms: int | None = None,
        source_out_ms: int | None = None,
    ) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None:
            self._set_status("속성을 바꿀 클립을 선택하세요")
            return False
        if duration_ms < 500:
            self._set_status("클립은 최소 0.5초 이상이어야 합니다")
            return False
        if not 0 <= volume <= 100:
            self._set_status("음량은 0~100% 범위여야 합니다")
            return False
        if speed not in {0.5, 1.0, 1.5, 2.0}:
            self._set_status("지원하는 속도는 0.5×, 1×, 1.5×, 2×입니다")
            return False
        if fit_mode is not None and fit_mode not in FIT_TO_CORE:
            self._set_status("지원하지 않는 화면 배치 프리셋입니다")
            return False
        if effect is not None and effect not in EFFECT_TO_CORE:
            self._set_status("지원하지 않는 시각 효과 프리셋입니다")
            return False
        if brightness is not None and not -100 <= brightness <= 100:
            self._set_status("밝기는 -100~100 범위여야 합니다")
            return False

        asset = self.asset_for_clip(clip)
        is_video = asset is not None and asset.kind is MediaKind.VIDEO
        is_photo = asset is not None and asset.kind is MediaKind.PHOTO
        if is_photo and not 1_000 <= duration_ms <= 30_000:
            self._set_status("사진 표시 시간은 1~30초 범위여야 합니다")
            return False
        new_in = clip.source_in_ms if source_in_ms is None else source_in_ms
        current_out = clip.source_out_ms or round(clip.source_in_ms + clip.duration_ms * clip.speed)
        new_out = current_out if source_out_ms is None else source_out_ms
        if not is_photo:
            source_limit = asset.duration_ms if asset is not None else None
            if new_in < 0 or new_out <= new_in or (
                source_limit is not None and new_out > source_limit
            ):
                self._set_status("원본 사용 구간이 미디어 길이를 벗어났습니다")
                return False
        if is_photo:
            command = UpdateClipTiming(
                clip.clip_id,
                photo_duration=ProjectTime.from_milliseconds(duration_ms),
                fit_mode=(
                    FIT_TO_CORE.get(fit_mode) if fit_mode is not None else None
                ),
                brightness=Brightness(brightness) if brightness is not None else None,
                effect_preset=(
                    EFFECT_TO_CORE.get(effect) if effect is not None else None
                ),
            )
        else:
            rate = Fraction(str(speed))
            command = UpdateClipTiming(
                clip.clip_id,
                source_in=ProjectTime.from_milliseconds(new_in),
                source_out=ProjectTime.from_milliseconds(new_out),
                playback_rate=(
                    PlaybackRate(rate.numerator, rate.denominator)
                    if is_video
                    else None
                ),
                audio_level=AudioLevel(volume) if is_video else None,
                audio_muted=muted if is_video else None,
                fit_mode=(
                    FIT_TO_CORE.get(fit_mode) if fit_mode is not None else None
                ),
                brightness=Brightness(brightness) if brightness is not None else None,
                effect_preset=(
                    EFFECT_TO_CORE.get(effect) if effect is not None else None
                ),
            )
        return self._execute_core(command, "클립 속성을 적용했습니다") is not None

    def update_selected_audio(
        self,
        *,
        start_ms: int,
        source_in_ms: int,
        source_out_ms: int,
        volume: int,
        muted: bool,
        fade_in_ms: int,
        fade_out_ms: int,
        ducking: str,
    ) -> bool:
        clip = self.selected_clip
        if (
            len(self.selected_clips) != 1
            or clip is None
            or clip.track not in {TrackKind.MUSIC, TrackKind.NARRATION}
        ):
            self._set_status("편집할 음악 또는 내레이션 클립 하나를 선택하세요")
            return False
        if start_ms < 0:
            self._set_status("오디오 클립 시작 위치는 0보다 작을 수 없습니다")
            return False
        if not 0 <= volume <= 100:
            self._set_status("음량은 0~100% 범위여야 합니다")
            return False
        asset = self.asset_for_clip(clip)
        source_limit = asset.duration_ms if asset is not None else None
        if source_in_ms < 0 or source_out_ms <= source_in_ms or (
            source_limit is not None and source_out_ms > source_limit
        ):
            self._set_status("오디오 원본 사용 구간이 유효하지 않습니다")
            return False
        duration_ms = source_out_ms - source_in_ms
        if fade_in_ms + fade_out_ms > duration_ms:
            self._set_status("페이드 합계가 오디오 클립 길이보다 길 수 없습니다")
            return False
        if ducking not in DUCKING_TO_CORE:
            self._set_status("지원하지 않는 더킹 프리셋입니다")
            return False
        if clip.track is TrackKind.MUSIC and ducking != "꺼짐":
            self._set_status("더킹 강도는 내레이션 클립에서 설정하세요")
            return False

        command = UpdateClipTiming(
            clip.clip_id,
            source_in=ProjectTime.from_milliseconds(source_in_ms),
            source_out=ProjectTime.from_milliseconds(source_out_ms),
            timeline_start=ProjectTime.from_milliseconds(start_ms),
            audio_level=AudioLevel(volume),
            audio_muted=muted,
            fade_in=ProjectTime.from_milliseconds(fade_in_ms),
            fade_out=ProjectTime.from_milliseconds(fade_out_ms),
            ducking=(
                DUCKING_TO_CORE[ducking]
                if clip.track is TrackKind.NARRATION
                else DuckingPreset.OFF
            ),
            history_label="오디오 속성 적용",
        )
        return self._execute_core(command, "오디오 타이밍 속성을 적용했습니다") is not None

    def set_clip_fit(self, fit_mode: str) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("화면 맞춤을 바꿀 시각 클립을 선택하세요")
            return False
        mode = FIT_TO_CORE.get(fit_mode)
        if mode is None:
            self._set_status("지원하지 않는 화면 배치 프리셋입니다")
            return False
        return self._execute_core(
            UpdateVisualProperties(clip.clip_id, fit_mode=mode),
            "화면 배치를 변경했습니다",
        ) is not None

    def rotate_selected_clip(self, degrees: int) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("회전할 영상 또는 사진 클립을 선택하세요")
            return False
        new_value = (clip.rotation + degrees) % 360
        try:
            rotation = UserRotation(new_value)
        except ValueError:
            self._set_status("사용자 회전은 90도 단위여야 합니다")
            return False
        return self._execute_core(
            UpdateVisualProperties(clip.clip_id, user_rotation=rotation),
            "클립을 회전했습니다",
        ) is not None

    def set_clip_effect(self, effect: str) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("효과를 적용할 시각 클립을 선택하세요")
            return False
        preset = EFFECT_TO_CORE.get(effect)
        if preset is None:
            self._set_status("지원하지 않는 시각 효과 프리셋입니다")
            return False
        return self._execute_core(
            UpdateVisualProperties(clip.clip_id, effect_preset=preset),
            "시각 효과를 변경했습니다",
        ) is not None

    def add_text(self, kind: str) -> bool:
        if not self.state.visual_clips:
            self._set_status("텍스트를 추가하려면 먼저 영상이나 사진을 배치하세요")
            return False
        text_kind = TEXT_KIND_TO_CORE.get(kind)
        if text_kind is None:
            self._set_status("지원하지 않는 텍스트 종류입니다")
            return False
        clip_id = self._next_id("text")
        project = self._execute_core(
            AddTextClip(clip_id, text_kind, self.preview_position),
            f"{kind}을 추가했습니다",
        )
        if project is None:
            return False
        self.state.selected_clip_id = clip_id
        self.state.selected_clip_ids = [clip_id]
        self.state.selected_asset_id = None
        self._publish()
        return True

    def update_selected_text(
        self,
        *,
        content: str,
        position: str,
        animation: str,
        duration_ms: int,
        kind: str | None = None,
        start_ms: int | None = None,
        font: str | None = None,
        size: int | None = None,
        bold: bool | None = None,
        color: str | None = None,
        alignment: str | None = None,
    ) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.TEXT:
            self._set_status("편집할 텍스트를 선택하세요")
            return False

        try:
            current = self._media_library.project.clip(clip.clip_id)
        except KeyError:
            self._set_status("텍스트 클립을 찾을 수 없습니다")
            return False
        assert current.text is not None
        requested_kind = kind if kind is not None else clip.text_kind or "캡션"
        requested_font = font if font is not None else clip.text_font
        requested_color = color if color is not None else clip.text_color
        requested_alignment = (
            alignment if alignment is not None else clip.text_alignment
        )
        if (
            requested_kind not in TEXT_KIND_TO_CORE
            or requested_font not in FONT_TO_CORE
            or requested_color not in COLOR_TO_HEX
            or requested_alignment not in ALIGNMENT_TO_CORE
            or position not in POSITION_Y
            or animation not in ANIMATION_TO_CORE
        ):
            self._set_status("지원하지 않는 텍스트 스타일 또는 애니메이션입니다")
            return False
        text = TextOverlay(
            kind=TEXT_KIND_TO_CORE[requested_kind],
            content=content,
            style=TextStyle(
                font_family=FONT_TO_CORE[requested_font],
                size=size if size is not None else clip.text_size,
                bold=bold if bold is not None else clip.text_bold,
                color=COLOR_TO_HEX[requested_color],
                outline_color="#FFFFFF" if requested_color == "검정" else "#000000",
                alignment=ALIGNMENT_TO_CORE[requested_alignment],
            ),
            position=NormalizedPosition(5_000, POSITION_Y[position]),
            animation=ANIMATION_TO_CORE[animation],
        )
        command = UpdateTextProperties(
            clip.clip_id,
            text,
            ProjectTime.from_milliseconds(
                clip.start_ms if start_ms is None else max(0, start_ms)
            ),
            ProjectTime.from_milliseconds(max(500, duration_ms)),
        )
        return self._execute_core(command, "텍스트 속성을 적용했습니다") is not None

    def update_transition_for_selected(self, transition_type: str, duration_ms: int) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("전환 앞쪽의 시각 클립 하나를 선택하세요")
            return False
        index = self.state.visual_clips.index(clip)
        if index >= len(self.state.visual_clips) - 1:
            self._set_status("전환에는 인접한 다음 시각 클립이 필요합니다")
            return False
        max_duration = min(clip.duration_ms, self.state.visual_clips[index + 1].duration_ms) // 2
        if duration_ms < 100 or duration_ms > max_duration:
            self._set_status("전환 길이는 양쪽 클립이 허용하는 범위 안이어야 합니다")
            return False
        next_clip = self.state.visual_clips[index + 1]
        if transition_type != "없음" and transition_type not in TRANSITION_TO_CORE:
            self._set_status("지원하지 않는 전환 프리셋입니다")
            return False
        preset = (
            None if transition_type == "없음" else TRANSITION_TO_CORE[transition_type]
        )
        return self._execute_core(
            UpdateTransition(
                clip.clip_id,
                next_clip.clip_id,
                preset,
                ProjectTime.from_milliseconds(duration_ms),
            ),
            "전환 속성을 적용했습니다",
        ) is not None

    def reset_selected_properties(self) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None:
            self._set_status("기본값으로 되돌릴 클립 하나를 선택하세요")
            return False

        if clip.track is TrackKind.TEXT:
            core_clip = self._media_library.project.clip(clip.clip_id)
            assert core_clip.text is not None
            text = TextOverlay(kind=core_clip.text.kind, content=core_clip.text.content)
            return self._execute_core(
                UpdateTextProperties(
                    clip.clip_id,
                    text,
                    core_clip.timeline_start,
                    core_clip.duration,
                ),
                "텍스트 속성을 기본값으로 복원했습니다",
            ) is not None
        if clip.track is TrackKind.VISUAL:
            asset = self.asset_for_clip(clip)
            audio_fields = asset is not None and asset.kind is MediaKind.VIDEO
            command = UpdateClipTiming(
                clip.clip_id,
                photo_duration=(
                    ProjectTime.from_milliseconds(clip.duration_ms)
                    if asset is not None and asset.kind is MediaKind.PHOTO
                    else None
                ),
                audio_level=AudioLevel() if audio_fields else None,
                audio_muted=False if audio_fields else None,
                fit_mode=FitMode.FIT,
                user_rotation=UserRotation.NONE,
                brightness=Brightness(),
                effect_preset=VisualEffectPreset.NONE,
                history_label="선택 속성 기본값 복원",
            )
        else:
            command = UpdateClipTiming(
                clip.clip_id,
                audio_level=AudioLevel(),
                audio_muted=False,
                fade_in=ProjectTime.zero(),
                fade_out=ProjectTime.zero(),
                ducking=DuckingPreset.OFF,
                history_label="선택 속성 기본값 복원",
            )
        return self._execute_core(command, "선택 속성을 기본값으로 복원했습니다") is not None

    def add_transition(self) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("전환 앞쪽의 시각 클립을 선택하세요")
            return False
        index = self.state.visual_clips.index(clip)
        if index >= len(self.state.visual_clips) - 1:
            self._set_status("전환에는 인접한 다음 시각 클립이 필요합니다")
            return False
        next_clip = self.state.visual_clips[index + 1]
        maximum_ms = min(clip.duration_ms, next_clip.duration_ms) // 2
        duration_ms = min(750, maximum_ms)
        if duration_ms < 100:
            self._set_status("전환을 넣기에는 인접 클립이 너무 짧습니다")
            return False
        return self._execute_core(
            UpdateTransition(
                clip.clip_id,
                next_clip.clip_id,
                TransitionPreset.FADE,
                ProjectTime.from_milliseconds(duration_ms),
            ),
            "페이드 전환을 추가했습니다",
        ) is not None

    def add_recorded_narration(self, source_path: str) -> bool:
        """Add an already verified WAV without deleting it on later project edits."""

        path = Path(source_path).expanduser().resolve(strict=False)
        try:
            with wave.open(str(path), "rb") as stream:
                frames = stream.getnframes()
                sample_rate = stream.getframerate()
                if frames <= 0 or sample_rate <= 0:
                    raise ValueError("WAV 길이가 비어 있습니다.")
        except (OSError, EOFError, ValueError, wave.Error) as error:
            self._set_status(f"녹음 WAV를 프로젝트에 추가할 수 없습니다 · {error}")
            return False
        duration = ProjectTime.from_seconds(Fraction(frames, sample_rate))
        asset_id = self._next_id("media-recording")
        clip_id = self._next_id("narration")
        reference = MediaReference(
            asset_id=asset_id,
            name=path.name,
            source_path=str(path),
            kind=CoreMediaKind.AUDIO,
            duration=duration,
            primary_stream_index=0,
            streams=(
                MediaStream(
                    0,
                    MediaStreamKind.AUDIO,
                    "pcm_s16le",
                    MediaTimeBase(1, sample_rate),
                    duration_ts=frames,
                    sample_rate=sample_rate,
                ),
            ),
        )
        project = self._execute_core(
            AddRecordedNarration(reference, clip_id, self.preview_position),
            "녹음 내레이션을 프로젝트에 추가했습니다",
        )
        if project is None:
            return False
        self.state.selected_clip_id = clip_id
        self.state.selected_clip_ids = [clip_id]
        self.state.selected_asset_id = None
        self._publish()
        return True

    def _set_original_canvas_reference(self) -> bool:
        video_asset: MockAsset | None = None
        photo_asset: MockAsset | None = None
        for clip in self.state.visual_clips:
            asset = self.asset_for_clip(clip)
            if asset is None:
                continue
            if asset.kind is MediaKind.VIDEO and video_asset is None:
                video_asset = asset
            elif asset.kind is MediaKind.PHOTO and photo_asset is None:
                photo_asset = asset
        reference = video_asset or photo_asset
        if reference is None or reference.width is None or reference.height is None:
            return False
        width, height = reference.width, reference.height
        try:
            core_reference = self._media_library.project.media_reference(reference.asset_id)
        except KeyError:
            core_reference = None
        if core_reference is not None:
            primary = next(
                (
                    stream
                    for stream in core_reference.streams
                    if stream.kind is MediaStreamKind.VIDEO
                    and stream.index == core_reference.primary_stream_index
                ),
                None,
            )
            if primary is not None and primary.rotation_degrees in {90, 270}:
                width, height = height, width
        self.state.reference_asset_id = reference.asset_id
        self.state.canvas_width = width
        self.state.canvas_height = height
        return True

    def set_canvas_mode(self, mode: str) -> bool:
        if mode == self.state.canvas_mode and self.state.canvas_width is not None:
            return True
        if mode == "원본 유지" and not self.state.visual_clips:
            self._set_status("원본 유지를 선택하려면 먼저 영상이나 사진을 추가하세요")
            return False

        def operation() -> None:
            self.state.canvas_mode = mode
            if mode == "원본 유지":
                self._set_original_canvas_reference()
            elif mode == "4:3":
                self.state.reference_asset_id = None
                self.state.canvas_width = 1440
                self.state.canvas_height = 1080
            else:
                self.state.reference_asset_id = None
                self.state.canvas_width = 1920
                self.state.canvas_height = 1080

        self._execute_edit("프로젝트 화면 변경", operation)
        return True

    def set_timeline_mode(self, mode: str) -> bool:
        clean_mode = mode.split(" ·", maxsplit=1)[0]
        try:
            selected_mode = TimelineViewMode(clean_mode)
        except ValueError:
            self._set_status("지원하지 않는 타임라인 보기입니다")
            return False
        if self.state.timeline_mode == selected_mode.value:
            return True
        self.state.timeline_mode = selected_mode.value
        self._set_status(f"{selected_mode.value} 보기 · 편집 내용은 유지됩니다")
        self.state_changed.emit()
        return True

    def set_timeline_zoom(self, zoom: int) -> bool:
        try:
            normalised = normalise_timeline_zoom(zoom)
        except TypeError as error:
            self._set_status(str(error))
            return False
        if self.state.timeline_zoom == normalised:
            return True
        self.state.timeline_zoom = normalised
        self._set_status(f"타임라인 확대 {self.state.timeline_zoom}%")
        self.state_changed.emit()
        return True

    def seek(self, position_ms: int) -> None:
        self._seek_preview(ProjectTime.from_milliseconds(position_ms))
        self._set_status(f"재생 위치 {format_time(self.state.playhead_ms)}")
        self.state_changed.emit()

    def step_frame(self, direction: int) -> None:
        position = self._playback.step(self._media_library.project, direction)
        self.state.playhead_ms = ui_milliseconds(position)
        self.state.is_playing = False
        self._set_status("실제 미디어 프레임 경계로 이동했습니다")
        self.state_changed.emit()

    def toggle_playback(self) -> bool:
        project = self._media_library.project
        if project.duration.nanoseconds == 0:
            self._set_status("재생하려면 먼저 타임라인에 미디어를 추가하세요")
            return False
        self.state.is_playing = self._playback.toggle(project)
        self.state.playhead_ms = ui_milliseconds(self._playback.position)
        self._set_status("재생 중" if self.state.is_playing else "일시 정지")
        self.state_changed.emit()
        return True

    def advance_playback(self, elapsed_ms: int | None = None) -> None:
        if not self._playback.is_playing:
            return
        if elapsed_ms is None:
            position = self._playback.advance(self._media_library.project)
        else:
            position = self._playback.advance_elapsed(
                self._media_library.project,
                elapsed_ms * 1_000_000,
            )
        self.state.playhead_ms = ui_milliseconds(position)
        self.state.is_playing = self._playback.is_playing
        if not self.state.is_playing and position == self._media_library.project.duration:
            self.state.status_message = "프로젝트 끝 · 다시 재생하면 처음부터 시작합니다"
        self._publish()

    def toggle_preview_mute(self) -> None:
        self.state.preview_muted = not self.state.preview_muted
        text = "미리 보기 음소거" if self.state.preview_muted else "미리 보기 소리 켜짐"
        self._set_status(f"{text} · 프로젝트 음량은 바뀌지 않습니다")
        self.state_changed.emit()

    def undo(self) -> bool:
        if not self.can_undo:
            self._set_status("실행 취소할 편집 명령이 없습니다")
            return False
        entry = self._history[self._history_position - 1]
        if entry.core:
            try:
                project = self._media_library.undo()
            except CommandError as error:
                self._set_status(f"실행 취소할 수 없습니다 · {error}")
                return False
            self._sync_from_core(project)
            self.state.is_dirty = project != self._saved_project
        else:
            if entry.mock is None:
                self._set_status("실행 취소 이력 형식이 올바르지 않습니다")
                return False
            self.state = deepcopy(entry.mock.before)
        self._history_position -= 1
        self.state.status_message = f"실행 취소: {entry.label}"
        self._publish()
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            self._set_status("다시 실행할 편집 명령이 없습니다")
            return False
        entry = self._history[self._history_position]
        if entry.core:
            try:
                project = self._media_library.redo()
            except CommandError as error:
                self._set_status(f"다시 실행할 수 없습니다 · {error}")
                return False
            self._sync_from_core(project)
            self.state.is_dirty = project != self._saved_project
        else:
            if entry.mock is None:
                self._set_status("다시 실행 이력 형식이 올바르지 않습니다")
                return False
            self.state = deepcopy(entry.mock.after)
        self._history_position += 1
        self.state.status_message = f"다시 실행: {entry.label}"
        self._publish()
        return True

    def configure_export(
        self,
        *,
        path: str,
        preset: str,
        framerate: str,
        quality: str,
    ) -> bool:
        if self.state.export_state in {ExportState.RUNNING, ExportState.CANCELLING}:
            self._set_status("이미 동영상을 저장하고 있습니다")
            return False
        self.state.export_path = path
        self.state.export_preset = preset
        self.state.export_framerate = framerate
        self.state.export_quality = quality
        self.state.export_state = ExportState.CONFIG
        self.export_changed.emit()
        self.state_changed.emit()
        return True

    def open_export_configuration(self) -> bool:
        """Enter export configuration when all visual sources are available."""

        if not self.state.visual_clips:
            self._set_status("동영상 저장에는 하나 이상의 영상 또는 사진이 필요합니다")
            return False
        if any(
            (asset := self.state.assets.get(clip.asset_id or "")) is None
            or asset.status is not AssetStatus.READY
            for clip in (*self.state.visual_clips, *self.state.music_clips)
        ):
            self._set_status("누락되거나 읽을 수 없는 출력 미디어를 먼저 다시 연결하세요")
            return False
        self.state.export_state = ExportState.CONFIG
        self.export_changed.emit()
        self.state_changed.emit()
        return True

    def start_export(self) -> bool:
        if not self.state.visual_clips:
            self._set_status("동영상 저장에는 하나 이상의 영상 또는 사진이 필요합니다")
            return False
        if self.state.export_state in {ExportState.RUNNING, ExportState.CANCELLING}:
            self._set_status("이미 동영상을 저장하고 있습니다")
            return False
        self.state.is_playing = False
        self.state.export_state = ExportState.RUNNING
        self.state.export_progress = 0
        self.state.export_error = None
        self.state.export_error_detail = None
        self.state.export_elapsed_ms = 0
        self.state.export_eta_ms = None
        self.state.export_result_path = None
        self.state.export_stage = "encoding"
        self.state.status_message = "동영상 저장을 시작했습니다"
        self.export_changed.emit()
        self._publish()
        return True

    def update_export_progress(
        self,
        percent: int,
        *,
        elapsed_ms: int,
        eta_ms: int | None,
        verifying: bool = False,
    ) -> None:
        if self.state.export_state is not ExportState.RUNNING:
            return
        self.state.export_progress = max(self.state.export_progress, min(percent, 99))
        self.state.export_elapsed_ms = max(0, elapsed_ms)
        self.state.export_eta_ms = max(0, eta_ms) if eta_ms is not None else None
        self.state.export_stage = "verifying" if verifying else "encoding"
        self.export_changed.emit()
        self._publish()

    def complete_export(self, path: str, *, elapsed_ms: int) -> None:
        self.state.export_state = ExportState.COMPLETE
        self.state.export_progress = 100
        self.state.export_elapsed_ms = max(0, elapsed_ms)
        self.state.export_eta_ms = 0
        self.state.export_result_path = path
        self.state.export_stage = "complete"
        self.state.export_error = None
        self.state.export_error_detail = None
        self.state.status_message = "동영상 저장을 완료했습니다"
        self.export_changed.emit()
        self._publish()

    def fail_export(self, message: str, detail: str | None = None) -> None:
        self.state.export_state = ExportState.FAILED
        self.state.export_error = message
        self.state.export_error_detail = detail
        self.state.export_eta_ms = None
        self.state.export_stage = "failed"
        self.state.status_message = "동영상 저장에 실패했습니다 · 해결 행동을 확인하세요"
        self.export_changed.emit()
        self._publish()

    def complete_export_cancellation(self) -> None:
        self.state.export_state = ExportState.CANCELLED
        self.state.export_eta_ms = None
        self.state.export_stage = "cancelled"
        self.state.status_message = "출력을 취소했습니다 · 불완전 파일을 정리했습니다"
        self.export_changed.emit()
        self._publish()

    def cancel_export(self) -> bool:
        if self.state.export_state is not ExportState.RUNNING:
            return False
        self.state.export_state = ExportState.CANCELLING
        self.state.status_message = "출력 프로세스와 임시 파일을 정리하고 있습니다"
        self.export_changed.emit()
        self._publish()
        return True

    def close_export_result(self) -> None:
        self.state.export_state = ExportState.CLOSED
        self.state.export_progress = 0
        self.state.export_error = None
        self.state.export_error_detail = None
        self.state.export_elapsed_ms = 0
        self.state.export_eta_ms = None
        self.state.export_result_path = None
        self.state.export_stage = "encoding"
        self.export_changed.emit()
        self.state_changed.emit()


def format_time(milliseconds: int) -> str:
    """Format a non-negative mock timeline position as MM:SS.mmm."""

    milliseconds = max(0, milliseconds)
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"
