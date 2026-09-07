"""Project-backed media library with partial-success batch imports."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from uuid import uuid4

from movie_maker.media.analysis import (
    FfprobeAnalyzer,
    MediaAnalysis,
    MediaAnalysisFailure,
    MediaAnalysisResult,
    MediaAnalysisSuccess,
    canonical_source_key,
    canonical_source_path,
)
from movie_maker.media.thumbnail import (
    FfmpegThumbnailer,
    ThumbnailErrorCode,
    ThumbnailFailure,
    ThumbnailResult,
    ThumbnailSuccess,
)
from movie_maker.project import (
    CommandError,
    CommandExecutor,
    HistoryEntry,
    InsertMediaReference,
    MediaKind,
    MediaReference,
    Project,
    ProjectCommand,
    ProjectValidationError,
    RemoveMediaReference,
)


class MediaAnalyzer(Protocol):
    """Analyze one selected source path."""

    def analyze(self, source_path: str | os.PathLike[str]) -> MediaAnalysisResult: ...


class Thumbnailer(Protocol):
    """Create one transient visual thumbnail."""

    def create(self, analysis: MediaAnalysis) -> ThumbnailResult: ...


class MediaImportErrorCode(str, Enum):
    """Analysis and command failures exposed by a batch import."""

    SOURCE_NOT_FOUND = "source_not_found"
    UNSUPPORTED_TYPE = "unsupported_type"
    FFPROBE_NOT_FOUND = "ffprobe_not_found"
    TIMEOUT = "timeout"
    PROCESS_FAILED = "process_failed"
    INVALID_JSON = "invalid_json"
    INVALID_MEDIA = "invalid_media"
    COMMAND_REJECTED = "command_rejected"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ImportedMedia:
    """One reference committed through the W-01 command boundary."""

    reference: MediaReference
    thumbnail: ThumbnailSuccess | None = None


@dataclass(frozen=True, slots=True)
class MediaImportFailure:
    """A per-file import failure that did not roll back other successes."""

    source_path: str
    code: MediaImportErrorCode
    message: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DuplicateMedia:
    """A path skipped because its source is already represented."""

    source_path: str
    existing_asset_id: str
    message: str = "이미 보관함에 있는 파일입니다."


@dataclass(frozen=True, slots=True)
class MediaImportReport:
    """Complete immutable outcome for one multi-file user intent."""

    imported: tuple[ImportedMedia, ...] = ()
    failures: tuple[MediaImportFailure, ...] = ()
    duplicates: tuple[DuplicateMedia, ...] = ()
    thumbnail_failures: tuple[ThumbnailFailure, ...] = ()
    cancelled: bool = False


class MediaRemovalErrorCode(str, Enum):
    """Stable reasons an item cannot be removed from the project library."""

    NOT_FOUND = "not_found"
    IN_USE = "in_use"
    COMMAND_REJECTED = "command_rejected"


@dataclass(frozen=True, slots=True)
class MediaRemovalSuccess:
    """A project reference and transient cache removed successfully."""

    reference: MediaReference


@dataclass(frozen=True, slots=True)
class MediaRemovalFailure:
    """A rejected removal that leaves the project and caches unchanged."""

    asset_id: str
    code: MediaRemovalErrorCode
    message: str
    usage_count: int = 0


type MediaRemovalResult = MediaRemovalSuccess | MediaRemovalFailure


def _new_asset_id() -> str:
    return f"media-{uuid4()}"


def _new_project() -> Project:
    return Project.empty(project_id=f"project-{uuid4()}")


class MediaLibrary:
    """Coordinate analysis, command commits, duplicate checks, and thumbnails."""

    def __init__(
        self,
        executor: CommandExecutor,
        analyzer: MediaAnalyzer,
        thumbnailer: Thumbnailer | None = None,
        *,
        asset_id_factory: Callable[[], str] = _new_asset_id,
    ) -> None:
        self._executor = executor
        self._analyzer = analyzer
        self._thumbnailer = thumbnailer
        self._asset_id_factory = asset_id_factory
        self._thumbnails: dict[str, ThumbnailSuccess] = {}
        self._thumbnail_failures: dict[str, ThumbnailFailure] = {}

    @classmethod
    def create_default(cls) -> MediaLibrary:
        """Create the runtime service with local FFmpeg-family tools."""

        return cls(CommandExecutor(_new_project()), FfprobeAnalyzer(), FfmpegThumbnailer())

    @property
    def project(self) -> Project:
        return self._executor.project

    @property
    def history_count(self) -> int:
        return self._executor.history_count

    @property
    def history(self) -> tuple[HistoryEntry, ...]:
        return self._executor.history

    @property
    def history_position(self) -> int:
        return self._executor.history_position

    @property
    def can_undo(self) -> bool:
        return self._executor.can_undo

    @property
    def can_redo(self) -> bool:
        return self._executor.can_redo

    @property
    def undo_label(self) -> str | None:
        return self._executor.undo_label

    @property
    def redo_label(self) -> str | None:
        return self._executor.redo_label

    def execute(self, command: ProjectCommand) -> Project:
        """Commit one project command in the shared media and timeline session."""

        return self._executor.execute(command)

    def undo(self) -> Project:
        """Undo the previous shared project command."""

        return self._executor.undo()

    def redo(self) -> Project:
        """Redo the next shared project command."""

        return self._executor.redo()

    def discard_redo(self) -> None:
        """Discard the shared redo branch after a non-core mock edit."""

        self._executor.discard_redo()

    def contains(self, asset_id: str) -> bool:
        return any(media.asset_id == asset_id for media in self.project.media)

    def thumbnail(self, asset_id: str) -> ThumbnailSuccess | None:
        return self._thumbnails.get(asset_id)

    def thumbnail_failure(self, asset_id: str) -> ThumbnailFailure | None:
        return self._thumbnail_failures.get(asset_id)

    def reset(self, project: Project | None = None) -> None:
        """Start a new command session and discard only regenerable thumbnails."""

        self._executor = CommandExecutor(project or _new_project())
        self._thumbnails.clear()
        self._thumbnail_failures.clear()

    def import_paths(self, source_paths: Sequence[str | os.PathLike[str]]) -> MediaImportReport:
        """Import every valid path independently and keep earlier successes."""

        if not source_paths:
            return MediaImportReport(cancelled=True)

        imported: list[ImportedMedia] = []
        failures: list[MediaImportFailure] = []
        duplicates: list[DuplicateMedia] = []
        thumbnail_failures: list[ThumbnailFailure] = []
        existing_paths = self._existing_paths()

        for source_path in source_paths:
            try:
                normalized_path = canonical_source_path(source_path)
                source_key = canonical_source_key(normalized_path)
            except (OSError, RuntimeError, ValueError) as error:
                failures.append(
                    MediaImportFailure(
                        str(source_path),
                        MediaImportErrorCode.SOURCE_NOT_FOUND,
                        "파일 경로를 확인할 수 없습니다.",
                        str(error),
                    )
                )
                continue

            existing_asset_id = existing_paths.get(source_key)
            if existing_asset_id is not None:
                duplicates.append(DuplicateMedia(normalized_path, existing_asset_id))
                continue

            try:
                analysis_result = self._analyzer.analyze(normalized_path)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                failures.append(
                    MediaImportFailure(
                        normalized_path,
                        MediaImportErrorCode.INTERNAL_ERROR,
                        "미디어 분석 중 예기치 않은 오류가 발생했습니다.",
                        str(error),
                    )
                )
                continue
            if isinstance(analysis_result, MediaAnalysisFailure):
                failures.append(_analysis_failure(analysis_result))
                continue
            if not isinstance(analysis_result, MediaAnalysisSuccess):
                failures.append(
                    MediaImportFailure(
                        normalized_path,
                        MediaImportErrorCode.INTERNAL_ERROR,
                        "미디어 분석기가 올바른 결과를 반환하지 않았습니다.",
                    )
                )
                continue

            analysis = analysis_result.analysis
            asset_id = self._asset_id_factory()
            try:
                reference = analysis.to_media_reference(asset_id)
                self._executor.execute(InsertMediaReference(reference))
            except (CommandError, ProjectValidationError, TypeError, ValueError) as error:
                failures.append(
                    MediaImportFailure(
                        normalized_path,
                        MediaImportErrorCode.COMMAND_REJECTED,
                        "분석 결과를 프로젝트에 추가하지 못했습니다.",
                        str(error),
                    )
                )
                continue

            thumbnail: ThumbnailSuccess | None = None
            if self._thumbnailer is not None and analysis.kind in {
                MediaKind.VIDEO,
                MediaKind.PHOTO,
            }:
                thumbnail_result = self._create_thumbnail(analysis)
                if isinstance(thumbnail_result, ThumbnailSuccess):
                    thumbnail = thumbnail_result
                    self._thumbnails[asset_id] = thumbnail_result
                else:
                    thumbnail_failures.append(thumbnail_result)
                    self._thumbnail_failures[asset_id] = thumbnail_result
            imported.append(ImportedMedia(reference, thumbnail))
            existing_paths[source_key] = asset_id

        return MediaImportReport(
            imported=tuple(imported),
            failures=tuple(failures),
            duplicates=tuple(duplicates),
            thumbnail_failures=tuple(thumbnail_failures),
        )

    def remove(self, asset_id: str) -> MediaRemovalResult:
        """Remove one unused reference while leaving its source file untouched."""

        try:
            reference = self.project.media_reference(asset_id)
        except KeyError:
            return MediaRemovalFailure(
                asset_id,
                MediaRemovalErrorCode.NOT_FOUND,
                "보관함 항목을 찾을 수 없습니다.",
            )

        usage_count = sum(
            clip.asset_id == asset_id
            for track in self.project.tracks
            for clip in track.clips
        )
        if usage_count:
            return MediaRemovalFailure(
                asset_id,
                MediaRemovalErrorCode.IN_USE,
                "타임라인에서 사용 중인 미디어는 제거할 수 없습니다.",
                usage_count,
            )

        try:
            self._executor.execute(RemoveMediaReference(asset_id, expected=reference))
        except CommandError as error:
            return MediaRemovalFailure(
                asset_id,
                MediaRemovalErrorCode.COMMAND_REJECTED,
                str(error),
            )
        self._thumbnails.pop(asset_id, None)
        self._thumbnail_failures.pop(asset_id, None)
        return MediaRemovalSuccess(reference)

    def _existing_paths(self) -> dict[str, str]:
        paths: dict[str, str] = {}
        for media in self.project.media:
            try:
                paths[canonical_source_key(media.source_path)] = media.asset_id
            except (OSError, RuntimeError, ValueError):
                continue
        return paths

    def _create_thumbnail(self, analysis: MediaAnalysis) -> ThumbnailResult:
        if self._thumbnailer is None:
            raise RuntimeError("Thumbnail creation requires a thumbnailer.")
        try:
            return self._thumbnailer.create(analysis)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return ThumbnailFailure(
                analysis.source_path,
                code=ThumbnailErrorCode.PROCESS_FAILED,
                message="썸네일 생성 중 오류가 발생해 기본 아이콘을 사용합니다.",
                detail=str(error),
            )


def _analysis_failure(failure: MediaAnalysisFailure) -> MediaImportFailure:
    return MediaImportFailure(
        source_path=failure.source_path,
        code=MediaImportErrorCode(failure.code.value),
        message=failure.message,
        detail=failure.detail,
    )
