from dataclasses import replace

import pytest

from movie_maker.project import (
    AudioLevel,
    Canvas,
    Clip,
    CommandExecutor,
    InvalidProjectDocument,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    Project,
    ProjectTime,
    ProjectValidationError,
    TimelineTrack,
    TrackKind,
    project_from_document,
    project_to_document,
)
from movie_maker.timeline import SplitClip, UpdateClipAudio, UpdateClipTiming


def _project() -> Project:
    video = MediaReference(
        asset_id="video",
        name="video.mp4",
        source_path="D:/Media/video.mp4",
        kind=MediaKind.VIDEO,
        duration=ProjectTime.from_seconds(8),
        width=640,
        height=360,
        primary_stream_index=0,
        streams=(
            MediaStream(
                0,
                MediaStreamKind.VIDEO,
                "h264",
                MediaTimeBase(1, 30),
            ),
            MediaStream(
                1,
                MediaStreamKind.AUDIO,
                "aac",
                MediaTimeBase(1, 48_000),
                sample_rate=48_000,
            ),
        ),
    )
    music = MediaReference(
        asset_id="music",
        name="music.wav",
        source_path="D:/Media/music.wav",
        kind=MediaKind.AUDIO,
        duration=ProjectTime.from_seconds(8),
        primary_stream_index=2,
        streams=(
            MediaStream(
                2,
                MediaStreamKind.AUDIO,
                "pcm_s16le",
                MediaTimeBase(1, 44_100),
                sample_rate=44_100,
            ),
        ),
    )
    visual = Clip(
        "visual",
        TrackKind.VISUAL,
        video.asset_id,
        "video",
        ProjectTime.zero(),
        ProjectTime.from_seconds(6),
        source_in=ProjectTime.from_seconds(1),
        source_out=ProjectTime.from_seconds(7),
        audio_level=AudioLevel(75),
    )
    music_clip = Clip(
        "music-clip",
        TrackKind.MUSIC,
        music.asset_id,
        "music",
        ProjectTime.from_seconds(1),
        ProjectTime.from_seconds(4),
        source_in=ProjectTime.from_seconds(2),
        source_out=ProjectTime.from_seconds(6),
        audio_level=AudioLevel(55),
        audio_muted=True,
    )
    empty = Project.empty(project_id="audio-project")
    return replace(
        empty,
        canvas=Canvas(640, 360, video.asset_id),
        media=(video, music),
        tracks=(
            TimelineTrack(TrackKind.VISUAL, (visual,)),
            TimelineTrack(TrackKind.MUSIC, (music_clip,)),
            empty.track(TrackKind.NARRATION),
            empty.track(TrackKind.TEXT),
        ),
    )


def test_audio_level_is_an_exact_bounded_value() -> None:
    assert AudioLevel(55).fraction.numerator == 11
    assert AudioLevel(55).fraction.denominator == 20

    with pytest.raises(ProjectValidationError, match="between 0 and 100"):
        AudioLevel(101)
    with pytest.raises(TypeError, match="integer"):
        AudioLevel(50.0)  # type: ignore[arg-type]


def test_audio_properties_round_trip_and_old_documents_receive_safe_defaults() -> None:
    project = _project()
    document = project_to_document(project)

    assert project_from_document(document) == project
    assert document["timeline"]["visual"][0]["audio_level_percent"] == 75
    assert document["timeline"]["music"][0]["audio_muted"] is True

    for clips in document["timeline"].values():
        for clip in clips:
            clip.pop("audio_level_percent")
            clip.pop("audio_muted")
    restored = project_from_document(document)
    assert restored.clip("visual").audio_level == AudioLevel(100)
    assert not restored.clip("visual").audio_muted


@pytest.mark.parametrize(
    ("field", "value"),
    (("audio_level_percent", 101), ("audio_muted", 0)),
)
def test_invalid_persisted_audio_values_are_rejected(field: str, value: object) -> None:
    document = project_to_document(_project())
    document["timeline"]["visual"][0][field] = value

    with pytest.raises(InvalidProjectDocument):
        project_from_document(document)


def test_level_and_mute_commands_preserve_level_and_round_trip_history() -> None:
    executor = CommandExecutor(_project())

    executor.execute(UpdateClipAudio("visual", audio_muted=True))
    assert executor.project.clip("visual").audio_level == AudioLevel(75)
    assert executor.project.clip("visual").audio_muted
    executor.execute(UpdateClipAudio("visual", audio_muted=False))
    assert executor.project.clip("visual").audio_level == AudioLevel(75)
    assert not executor.project.clip("visual").audio_muted
    executor.execute(UpdateClipAudio("visual", audio_level=AudioLevel(25)))
    changed = executor.project

    assert executor.undo() != changed
    assert executor.redo() == changed


def test_combined_timing_and_audio_submission_is_one_atomic_history_entry() -> None:
    executor = CommandExecutor(_project())

    executor.execute(
        UpdateClipTiming(
            "music-clip",
            timeline_start=ProjectTime.from_seconds(2),
            source_in=ProjectTime.from_seconds(1),
            source_out=ProjectTime.from_seconds(5),
            audio_level=AudioLevel(35),
            audio_muted=False,
            history_label="오디오 속성 적용",
        )
    )

    clip = executor.project.clip("music-clip")
    assert (clip.timeline_start, clip.source_in, clip.source_out) == (
        ProjectTime.from_seconds(2),
        ProjectTime.from_seconds(1),
        ProjectTime.from_seconds(5),
    )
    assert (clip.audio_level, clip.audio_muted) == (AudioLevel(35), False)
    assert executor.history_count == 1
    assert executor.undo() == _project()


def test_split_and_trim_preserve_original_sound_properties() -> None:
    executor = CommandExecutor(_project())

    executor.execute(SplitClip("visual", ProjectTime.from_seconds(3), "visual-back"))
    front = executor.project.clip("visual")
    back = executor.project.clip("visual-back")

    assert front.audio_level == back.audio_level == AudioLevel(75)
    assert front.audio_muted is back.audio_muted is False


def test_photo_cannot_persist_audio_properties() -> None:
    photo = MediaReference(
        "photo",
        "photo.png",
        "D:/Media/photo.png",
        MediaKind.PHOTO,
        None,
        100,
        100,
    )
    clip = Clip(
        "photo-clip",
        TrackKind.VISUAL,
        photo.asset_id,
        "photo",
        ProjectTime.zero(),
        ProjectTime.from_seconds(2),
        audio_muted=True,
    )
    empty = Project.empty(project_id="photo")

    with pytest.raises(ProjectValidationError, match="Photo clips"):
        replace(
            empty,
            media=(photo,),
            tracks=(
                TimelineTrack(TrackKind.VISUAL, (clip,)),
                *empty.tracks[1:],
            ),
        )


def test_narration_cannot_persist_w06_audio_properties() -> None:
    project = _project()
    narration = replace(
        project.clip("music-clip"),
        clip_id="narration-clip",
        track=TrackKind.NARRATION,
        audio_muted=True,
    )

    with pytest.raises(ProjectValidationError, match="Narration clips"):
        replace(
            project,
            tracks=(
                project.track(TrackKind.VISUAL),
                TimelineTrack(TrackKind.MUSIC),
                TimelineTrack(TrackKind.NARRATION, (narration,)),
                project.track(TrackKind.TEXT),
            ),
        )
