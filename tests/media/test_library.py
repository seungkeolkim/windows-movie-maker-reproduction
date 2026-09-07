from dataclasses import replace
from pathlib import Path

from movie_maker.media import (
    DuplicateMedia,
    ImportedMedia,
    MediaAnalysis,
    MediaAnalysisErrorCode,
    MediaAnalysisFailure,
    MediaAnalysisSuccess,
    MediaImportErrorCode,
    MediaLibrary,
    MediaRemovalErrorCode,
    MediaRemovalFailure,
    MediaRemovalSuccess,
    ThumbnailErrorCode,
    ThumbnailFailure,
    ThumbnailSuccess,
)
from movie_maker.project import (
    Clip,
    CommandExecutor,
    FrameRate,
    InsertClip,
    InsertMediaReference,
    MediaKind,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    Project,
    ProjectTime,
    RemoveMediaReference,
    TrackKind,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nthumbnail-data"


def _analysis(source_path: str, kind: MediaKind) -> MediaAnalysis:
    stream_kind = (
        MediaStreamKind.AUDIO if kind is MediaKind.AUDIO else MediaStreamKind.VIDEO
    )
    extension = {MediaKind.VIDEO: ".mp4", MediaKind.PHOTO: ".png", MediaKind.AUDIO: ".wav"}[
        kind
    ]
    path = Path(source_path).with_suffix(extension)
    stream = MediaStream(
        index=0,
        kind=stream_kind,
        codec_name="pcm_s16le" if kind is MediaKind.AUDIO else "h264",
        time_base=MediaTimeBase(1, 1_000),
        start_pts=0,
        duration_ts=5_000,
        average_frame_rate=FrameRate(30, 1) if stream_kind is MediaStreamKind.VIDEO else None,
        sample_rate=48_000 if stream_kind is MediaStreamKind.AUDIO else None,
    )
    return MediaAnalysis(
        source_path=str(path.resolve(strict=False)),
        name=path.name,
        kind=kind,
        duration=None if kind is MediaKind.PHOTO else ProjectTime.from_seconds(5),
        width=1920 if kind is not MediaKind.AUDIO else None,
        height=1080 if kind is not MediaKind.AUDIO else None,
        primary_stream_index=0,
        streams=(stream,),
    )


class StubAnalyzer:
    def __init__(self, results_by_name) -> None:
        self.results_by_name = results_by_name
        self.calls: list[str] = []

    def analyze(self, source_path):
        normalized = str(Path(source_path).resolve(strict=False))
        self.calls.append(normalized)
        result = self.results_by_name[Path(normalized).name]
        if callable(result):
            return result(normalized)
        return result


class StubThumbnailer:
    def __init__(self, *, fail_names=()) -> None:
        self.fail_names = set(fail_names)
        self.calls: list[str] = []

    def create(self, analysis):
        self.calls.append(analysis.source_path)
        if analysis.name in self.fail_names:
            return ThumbnailFailure(
                analysis.source_path,
                ThumbnailErrorCode.PROCESS_FAILED,
                "썸네일 생성 실패",
            )
        return ThumbnailSuccess(analysis.source_path, PNG_BYTES)


def _success(kind: MediaKind):
    return lambda source_path: MediaAnalysisSuccess(_analysis(source_path, kind))


def _library(analyzer, thumbnailer=None, ids=("media-1", "media-2", "media-3")):
    identifier_iterator = iter(ids)
    executor = CommandExecutor(Project.empty(project_id="project-1"))
    library = MediaLibrary(
        executor,
        analyzer,
        thumbnailer,
        asset_id_factory=lambda: next(identifier_iterator),
    )
    return library, executor


def test_mixed_batch_keeps_video_photo_audio_successes_and_file_failures(tmp_path) -> None:
    sources = [
        tmp_path / "여행 영상.mp4",
        tmp_path / "가족 사진.png",
        tmp_path / "배경 음악.wav",
        tmp_path / "지원 안 됨.xyz",
        tmp_path / "손상 영상.mov",
    ]
    for source in sources:
        source.write_bytes(f"original:{source.name}".encode())
    before = {source: (source.read_bytes(), source.stat().st_mtime_ns) for source in sources}
    analyzer = StubAnalyzer(
        {
            "여행 영상.mp4": _success(MediaKind.VIDEO),
            "가족 사진.png": _success(MediaKind.PHOTO),
            "배경 음악.wav": _success(MediaKind.AUDIO),
            "지원 안 됨.xyz": lambda path: MediaAnalysisFailure(
                path,
                MediaAnalysisErrorCode.UNSUPPORTED_TYPE,
                "지원하지 않는 파일 형식입니다.",
            ),
            "손상 영상.mov": lambda path: MediaAnalysisFailure(
                path,
                MediaAnalysisErrorCode.PROCESS_FAILED,
                "ffprobe가 파일을 분석하지 못했습니다.",
            ),
        }
    )
    thumbnailer = StubThumbnailer()
    library, executor = _library(analyzer, thumbnailer)

    report = library.import_paths(sources)

    assert len(report.imported) == 3
    assert {item.reference.kind for item in report.imported} == set(MediaKind)
    assert [failure.code for failure in report.failures] == [
        MediaImportErrorCode.UNSUPPORTED_TYPE,
        MediaImportErrorCode.PROCESS_FAILED,
    ]
    assert len(library.project.media) == 3
    assert executor.history_count == 3
    assert all(
        isinstance(entry.command, InsertMediaReference) for entry in executor.history
    )
    assert len(thumbnailer.calls) == 2
    assert all((source.read_bytes(), source.stat().st_mtime_ns) == before[source] for source in sources)


def test_duplicate_paths_are_not_reanalyzed_or_reinserted(tmp_path) -> None:
    source = tmp_path / "같은 영상.mp4"
    source.write_bytes(b"original")
    analyzer = StubAnalyzer({source.name: _success(MediaKind.VIDEO)})
    library, executor = _library(analyzer)
    equivalent_path = source.parent / "." / source.name

    first = library.import_paths((source, equivalent_path))
    second = library.import_paths((source,))

    assert len(first.imported) == 1
    assert first.duplicates == (
        DuplicateMedia(str(source.resolve()), "media-1"),
    )
    assert second.duplicates == (
        DuplicateMedia(str(source.resolve()), "media-1"),
    )
    assert analyzer.calls == [str(source.resolve())]
    assert executor.history_count == 1


def test_thumbnail_failure_keeps_committed_media_and_is_cached_as_warning(tmp_path) -> None:
    source = tmp_path / "thumbnail failure.mp4"
    source.write_bytes(b"original")
    analyzer = StubAnalyzer({source.name: _success(MediaKind.VIDEO)})
    thumbnailer = StubThumbnailer(fail_names={source.name})
    library, executor = _library(analyzer, thumbnailer)

    report = library.import_paths((source,))

    assert report.imported == (ImportedMedia(library.project.media[0]),)
    assert len(report.thumbnail_failures) == 1
    assert library.thumbnail("media-1") is None
    assert library.thumbnail_failure("media-1") == report.thumbnail_failures[0]
    assert executor.history_count == 1


def test_unused_item_removal_uses_command_and_discards_only_thumbnail(tmp_path) -> None:
    source = tmp_path / "remove me.mp4"
    source.write_bytes(b"source bytes")
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    analyzer = StubAnalyzer({source.name: _success(MediaKind.VIDEO)})
    library, executor = _library(analyzer, StubThumbnailer())
    report = library.import_paths((source,))
    assert report.imported[0].thumbnail is not None

    result = library.remove("media-1")

    assert isinstance(result, MediaRemovalSuccess)
    assert not library.project.media
    assert library.thumbnail("media-1") is None
    assert executor.history_count == 2
    assert isinstance(executor.history[-1].command, RemoveMediaReference)
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_used_item_removal_is_rejected_without_project_or_history_change(tmp_path) -> None:
    analysis = _analysis(str(tmp_path / "used.mp4"), MediaKind.VIDEO)
    reference = analysis.to_media_reference("media-used")
    empty = Project.empty(project_id="project-1")
    project_with_media = replace(empty, media=(reference,))
    executor = CommandExecutor(project_with_media)
    clip = Clip(
        clip_id="clip-used",
        track=TrackKind.VISUAL,
        asset_id=reference.asset_id,
        label="used",
        timeline_start=ProjectTime.zero(),
        duration=ProjectTime.from_seconds(5),
        source_out=ProjectTime.from_seconds(5),
    )
    executor.execute(InsertClip(clip))
    library = MediaLibrary(executor, StubAnalyzer({}))
    before_project = library.project
    before_history = executor.history_count

    result = library.remove(reference.asset_id)

    assert result == MediaRemovalFailure(
        reference.asset_id,
        MediaRemovalErrorCode.IN_USE,
        "타임라인에서 사용 중인 미디어는 제거할 수 없습니다.",
        1,
    )
    assert library.project == before_project
    assert executor.history_count == before_history


def test_empty_selection_is_cancelled_without_state_changes() -> None:
    analyzer = StubAnalyzer({})
    library, executor = _library(analyzer)
    before = library.project

    report = library.import_paths(())

    assert report.cancelled
    assert report.imported == ()
    assert library.project == before
    assert executor.history_count == 0
    assert analyzer.calls == []


def test_command_rejection_does_not_rollback_earlier_success(tmp_path) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    analyzer = StubAnalyzer(
        {
            first.name: _success(MediaKind.VIDEO),
            second.name: _success(MediaKind.VIDEO),
        }
    )
    library, executor = _library(analyzer, ids=("same-id", "same-id"))

    report = library.import_paths((first, second))

    assert len(report.imported) == 1
    assert report.failures[0].code is MediaImportErrorCode.COMMAND_REJECTED
    assert [media.name for media in library.project.media] == [first.name]
    assert executor.history_count == 1
