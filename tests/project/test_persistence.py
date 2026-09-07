from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from movie_maker.project import (
    Canvas,
    Clip,
    FrameRate,
    InvalidProjectDocument,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    PlaybackRate,
    Project,
    ProjectFileStore,
    ProjectReadError,
    ProjectTime,
    ProjectWriteError,
    TimelineTrack,
    TrackKind,
    UnsupportedProjectVersion,
    project_from_document,
    project_to_document,
)


def _complete_project(source_root: Path) -> Project:
    video = MediaReference(
        asset_id="영상-001",
        name="긴 이름 영상 파일.mp4",
        source_path=str(source_root / "한글 폴더" / "긴 이름 영상 파일.mp4"),
        kind=MediaKind.VIDEO,
        duration=ProjectTime(12_345_678_901),
        width=3840,
        height=2160,
        primary_stream_index=0,
        streams=(
            MediaStream(
                index=0,
                kind=MediaStreamKind.VIDEO,
                codec_name="hevc",
                time_base=MediaTimeBase(1, 90_000),
                start_pts=-1_800,
                duration_ts=1_111_111,
                average_frame_rate=FrameRate(24_000, 1_001),
            ),
            MediaStream(
                index=1,
                kind=MediaStreamKind.AUDIO,
                codec_name="aac",
                time_base=MediaTimeBase(1, 48_000),
                start_pts=0,
                duration_ts=592_592,
                sample_rate=48_000,
            ),
        ),
    )
    photo = MediaReference(
        asset_id="photo-001",
        name="산 사진.png",
        source_path=str(source_root / "산 사진.png"),
        kind=MediaKind.PHOTO,
        duration=None,
        width=6000,
        height=4000,
        primary_stream_index=0,
        streams=(
            MediaStream(
                index=0,
                kind=MediaStreamKind.VIDEO,
                codec_name="png",
                time_base=MediaTimeBase(1, 25),
                average_frame_rate=FrameRate(25),
            ),
        ),
    )
    audio = MediaReference(
        asset_id="audio-001",
        name="배경 음악.wav",
        source_path=str(source_root / "배경 음악.wav"),
        kind=MediaKind.AUDIO,
        duration=ProjectTime(30_000_000_000),
        primary_stream_index=2,
        streams=(
            MediaStream(
                index=2,
                kind=MediaStreamKind.AUDIO,
                codec_name="pcm_s24le",
                time_base=MediaTimeBase(1, 96_000),
                start_pts=96,
                duration_ts=2_880_000,
                sample_rate=96_000,
            ),
        ),
    )
    visual_clips = (
        Clip(
            clip_id="clip-video",
            track=TrackKind.VISUAL,
            asset_id=video.asset_id,
            label="첫 장면",
            timeline_start=ProjectTime.zero(),
            duration=ProjectTime(5_000_000_000),
            source_in=ProjectTime(1_000_000_000),
            source_out=ProjectTime(6_000_000_000),
            playback_rate=PlaybackRate(1, 1),
        ),
        Clip(
            clip_id="clip-photo",
            track=TrackKind.VISUAL,
            asset_id=photo.asset_id,
            label="사진 장면",
            timeline_start=ProjectTime(5_000_000_000),
            duration=ProjectTime(4_000_000_000),
        ),
    )
    return Project(
        schema_version=1,
        project_id="프로젝트-공백 유니코드",
        name="제주 여행 프로젝트",
        canvas=Canvas(3840, 2160, video.asset_id),
        media=(video, photo, audio),
        tracks=(
            TimelineTrack(TrackKind.VISUAL, visual_clips),
            TimelineTrack(
                TrackKind.MUSIC,
                (
                    Clip(
                        clip_id="clip-music",
                        track=TrackKind.MUSIC,
                        asset_id=audio.asset_id,
                        label="배경 음악",
                        timeline_start=ProjectTime(500_000_000),
                        duration=ProjectTime(8_000_000_000),
                        source_in=ProjectTime(2_000_000_000),
                        source_out=ProjectTime(10_000_000_000),
                    ),
                ),
            ),
            TimelineTrack(
                TrackKind.NARRATION,
                (
                    Clip(
                        clip_id="clip-narration",
                        track=TrackKind.NARRATION,
                        asset_id=audio.asset_id,
                        label="내레이션",
                        timeline_start=ProjectTime(2_000_000_000),
                        duration=ProjectTime(4_000_000_000),
                        source_out=ProjectTime(8_000_000_000),
                        playback_rate=PlaybackRate(2, 1),
                    ),
                ),
            ),
            TimelineTrack(
                TrackKind.TEXT,
                (
                    Clip(
                        clip_id="clip-text",
                        track=TrackKind.TEXT,
                        asset_id=None,
                        label="제목 텍스트",
                        timeline_start=ProjectTime.zero(),
                        duration=ProjectTime(3_000_000_000),
                    ),
                ),
            ),
        ),
    )


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


