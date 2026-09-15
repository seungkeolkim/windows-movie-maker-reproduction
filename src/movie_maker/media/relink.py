"""Analyze and classify replacement sources before one atomic relink command."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from movie_maker.media.analysis import MediaAnalysis, MediaAnalysisFailure, MediaAnalysisSuccess
from movie_maker.media.library import MediaAnalyzer
from movie_maker.project import (
    CommandError,
    CommandExecutor,
    MediaReference,
    ReplaceMediaReference,
)


class RelinkMatch(str, Enum):
    EXACT = "exact"
    CONFIRM = "confirm"
    MISMATCH = "mismatch"


class RelinkErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    ANALYSIS_FAILED = "analysis_failed"
    MISMATCH = "mismatch"
    CONFIRMATION_REQUIRED = "confirmation_required"
    COMMAND_REJECTED = "command_rejected"


@dataclass(frozen=True, slots=True)
class RelinkComparison:
    match: RelinkMatch
    differences: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelinkSuccess:
    previous: MediaReference
    replacement: MediaReference
    comparison: RelinkComparison


@dataclass(frozen=True, slots=True)
class RelinkFailure:
    code: RelinkErrorCode
    message: str
    comparison: RelinkComparison | None = None
    detail: str | None = None


type RelinkResult = RelinkSuccess | RelinkFailure


@dataclass(frozen=True, slots=True)
class FolderRelinkCandidate:
    asset_id: str
    candidate_path: str
    comparison: RelinkComparison


@dataclass(frozen=True, slots=True)
class FolderRelinkReport:
    candidates: tuple[FolderRelinkCandidate, ...]
    unresolved_asset_ids: tuple[str, ...]
    conflicting_asset_ids: tuple[str, ...]


class MediaRelinker:
    def __init__(self, executor: CommandExecutor, analyzer: MediaAnalyzer) -> None:
        self._executor = executor
        self._analyzer = analyzer

    def inspect(
        self, asset_id: str, candidate_path: str
    ) -> tuple[MediaAnalysis, RelinkComparison] | RelinkFailure:
        try:
            expected = self._executor.project.media_reference(asset_id)
        except KeyError:
            return RelinkFailure(RelinkErrorCode.NOT_FOUND, "다시 연결할 미디어를 찾을 수 없습니다.")
        result = self._analyzer.analyze(candidate_path)
        if isinstance(result, MediaAnalysisFailure):
            return RelinkFailure(
                RelinkErrorCode.ANALYSIS_FAILED,
                result.message,
                detail=result.detail,
            )
        if not isinstance(result, MediaAnalysisSuccess):
            return RelinkFailure(RelinkErrorCode.ANALYSIS_FAILED, "분석 결과가 올바르지 않습니다.")
        return result.analysis, compare_relink(expected, result.analysis)

    def relink(self, asset_id: str, candidate_path: str, *, confirmed: bool = False) -> RelinkResult:
        inspected = self.inspect(asset_id, candidate_path)
        if isinstance(inspected, RelinkFailure):
            return inspected
        analysis, comparison = inspected
        return self.apply_inspected(asset_id, analysis, comparison, confirmed=confirmed)

    def apply_inspected(
        self,
        asset_id: str,
        analysis: MediaAnalysis,
        comparison: RelinkComparison,
        *,
        confirmed: bool = False,
    ) -> RelinkResult:
        """Commit a previously analyzed candidate without launching a second probe."""

        if comparison.match is RelinkMatch.MISMATCH:
            return RelinkFailure(
                RelinkErrorCode.MISMATCH,
                "선택한 파일은 저장된 원본과 일치하지 않습니다.",
                comparison,
            )
        if comparison.match is RelinkMatch.CONFIRM and not confirmed:
            return RelinkFailure(
                RelinkErrorCode.CONFIRMATION_REQUIRED,
                "원본과 차이가 있어 사용자 확인이 필요합니다.",
                comparison,
            )
        expected = self._executor.project.media_reference(asset_id)
        analyzed = analysis.to_media_reference(asset_id)
        replacement = replace(analyzed, name=expected.name)
        try:
            self._executor.execute(ReplaceMediaReference(replacement, expected=expected))
        except CommandError as error:
            return RelinkFailure(RelinkErrorCode.COMMAND_REJECTED, str(error), comparison)
        return RelinkSuccess(expected, replacement, comparison)

    def inspect_folder(
        self,
        asset_ids: Sequence[str],
        folder_path: str,
    ) -> FolderRelinkReport:
        """Analyze direct same-name candidates without changing the project.

        A source is never assigned to more than one asset. Confirm-level matches remain
        explicit candidates for the caller instead of being silently committed.
        """

        folder = Path(folder_path)
        if not folder.is_dir():
            return FolderRelinkReport((), tuple(asset_ids), ())
        by_name: dict[str, list[Path]] = {}
        try:
            children = tuple(folder.iterdir())
        except OSError:
            return FolderRelinkReport((), tuple(asset_ids), ())
        for child in children:
            if child.name.startswith(".") or not child.is_file():
                continue
            by_name.setdefault(child.name.casefold(), []).append(child)

        candidates: list[FolderRelinkCandidate] = []
        unresolved: list[str] = []
        conflicts: list[str] = []
        claimed: set[Path] = set()
        for asset_id in asset_ids:
            try:
                expected = self._executor.project.media_reference(asset_id)
            except KeyError:
                unresolved.append(asset_id)
                continue
            paths = by_name.get(Path(expected.source_path).name.casefold(), [])
            if len(paths) != 1:
                (conflicts if paths else unresolved).append(asset_id)
                continue
            resolved = paths[0].resolve(strict=False)
            if resolved in claimed:
                conflicts.append(asset_id)
                continue
            inspected = self.inspect(asset_id, str(resolved))
            if isinstance(inspected, RelinkFailure):
                unresolved.append(asset_id)
                continue
            _analysis, comparison = inspected
            if comparison.match is RelinkMatch.MISMATCH:
                unresolved.append(asset_id)
                continue
            claimed.add(resolved)
            candidates.append(FolderRelinkCandidate(asset_id, str(resolved), comparison))
        return FolderRelinkReport(tuple(candidates), tuple(unresolved), tuple(conflicts))


def compare_relink(expected: MediaReference, candidate: MediaAnalysis) -> RelinkComparison:
    if expected.kind is not candidate.kind:
        return RelinkComparison(RelinkMatch.MISMATCH, ("미디어 종류가 다릅니다.",))

    differences: list[str] = []
    severe = False
    if expected.width != candidate.width or expected.height != candidate.height:
        differences.append("화면 크기가 다릅니다.")
    if expected.duration != candidate.duration:
        differences.append("재생 길이가 다릅니다.")
        if expected.duration is not None and candidate.duration is not None:
            tolerance = max(100_000_000, expected.duration.nanoseconds // 100)
            severe = abs(expected.duration.nanoseconds - candidate.duration.nanoseconds) > tolerance

    expected_streams = tuple(
        (stream.kind, stream.codec_name, stream.sample_rate) for stream in expected.streams
    )
    candidate_streams = tuple(
        (stream.kind, stream.codec_name, stream.sample_rate) for stream in candidate.streams
    )
    if expected_streams != candidate_streams:
        differences.append("스트림 구성이 다릅니다.")
    if severe:
        return RelinkComparison(RelinkMatch.MISMATCH, tuple(differences))
    if differences:
        return RelinkComparison(RelinkMatch.CONFIRM, tuple(differences))
    return RelinkComparison(RelinkMatch.EXACT, ())
