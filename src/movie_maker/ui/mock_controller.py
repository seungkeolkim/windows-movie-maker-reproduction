"""In-memory command boundary for the interactive product mock-up."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from itertools import count
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from movie_maker.media import (
    ImportedMedia,
    MediaImportReport,
    MediaLibrary,
    MediaRemovalFailure,
)
from movie_maker.project import (
    Canvas,
    Clip,
    CommandError,
    MediaReference,
    PlaybackRate,
    Project,
    ProjectCommand,
    ProjectFileStore,
    ProjectPersistenceError,
    ProjectTime,
    RenameProject,
    TimelineTrack,
)
from movie_maker.project.model import MediaKind as CoreMediaKind
from movie_maker.project.model import TrackKind as CoreTrackKind
from movie_maker.timeline import (
    AddMediaClip,
    DeleteTimelineClip,
    MoveVisualClip,
    SplitClip,
    UpdateClipTiming,
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


class MockController(QObject):
    """Own mock state and expose user intents independently of Qt widgets."""

    state_changed = Signal()
    status_changed = Signal(str)
    export_changed = Signal()

    def __init__(
        self,
        media_library: MediaLibrary | None = None,
        project_store: ProjectFileStore | None = None,
        *,
        clip_id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__()
        self.state = MockProjectState()
        self._media_library = media_library or MediaLibrary.create_default()
        self._project_store = project_store or ProjectFileStore()
        self.last_persistence_error: str | None = None
        self._history: list[_ControllerHistoryEntry] = []
        self._history_position = 0
        self._id_counter = count(1)
        self._clip_id_factory = clip_id_factory
        self._saved_project = self._media_library.project
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
    def media_history_count(self) -> int:
        """Return successful real media commands in this project session."""

        return self._media_library.history_count

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
        self.state_changed.emit()
        self.status_changed.emit(self.state.status_message)

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

    def _reset_history(self) -> None:
        self._history.clear()
        self._history_position = 0

    def new_project(self) -> None:
        project = Project.empty(project_id=str(uuid4()))
        self._media_library.reset(project)
        self.state = MockProjectState(status_message="새 프로젝트를 만들었습니다")
        self._saved_project = project
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
                )
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
                is_real_media=True,
            )

        state = MockProjectState(
            project_name=project.name,
            project_path=path,
            assets=assets,
            canvas_width=project.canvas.width,
            canvas_height=project.canvas.height,
            reference_asset_id=project.canvas.reference_asset_id,
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
                target_lists[CORE_TO_UI_TRACK_KIND[track.kind]].append(
                    MockClip(
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
                    )
                )
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
                    is_real_media=True,
                )
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
                target_lists[ui_track].append(projected)

        self.state.visual_clips = target_lists[TrackKind.VISUAL]
        self.state.music_clips = target_lists[TrackKind.MUSIC]
        self.state.narration_clips = target_lists[TrackKind.NARRATION]
        self.state.text_clips = target_lists[TrackKind.TEXT]
        self.state.project_name = project.name
        self.state.canvas_width = project.canvas.width
        self.state.canvas_height = project.canvas.height
        self.state.reference_asset_id = project.canvas.reference_asset_id

        existing_ids = {clip.clip_id for clip in self.state.all_clips}
        if self.state.selected_clip_id not in existing_ids:
            self.state.selected_clip_id = None
        self.state.selected_clip_ids = [
            clip_id for clip_id in self.state.selected_clip_ids if clip_id in existing_ids
        ]
        if self.state.selected_asset_id not in self.state.assets:
            self.state.selected_asset_id = None
        self.state.playhead_ms = min(
            max(self.state.playhead_ms, 0),
            project.duration.to_milliseconds(),
        )
        visual_ids = {clip.clip_id for clip in self.state.visual_clips}
        self.state.transitions = {
            boundary: value
            for boundary, value in self.state.transitions.items()
            if all(clip_id in visual_ids for clip_id in boundary.split("|"))
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
        self._set_status("프로젝트를 저장했습니다")
        self.state_changed.emit()
        return True

    def open_project(self, path: str) -> bool:
        try:
            project = self._project_store.load(path)
            next_state = self._state_from_project(project, str(Path(path)))
        except (ProjectPersistenceError, ValueError) as error:
            self.last_persistence_error = str(error)
            self._set_status(f"프로젝트 열기 실패 · {error}")
            return False

        self._media_library.reset(project)
        self._saved_project = project
        self.state = next_state
        self._reset_history()
        self._id_counter = count(1)
        self.last_persistence_error = None
        self._publish()
        return True

    def discard_unsaved_changes(self) -> None:
        """Clear the dirty flag through the controller boundary."""

        self.state.is_dirty = False
        self.state_changed.emit()

    def apply_recovery_choice(self, choice: str) -> None:
        """Load the deterministic recovery result selected by the user."""

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
        self.state.selected_clip_id = clip_id
        self.state.selected_clip_ids = [clip_id] if clip_id is not None else []
        self.state.selected_asset_id = None
        if clip is None:
            self._set_status("타임라인 선택을 해제했습니다")
        else:
            if clip.track is TrackKind.VISUAL:
                self.state.playhead_ms = clip.start_ms
            self._set_status(f"{clip.label} 클립 선택")
        self.state_changed.emit()

    def select_clips(self, clip_ids: list[str], active_clip_id: str | None) -> None:
        existing = {clip.clip_id: clip for clip in self.state.all_clips}
        valid_ids = [clip_id for clip_id in clip_ids if clip_id in existing]
        if active_clip_id not in valid_ids:
            active_clip_id = valid_ids[-1] if valid_ids else None
        self.state.selected_clip_ids = valid_ids
        self.state.selected_clip_id = active_clip_id
        self.state.selected_asset_id = None
        if not valid_ids:
            self._set_status("타임라인 선택을 해제했습니다")
        elif len(valid_ids) == 1 and active_clip_id is not None:
            clip = existing[active_clip_id]
            if clip.track is TrackKind.VISUAL:
                self.state.playhead_ms = clip.start_ms
            self._set_status(f"{clip.label} 클립 선택")
        else:
            self._set_status(f"클립 {len(valid_ids)}개 선택 · 공통 명령만 사용할 수 있습니다")
        self.state_changed.emit()

    def add_selected_to_timeline(self, *, narration: bool = False) -> bool:
        asset = self.selected_asset
        if asset is None:
            self._set_status("먼저 보관함에서 미디어를 선택하세요")
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
            ),
            "타임라인에 미디어를 추가했습니다",
        )
        if result is None:
            return False
        added = result.clip(clip_id)
        self.state.selected_asset_id = None
        self.state.selected_clip_id = clip_id
        self.state.selected_clip_ids = [clip_id]
        self.state.playhead_ms = added.timeline_start.to_milliseconds()
        self._publish()
        return True

    def asset_usage_count(self, asset_id: str) -> int:
        return sum(clip.asset_id == asset_id for clip in self.state.all_clips)

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
        self.state.playhead_ms = missing_clip.start_ms
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

        def operation() -> None:
            asset.proxy_enabled = enabling
            asset.proxy_status = "준비됨 · 목업 대체본" if enabling else "사용 안 함"

        label = "프록시 사용" if enabling else "프록시 사용 해제"
        self._execute_edit(label, operation)
        return True

    def move_selected_visual(self, offset: int) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("이동할 영상 또는 사진 클립을 선택하세요")
            return False
        index = self.state.visual_clips.index(clip)
        target = index + offset
        if target < 0 or target >= len(self.state.visual_clips):
            self._set_status("선택한 클립은 더 이동할 수 없습니다")
            return False

        direction = "앞으로" if offset < 0 else "뒤로"
        return (
            self._execute_core(
                MoveVisualClip(
                    clip.clip_id,
                    target,
                    history_label=f"클립 {direction} 이동",
                ),
                f"클립을 {direction} 이동했습니다",
            )
            is not None
        )

    def delete_selected_clip(self) -> bool:
        selected = self.selected_clips
        if not selected:
            self._set_status("삭제할 타임라인 클립을 선택하세요")
            return False
        selected_ids = {clip.clip_id for clip in selected}

        if len(selected) == 1:
            clip = selected[0]
            if self._execute_core(
                DeleteTimelineClip(clip.clip_id),
                "클립을 삭제했습니다 · 보관함과 원본 파일은 유지됩니다",
            ) is None:
                return False
            self.state.selected_clip_id = None
            self.state.selected_clip_ids.clear()
            self.state.playhead_ms = min(
                self.state.playhead_ms,
                self._media_library.project.duration.to_milliseconds(),
            )
            self._publish()
            return True

        def operation() -> None:
            for track in TrackKind:
                clips = self.clips_for_track(track)
                clips[:] = [clip for clip in clips if clip.clip_id not in selected_ids]
            self.state.selected_clip_id = None
            self.state.selected_clip_ids.clear()
            valid_ids = {item.clip_id for item in self.state.visual_clips}
            self.state.transitions = {
                boundary: value
                for boundary, value in self.state.transitions.items()
                if all(item in valid_ids for item in boundary.split("|"))
            }

        label = "클립 삭제" if len(selected) == 1 else f"클립 {len(selected)}개 삭제"
        self._execute_edit(label, operation)
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
                ProjectTime.from_milliseconds(self.state.playhead_ms),
                trailing_clip_id,
            ),
            "재생 위치에서 클립을 분할했습니다",
        )
        if result is None:
            return False
        trailing = result.clip(trailing_clip_id)
        self.state.selected_clip_id = trailing_clip_id
        self.state.selected_clip_ids = [trailing_clip_id]
        self.state.playhead_ms = trailing.timeline_start.to_milliseconds()
        self._publish()
        return True

    def duplicate_selected_clip(self) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None:
            self._set_status("복제할 클립을 선택하세요")
            return False
        duplicate = deepcopy(clip)
        duplicate.clip_id = self._next_id("clip")
        duplicate.label = f"{clip.label} 복제본"

        def operation() -> None:
            clips = self.clips_for_track(clip.track)
            index = clips.index(clip)
            clips.insert(index + 1, duplicate)
            self.state.selected_clip_id = duplicate.clip_id
            self.state.selected_clip_ids = [duplicate.clip_id]

        self._execute_edit("클립 복제", operation)
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
            )
        if self._execute_core(command, "클립 속성을 적용했습니다") is None:
            return False
        updated = self.selected_clip
        if updated is None:
            return False
        updated.volume = volume
        updated.muted = muted
        if fit_mode is not None and updated.track is TrackKind.VISUAL:
            updated.fit_mode = fit_mode
        if effect is not None and updated.track is TrackKind.VISUAL:
            updated.effect = effect
        self._publish()
        return True

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

        command = UpdateClipTiming(
            clip.clip_id,
            source_in=ProjectTime.from_milliseconds(source_in_ms),
            source_out=ProjectTime.from_milliseconds(source_out_ms),
            timeline_start=ProjectTime.from_milliseconds(start_ms),
            history_label="오디오 속성 적용",
        )
        if self._execute_core(command, "오디오 타이밍 속성을 적용했습니다") is None:
            return False
        updated = self.selected_clip
        if updated is None:
            return False
        updated.volume = min(100, max(0, volume))
        updated.muted = muted
        updated.fade_in_ms = fade_in_ms
        updated.fade_out_ms = fade_out_ms
        updated.ducking = ducking
        self._publish()
        return True

    def set_clip_fit(self, fit_mode: str) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("화면 맞춤을 바꿀 시각 클립을 선택하세요")
            return False
        self._execute_edit("화면 배치 변경", lambda: setattr(clip, "fit_mode", fit_mode))
        return True

    def rotate_selected_clip(self, degrees: int) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("회전할 영상 또는 사진 클립을 선택하세요")
            return False
        new_value = (clip.rotation + degrees) % 360
        self._execute_edit("클립 회전", lambda: setattr(clip, "rotation", new_value))
        return True

    def set_clip_effect(self, effect: str) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None or clip.track is not TrackKind.VISUAL:
            self._set_status("효과를 적용할 시각 클립을 선택하세요")
            return False
        self._execute_edit("시각 효과 변경", lambda: setattr(clip, "effect", effect))
        return True

    def add_text(self, kind: str) -> bool:
        if not self.state.visual_clips:
            self._set_status("텍스트를 추가하려면 먼저 영상이나 사진을 배치하세요")
            return False
        if kind == "제목":
            start_ms = 0
        elif kind == "크레딧":
            start_ms = max(0, self.state.total_duration_ms - 3_000)
        else:
            start_ms = min(self.state.playhead_ms, max(0, self.state.total_duration_ms - 3_000))
        clip = MockClip(
            self._next_id("text"),
            TrackKind.TEXT,
            None,
            f"{kind} · 텍스트를 입력하세요",
            start_ms,
            3_000,
            text_kind=kind,
        )

        def operation() -> None:
            self.state.text_clips.append(clip)
            self.state.selected_clip_id = clip.clip_id
            self.state.selected_clip_ids = [clip.clip_id]
            self.state.selected_asset_id = None

        self._execute_edit(f"{kind} 추가", operation)
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

        def operation() -> None:
            if kind is not None:
                clip.text_kind = kind
            if start_ms is not None:
                clip.start_ms = max(0, start_ms)
            clip.text_content = content
            if font is not None:
                clip.text_font = font
            if size is not None:
                clip.text_size = min(96, max(12, size))
            if bold is not None:
                clip.text_bold = bold
            if color is not None:
                clip.text_color = color
            if alignment is not None:
                clip.text_alignment = alignment
            clip.text_position = position
            clip.text_animation = animation
            clip.duration_ms = max(500, duration_ms)
            shown = content.strip() or "텍스트를 입력하세요"
            clip.label = f"{clip.text_kind} · {shown}"

        self._execute_edit("텍스트 속성 적용", operation)
        return True

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
        boundary = f"{clip.clip_id}|{self.state.visual_clips[index + 1].clip_id}"
        value = "없음" if transition_type == "없음" else f"{transition_type} · {duration_ms / 1_000:g}초"

        def operation() -> None:
            if transition_type == "없음":
                self.state.transitions.pop(boundary, None)
            else:
                self.state.transitions[boundary] = value

        self._execute_edit("전환 속성 적용", operation)
        return True

    def reset_selected_properties(self) -> bool:
        clip = self.selected_clip
        if len(self.selected_clips) != 1 or clip is None:
            self._set_status("기본값으로 되돌릴 클립 하나를 선택하세요")
            return False

        def operation() -> None:
            clip.volume = 100
            clip.muted = False
            clip.fit_mode = "맞춤"
            clip.rotation = 0
            clip.effect = "없음"
            clip.fade_in_ms = 0
            clip.fade_out_ms = 0
            clip.ducking = "꺼짐"
            clip.text_font = "맑은 고딕"
            clip.text_size = 32
            clip.text_bold = True
            clip.text_color = "흰색"
            clip.text_alignment = "가운데"
            clip.text_position = "아래"
            clip.text_animation = "없음"

        self._execute_edit("선택 속성 기본값 복원", operation)
        return True

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
        boundary = f"{clip.clip_id}|{next_clip.clip_id}"
        self._execute_edit(
            "페이드 전환 추가",
            lambda: self.state.transitions.__setitem__(boundary, "페이드 · 0.75초"),
        )
        return True

    def add_recorded_narration(self) -> None:
        recording = MockAsset(
            "media-recording",
            "목업 내레이션.wav",
            MediaKind.AUDIO,
            5_000,
            None,
            None,
            "#9e6b35",
            r"C:\MockMedia\목업 내레이션.wav",
        )
        clip = MockClip(
            self._next_id("narration"),
            TrackKind.NARRATION,
            "media-recording",
            "목업 내레이션",
            self.state.playhead_ms,
            5_000,
            source_out_ms=5_000,
            volume=90,
        )

        def operation() -> None:
            self.state.assets.setdefault(recording.asset_id, recording)
            self.state.narration_clips.append(clip)
            self.state.selected_clip_id = clip.clip_id
            self.state.selected_clip_ids = [clip.clip_id]
            self.state.selected_asset_id = None

        self._execute_edit("내레이션 녹음 추가", operation)

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
        self.state.reference_asset_id = reference.asset_id
        self.state.canvas_width = reference.width
        self.state.canvas_height = reference.height
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

    def set_timeline_mode(self, mode: str) -> None:
        self.state.timeline_mode = mode
        self._set_status(f"{mode} 보기 · 편집 내용은 유지됩니다")
        self.state_changed.emit()

    def set_timeline_zoom(self, zoom: int) -> None:
        self.state.timeline_zoom = min(200, max(50, zoom))
        self._set_status(f"타임라인 확대 {self.state.timeline_zoom}%")
        self.state_changed.emit()

    def seek(self, position_ms: int) -> None:
        self.state.is_playing = False
        self.state.playhead_ms = min(max(position_ms, 0), self.state.total_duration_ms)
        self._set_status(f"재생 위치 {format_time(self.state.playhead_ms)}")
        self.state_changed.emit()

    def step_frame(self, direction: int) -> None:
        self.seek(self.state.playhead_ms + direction * 33)
        self._set_status("목업 프레임 이동 · 30fps 기준")

    def toggle_playback(self) -> bool:
        total = self.state.total_duration_ms
        if total == 0:
            self._set_status("재생하려면 먼저 타임라인에 미디어를 추가하세요")
            return False
        if self.state.playhead_ms >= total and not self.state.is_playing:
            self.state.playhead_ms = 0
        self.state.is_playing = not self.state.is_playing
        self._set_status("재생 중 · 목업" if self.state.is_playing else "일시 정지")
        self.state_changed.emit()
        return True

    def advance_playback(self, elapsed_ms: int = 100) -> None:
        if not self.state.is_playing:
            return
        self.state.playhead_ms += elapsed_ms
        if self.state.playhead_ms >= self.state.total_duration_ms:
            self.state.playhead_ms = self.state.total_duration_ms
            self.state.is_playing = False
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
    ) -> None:
        self.state.export_path = path
        self.state.export_preset = preset
        self.state.export_framerate = framerate
        self.state.export_quality = quality
        self.state.export_state = ExportState.CONFIG
        self.export_changed.emit()
        self.state_changed.emit()

    def open_export_configuration(self) -> bool:
        """Enter the export configuration state when the project can be exported."""

        if not self.state.visual_clips:
            self._set_status("동영상 저장에는 하나 이상의 영상 또는 사진이 필요합니다")
            return False
        self.state.export_state = ExportState.CONFIG
        self.export_changed.emit()
        self.state_changed.emit()
        return True

    def start_export(self) -> bool:
        if not self.state.visual_clips:
            self._set_status("동영상 저장에는 하나 이상의 영상 또는 사진이 필요합니다")
            return False
        self.state.is_playing = False
        self.state.export_state = ExportState.RUNNING
        self.state.export_progress = 0
        self.state.export_error = None
        self.state.status_message = "동영상 저장 준비 중 · 목업"
        self.export_changed.emit()
        self._publish()
        return True

    def advance_export(self) -> None:
        if self.state.export_state is ExportState.CANCELLING:
            self.state.export_state = ExportState.CANCELLED
            self.state.status_message = "출력을 취소했습니다 · 불완전 파일 없음 · 목업"
        elif self.state.export_state is ExportState.RUNNING:
            self.state.export_progress = min(100, self.state.export_progress + 5)
            if self.state.pending_export_error and self.state.export_progress >= 25:
                self.state.export_state = ExportState.FAILED
                self.state.export_error = self.state.pending_export_error
                self.state.pending_export_error = None
                self.state.status_message = "동영상 저장에 실패했습니다 · 해결 행동을 확인하세요"
            elif self.state.export_progress >= 100:
                self.state.export_state = ExportState.COMPLETE
                self.state.status_message = "동영상 저장 완료 · 목업 파일"
        self.export_changed.emit()
        self._publish()

    def cancel_export(self) -> bool:
        if self.state.export_state is not ExportState.RUNNING:
            return False
        self.state.export_state = ExportState.CANCELLING
        self.state.status_message = "출력 취소 요청을 정리하고 있습니다 · 목업"
        self.export_changed.emit()
        self._publish()
        return True

    def reserve_export_failure(self, reason: str) -> None:
        self.state.pending_export_error = reason
        self._set_status(f"다음 출력 실패 예약: {reason} · 목업 상태")
        self.state_changed.emit()

    def close_export_result(self) -> None:
        self.state.export_state = ExportState.CLOSED
        self.state.export_progress = 0
        self.state.export_error = None
        self.export_changed.emit()
        self.state_changed.emit()


def format_time(milliseconds: int) -> str:
    """Format a non-negative mock timeline position as MM:SS.mmm."""

    milliseconds = max(0, milliseconds)
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"