def test_empty_project_json_round_trip() -> None:
    project = Project.empty(project_id="empty-project", name="빈 프로젝트")

    assert project_from_document(project_to_document(project)) == project


def test_all_media_tracks_clips_and_stream_metadata_round_trip(tmp_path: Path) -> None:
    project = _complete_project(tmp_path / ("아주 긴 경로 " * 8))
    target = tmp_path / (("매우 긴 프로젝트 이름 " * 8) + ".mmrproj")

    ProjectFileStore().save(project, target)

    assert ProjectFileStore().load(target) == project
    assert target.read_text(encoding="utf-8").startswith("{\n  \"schema_version\": 1")


def test_document_excludes_session_and_regenerable_state(tmp_path: Path) -> None:
    encoded = json.dumps(project_to_document(_complete_project(tmp_path)), ensure_ascii=False)

    for excluded in (
        "selected",
        "playhead",
        "panel",
        "thumbnail",
        "proxy",
        "export",
        "history",
    ):
        assert excluded not in encoded.casefold()


@pytest.mark.parametrize(
    ("mutate", "error_type"),
    [
        (lambda value: value.update(schema_version=2), UnsupportedProjectVersion),
        (lambda value: value.update(schema_version=True), InvalidProjectDocument),
        (lambda value: value["media"][0].update(width=-1), InvalidProjectDocument),
        (
            lambda value: value["timeline"]["visual"][0].update(asset_id="missing"),
            InvalidProjectDocument,
        ),
        (lambda value: value["timeline"].pop("text"), InvalidProjectDocument),
    ],
)
def test_invalid_versions_values_references_and_structure_are_rejected(
    tmp_path: Path,
    mutate,
    error_type: type[ProjectReadError],
) -> None:
    document = project_to_document(_complete_project(tmp_path))
    mutate(document)

    with pytest.raises(error_type):
        project_from_document(document)


def test_corrupt_json_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "broken.mmrproj"
    target.write_text('{"schema_version":', encoding="utf-8")

    with pytest.raises(ProjectReadError):
        ProjectFileStore().load(target)


def test_replace_failure_preserves_existing_file_and_removes_temporary_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "existing.mmrproj"
    original = b"known-good-existing-project"
    target.write_bytes(original)

    def fail_replace(_source: str, _target: str) -> None:
        raise OSError("injected replace failure")

    with pytest.raises(ProjectWriteError):
        ProjectFileStore(replace_file=fail_replace).save(
            Project.empty(project_id="replacement"), target
        )

    assert target.read_bytes() == original
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_fsync_failure_preserves_existing_file_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing.mmrproj"
    original = b"known-good-existing-project"
    target.write_bytes(original)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(ProjectWriteError):
        ProjectFileStore().save(Project.empty(project_id="replacement"), target)

    assert target.read_bytes() == original
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_partial_write_failure_preserves_existing_file_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing.mmrproj"
    original = b"known-good-existing-project"
    target.write_bytes(original)

    def fail_dump(_value: object, stream, **_kwargs: object) -> None:
        stream.write("partial")
        raise OSError("injected write failure")

    monkeypatch.setattr(json, "dump", fail_dump)
    with pytest.raises(ProjectWriteError):
        ProjectFileStore().save(Project.empty(project_id="replacement"), target)

    assert target.read_bytes() == original
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_save_and_load_do_not_modify_missing_or_existing_source_media(tmp_path: Path) -> None:
    source = tmp_path / "한글 원본 파일.mp4"
    source.write_bytes(b"immutable source media\x00\x01")
    project = _complete_project(tmp_path)
    first = project.media[0]
    project = Project(
        schema_version=project.schema_version,
        project_id=project.project_id,
        name=project.name,
        canvas=project.canvas,
        media=(
            MediaReference(
                asset_id=first.asset_id,
                name=source.name,
                source_path=str(source),
                kind=first.kind,
                duration=first.duration,
                width=first.width,
                height=first.height,
                primary_stream_index=first.primary_stream_index,
                streams=first.streams,
            ),
            *project.media[1:],
        ),
        tracks=project.tracks,
    )
    before = _fingerprint(source)

    target = tmp_path / "project.mmrproj"
    ProjectFileStore().save(project, target)
    loaded = ProjectFileStore().load(target)

    assert _fingerprint(source) == before
    assert loaded == project
    assert not Path(loaded.media[1].source_path).exists()
    assert loaded.track(TrackKind.VISUAL).clips[1] == project.track(
        TrackKind.VISUAL
    ).clips[1]
