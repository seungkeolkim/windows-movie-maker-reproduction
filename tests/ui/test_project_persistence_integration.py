from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QFileDialog, QPushButton

from movie_maker.project import (
    Canvas,
    Clip,
    FrameRate,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    PlaybackRate,
    Project,
    ProjectFileStore,
    ProjectTime,
    TimelineTrack,
    TrackKind,
)
from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_controller import MockController
from movie_maker.ui.mock_model import AssetStatus


def _button(dialog: QDialog, text: str) -> QPushButton:
    return next(button for button in dialog.findChildren(QPushButton) if button.text() == text)


def test_controller_save_as_and_open_round_trip_clear_new_session_history(
    tmp_path: Path,
) -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_clip("clip-market")
    controller.seek(3_000)
    controller.toggle_preview_mute()
    controller.set_timeline_zoom(175)
    history_before = controller.history_count
    target = tmp_path / "한글 이름 프로젝트.mmrproj"

    assert controller.save_project(str(target))
    assert controller.history_count == history_before
    assert not controller.state.is_dirty

    controller.rename_project("저장 이후 변경")
    assert controller.open_project(str(target))

    assert controller.state.project_name == "제주 여행 목업"
    assert controller.state.project_path == str(target)
    assert not controller.state.is_dirty
    assert controller.history_count == 0
    assert controller.media_history_count == 0
    assert controller.state.selected_clip_id is None
    assert controller.state.playhead_ms == 0
    assert not controller.state.preview_muted
    assert controller.state.timeline_zoom == 100
    assert len(controller.state.visual_clips) == 3


def test_save_as_keeps_original_copy_and_changes_path_only_after_success(
    tmp_path: Path,
) -> None:
    controller = MockController()
    controller.import_sample_media()
    original = tmp_path / "original.mmrproj"
    copy = tmp_path / "copy.mmrproj"
    assert controller.save_project(str(original))
    original_bytes = original.read_bytes()
    controller.rename_project("사본에서만 바뀐 이름")

    assert controller.save_project(str(copy))

    assert original.read_bytes() == original_bytes
    assert ProjectFileStore().load(original).name == "제목 없음"
    assert ProjectFileStore().load(copy).name == "사본에서만 바뀐 이름"
    assert controller.state.project_path == str(copy)


def test_ui_open_then_save_preserves_sub_millisecond_clip_times_and_exact_rate(
    tmp_path: Path,
) -> None:
    media = MediaReference(
        asset_id="precise-media",
        name="precise.mp4",
        source_path=str(tmp_path / "missing precise.mp4"),
        kind=MediaKind.VIDEO,
        duration=ProjectTime(9_999_999_999),
        width=1920,
        height=1080,
        primary_stream_index=0,
        streams=(
            MediaStream(
                index=0,
                kind=MediaStreamKind.VIDEO,
                codec_name="h264",
                time_base=MediaTimeBase(1, 90_000),
                start_pts=-900,
                duration_ts=899_999,
                average_frame_rate=FrameRate(24_000, 1_001),
            ),
        ),
    )
    precise_clip = Clip(
        clip_id="precise-clip",
        track=TrackKind.VISUAL,
        asset_id=media.asset_id,
        label="정밀 클립",
        timeline_start=ProjectTime.zero(),
        duration=ProjectTime(5_000_000_499),
        source_in=ProjectTime(111_111_111),
        source_out=ProjectTime(5_116_116_615),
        playback_rate=PlaybackRate(24_000, 1_001),
    )
    project = Project(
        schema_version=1,
        project_id="precise-project",
        name="정밀 프로젝트",
        canvas=Canvas(1920, 1080, media.asset_id),
        media=(media,),
        tracks=(
            TimelineTrack(TrackKind.VISUAL, (precise_clip,)),
            TimelineTrack(TrackKind.MUSIC),
            TimelineTrack(TrackKind.NARRATION),
            TimelineTrack(TrackKind.TEXT),
        ),
    )
    source = tmp_path / "source.mmrproj"
    destination = tmp_path / "destination.mmrproj"
    ProjectFileStore().save(project, source)
    controller = MockController()

    assert controller.open_project(str(source))
    assert controller.save_project(str(destination))

    assert ProjectFileStore().load(destination) == project


def test_open_marks_missing_sources_without_losing_clips(tmp_path: Path) -> None:
    writer = MockController()
    writer.load_sample_project()
    target = tmp_path / "missing sources.mmrproj"
    assert writer.save_project(str(target))

    reader = MockController()
    assert reader.open_project(str(target))

    assert all(asset.status is AssetStatus.MISSING for asset in reader.state.assets.values())
    assert [clip.clip_id for clip in reader.state.visual_clips] == [
        "clip-beach",
        "clip-market",
        "clip-photo",
    ]
    assert reader.state.visual_clips[1].source_in_ms == 1_000
    assert reader.state.visual_clips[1].source_out_ms == 7_000
    assert "누락 미디어 5개" in reader.state.status_message


def test_failed_open_keeps_current_project_path_name_dirty_and_histories(tmp_path: Path) -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.rename_project("변경된 프로젝트")
    controller.state.project_path = str(tmp_path / "current.mmrproj")
    before = deepcopy(controller.state)
    history_before = (controller.history_count, controller.history_position)
    broken = tmp_path / "broken.mmrproj"
    broken.write_text("not json", encoding="utf-8")

    assert not controller.open_project(str(broken))

    assert controller.state.project_name == before.project_name
    assert controller.state.project_path == before.project_path
    assert controller.state.is_dirty == before.is_dirty
    assert controller.state.assets == before.assets
    assert controller.state.all_clips == before.all_clips
    assert (controller.history_count, controller.history_position) == history_before


