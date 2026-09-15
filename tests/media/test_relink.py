from dataclasses import replace
from pathlib import Path

from movie_maker.media import (
    MediaAnalysis,
    MediaAnalysisSuccess,
    MediaRelinker,
    RelinkErrorCode,
    RelinkFailure,
    RelinkMatch,
    RelinkSuccess,
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
    TrackKind,
)


class Analyzer:
    def __init__(self, result) -> None:
        self.result = result

    def analyze(self, _source_path):
        return self.result


def _analysis(path: Path, *, duration: int = 5_000_000_000, width: int = 1920):
    stream = MediaStream(
        0,
        MediaStreamKind.VIDEO,
        "h264",
        MediaTimeBase(1, 1_000),
        start_pts=0,
        duration_ts=duration // 1_000_000,
        average_frame_rate=FrameRate(30),
    )
    return MediaAnalysis(
        str(path),
        path.name,
        MediaKind.VIDEO,
        ProjectTime(duration),
        width,
        1080,
        0,
        (stream,),
    )


def _executor(tmp_path: Path):
    old = _analysis(tmp_path / "missing.mp4").to_media_reference("media-1")
    executor = CommandExecutor(Project.empty(project_id="project-1"))
    executor.execute(InsertMediaReference(old))
    executor.execute(
        InsertClip(
            Clip(
                clip_id="clip-1",
                track=TrackKind.VISUAL,
                asset_id="media-1",
                label="clip",
                timeline_start=ProjectTime(0),
                duration=ProjectTime(5_000_000_000),
                source_out=ProjectTime(5_000_000_000),
            )
        )
    )
    return executor, old


def test_exact_relink_is_one_command_and_preserves_clips_and_edit_points(tmp_path: Path) -> None:
    executor, old = _executor(tmp_path)
    candidate = _analysis(tmp_path / "new.mp4")
    before_clip = executor.project.clip("clip-1")
    before_count = executor.history_count

    result = MediaRelinker(executor, Analyzer(MediaAnalysisSuccess(candidate))).relink(
        "media-1", str(tmp_path / "new.mp4")
    )

    assert isinstance(result, RelinkSuccess)
    assert result.comparison.match is RelinkMatch.EXACT
    assert executor.history_count == before_count + 1
    assert executor.project.clip("clip-1") == before_clip
    assert executor.project.media_reference("media-1").source_path == candidate.source_path
    executor.undo()
    assert executor.project.media_reference("media-1") == old


def test_difference_requires_confirmation_and_mismatch_changes_nothing(tmp_path: Path) -> None:
    executor, _old = _executor(tmp_path)
    before = executor.project
    before_position = executor.history_position
    confirm = replace(_analysis(tmp_path / "new.mp4"), width=1280)
    relinker = MediaRelinker(executor, Analyzer(MediaAnalysisSuccess(confirm)))
    result = relinker.relink("media-1", confirm.source_path)
    assert isinstance(result, RelinkFailure)
    assert result.code is RelinkErrorCode.CONFIRMATION_REQUIRED
    assert executor.project == before
    assert executor.history_position == before_position

    mismatch = _analysis(tmp_path / "wrong.mp4", duration=8_000_000_000)
    result = MediaRelinker(
        executor, Analyzer(MediaAnalysisSuccess(mismatch))
    ).relink("media-1", mismatch.source_path, confirmed=True)
    assert isinstance(result, RelinkFailure)
    assert result.code is RelinkErrorCode.MISMATCH
    assert executor.project == before


def test_folder_scan_validates_each_same_name_candidate_without_mutation(tmp_path: Path) -> None:
    executor, _old = _executor(tmp_path)
    folder = tmp_path / "moved"
    folder.mkdir()
    candidate_path = folder / "missing.mp4"
    candidate_path.write_bytes(b"candidate")
    candidate = _analysis(candidate_path)
    before = executor.project

    report = MediaRelinker(executor, Analyzer(MediaAnalysisSuccess(candidate))).inspect_folder(
        ("media-1", "media-1", "unknown"), str(folder)
    )

    assert len(report.candidates) == 1
    assert report.candidates[0].asset_id == "media-1"
    assert report.candidates[0].comparison.match is RelinkMatch.EXACT
    assert report.unresolved_asset_ids == ("unknown",)
    assert report.conflicting_asset_ids == ("media-1",)
    assert executor.project == before