def test_failed_save_keeps_path_name_dirty_and_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "existing.mmrproj"
    target.write_bytes(b"existing good bytes")

    def fail_replace(_source: str, _target: str) -> None:
        raise OSError("injected")

    controller = MockController(project_store=ProjectFileStore(replace_file=fail_replace))
    controller.import_sample_media()
    before_name = controller.state.project_name
    before_path = controller.state.project_path

    assert not controller.save_project(str(target))

    assert target.read_bytes() == b"existing good bytes"
    assert controller.state.project_name == before_name
    assert controller.state.project_path == before_path
    assert controller.state.is_dirty


def test_save_and_open_picker_cancellation_preserves_dirty_session(qtbot) -> None:
    window = MainWindow(
        project_open_selector=lambda: None,
        project_save_selector=lambda _current: None,
    )
    qtbot.addWidget(window)
    window.controller.import_sample_media()
    before = deepcopy(window.controller.state)

    assert not window._request_save()
    window._open_selected_project()

    assert window.controller.state.project_name == before.project_name
    assert window.controller.state.project_path == before.project_path
    assert window.controller.state.is_dirty
    assert window.controller.state.assets == before.assets


def test_unsaved_new_project_confirmation_handles_all_three_branches(
    qtbot, tmp_path: Path
) -> None:
    save_target = tmp_path / "before new.mmrproj"
    window = MainWindow(project_save_selector=lambda _current: str(save_target))
    qtbot.addWidget(window)
    window.controller.import_sample_media()
    original_assets = dict(window.controller.state.assets)

    window._request_new_project()
    cancel_dialog = window.findChildren(QDialog)[-1]
    qtbot.mouseClick(_button(cancel_dialog, "취소"), Qt.MouseButton.LeftButton)
    assert window.controller.state.assets == original_assets
    assert window.controller.state.is_dirty

    window._request_new_project()
    discard_dialog = window.findChildren(QDialog)[-1]
    qtbot.mouseClick(
        _button(discard_dialog, "저장하지 않고 계속"), Qt.MouseButton.LeftButton
    )
    assert window.controller.state.assets == {}
    assert not window.controller.state.is_dirty

    window.controller.import_sample_media()
    window._request_new_project()
    save_dialog = window.findChildren(QDialog)[-1]
    qtbot.mouseClick(_button(save_dialog, "저장하고 계속"), Qt.MouseButton.LeftButton)
    assert save_target.is_file()
    assert len(ProjectFileStore().load(save_target).media) == 5
    assert window.controller.state.assets == {}
    assert not window.controller.state.is_dirty


def test_unsaved_open_save_failure_stops_session_transition(qtbot, tmp_path: Path) -> None:
    opened = MockController()
    opened.load_sample_project()
    source = tmp_path / "opened.mmrproj"
    assert opened.save_project(str(source))

    def fail_replace(_source: str, _target: str) -> None:
        raise OSError("injected")

    controller = MockController(project_store=ProjectFileStore(replace_file=fail_replace))
    controller.import_sample_media()
    window = MainWindow(
        controller,
        project_open_selector=lambda: str(source),
        project_save_selector=lambda _current: str(tmp_path / "cannot-save.mmrproj"),
    )
    qtbot.addWidget(window)

    window._request_open_project()
    dialog = window.findChildren(QDialog)[-1]
    qtbot.mouseClick(_button(dialog, "저장하고 계속"), Qt.MouseButton.LeftButton)

    assert window.controller.state.is_dirty
    assert window.controller.state.project_path is None
    assert len(window.controller.state.assets) == 5


def test_close_confirmation_cancel_and_failed_save_keep_window_open(qtbot, tmp_path: Path) -> None:
    window = MainWindow(project_save_selector=lambda _current: None)
    qtbot.addWidget(window)
    window.controller.import_sample_media()
    window.show()

    window.close()
    cancel_dialog = window.findChildren(QDialog)[-1]
    qtbot.mouseClick(_button(cancel_dialog, "취소"), Qt.MouseButton.LeftButton)
    assert window.isVisible()
    assert window.controller.state.is_dirty

    window.close()
    save_dialog = window.findChildren(QDialog)[-1]
    qtbot.mouseClick(_button(save_dialog, "저장하고 닫기"), Qt.MouseButton.LeftButton)
    assert window.isVisible()
    assert window.controller.state.is_dirty


def test_close_confirmation_discard_closes_without_mutating_project(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.import_sample_media()
    assets_before = dict(window.controller.state.assets)
    window.show()

    window.close()
    dialog = window.findChildren(QDialog)[-1]
    qtbot.mouseClick(_button(dialog, "저장하지 않고 닫기"), Qt.MouseButton.LeftButton)

    assert not window.isVisible()
    assert window.controller.state.assets == assets_before
    assert window.controller.state.is_dirty


def test_project_shortcuts_and_default_native_file_dialogs(qtbot, monkeypatch) -> None:
    opened: list[tuple[object, ...]] = []
    saved: list[tuple[object, ...]] = []

    def choose_open(*args):
        opened.append(args)
        return "C:/프로젝트/opened.mmrproj", ""

    def choose_save(*args):
        saved.append(args)
        return "C:/프로젝트/saved.mmrproj", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose_open)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_save)
    window = MainWindow()
    qtbot.addWidget(window)

    assert window._actions["new"].shortcut() == QKeySequence("Ctrl+N")
    assert window._actions["open"].shortcut() == QKeySequence("Ctrl+O")
    assert window._actions["save"].shortcut() == QKeySequence("Ctrl+S")
    assert window._actions["save_as"].shortcut() == QKeySequence("Ctrl+Shift+S")
    assert window._choose_project_to_open() == "C:/프로젝트/opened.mmrproj"
    assert window._choose_project_to_save(None) == "C:/프로젝트/saved.mmrproj"
    assert opened and saved
