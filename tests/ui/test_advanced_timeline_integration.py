from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_controller import MockController
from movie_maker.ui.mock_model import TrackKind


def test_selection_rejects_mixed_tracks_without_losing_previous_selection() -> None:
    controller = MockController()
    controller.load_sample_project()
    assert controller.select_clips(["clip-beach", "clip-market"], "clip-market")
    before = (
        list(controller.state.selected_clip_ids),
        controller.state.selected_clip_id,
        controller.history_position,
    )

    assert not controller.select_clips(["clip-beach", "clip-music"], "clip-music")

    assert (
        controller.state.selected_clip_ids,
        controller.state.selected_clip_id,
        controller.history_position,
    ) == before
    assert "서로 다른 트랙" in controller.state.status_message


def test_duplicate_and_group_delete_use_core_history_and_deterministic_selection() -> None:
    ids = iter(("clip-copy",))
    controller = MockController(clip_id_factory=lambda: next(ids))
    controller.load_sample_project()
    controller.select_clip("clip-market")

    assert controller.duplicate_selected_clip()
    assert controller.state.selected_clip_ids == ["clip-copy"]
    assert controller.media_project.clip("clip-copy").asset_id == "media-market"
    assert controller.undo_label == "클립 복제"
    assert controller.undo()
    assert controller.state.selected_clip_ids == []
    assert controller.redo()

    assert controller.select_clips(["clip-market", "clip-copy"], "clip-copy")
    position = controller.history_position
    assert controller.delete_selected_clip()
    assert controller.history_position == position + 1
    assert controller.state.selected_clip_ids == []
    assert controller.undo_label == "클립 2개 삭제"
    assert controller.undo()
    assert controller.state.selected_clip_ids == []


def test_view_zoom_and_noop_drop_are_session_only() -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_clip("clip-market")
    controller.seek(8_500)
    project = controller.media_project
    dirty = controller.state.is_dirty
    history = (controller.history_count, controller.history_position)

    assert controller.set_timeline_mode("스토리보드")
    assert controller.set_timeline_zoom(174)
    assert controller.state.timeline_zoom == 175
    assert not controller.move_selected_to_index(controller.selected_clip.track, 1)  # type: ignore[union-attr]

    assert controller.media_project is project
    assert controller.state.is_dirty is dirty
    assert (controller.history_count, controller.history_position) == history
    assert controller.state.selected_clip_id == "clip-market"
    assert controller.state.playhead_ms == 8_500


def test_auxiliary_drop_applies_one_common_time_delta() -> None:
    controller = MockController()
    controller.load_sample_project()
    assert controller.select_clips(["clip-caption"], "clip-caption")
    position = controller.history_position

    assert not controller.move_selected_absolute_to_index(TrackKind.TEXT, 0)
    assert controller.history_position == position

    assert controller.select_clips(["clip-music"], "clip-music")
    assert controller.move_selected_absolute(1_000)
    assert controller.media_project.clip("clip-music").timeline_start.to_milliseconds() == 1_000
    assert controller.history_position == position + 1


def test_ctrl_and_shift_selection_and_view_switch_share_context(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    window.show()
    track = window._timeline_lists[next(iter(window._timeline_lists))]

    qtbot.mouseClick(
        track.viewport(),
        Qt.MouseButton.LeftButton,
        pos=track.visualItemRect(track.item(0)).center(),
    )
    qtbot.mouseClick(
        track.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        track.visualItemRect(track.item(2)).center(),
    )
    assert window.controller.state.selected_clip_ids == ["clip-beach", "clip-photo"]

    qtbot.mouseClick(
        track.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        track.visualItemRect(track.item(1)).center(),
    )
    assert window.controller.state.selected_clip_ids == [
        "clip-beach",
        "clip-market",
        "clip-photo",
    ]
    playhead = window.controller.state.playhead_ms

    window._actions["view_toggle"].trigger()

    assert window.controller.state.timeline_mode == "스토리보드"
    assert window.timeline_views.currentWidget() is window.storyboard_list.parentWidget()
    assert window.controller.state.selected_clip_ids == [
        "clip-beach",
        "clip-market",
        "clip-photo",
    ]
    assert window.controller.state.playhead_ms == playhead
    assert [
        window.storyboard_list.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(window.storyboard_list.count())
    ] == ["clip-beach", "clip-market", "clip-photo"]


def test_edit_shortcuts_respect_text_focus(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    window.controller.select_clip("clip-market")
    assert window.controller.duplicate_selected_clip()
    window.show()
    history = (window.controller.history_count, window.controller.history_position)
    project = window.controller.media_project

    editor = window.project_name_edit
    assert isinstance(editor, QLineEdit)
    monkeypatch.setattr(QApplication, "focusWidget", lambda: editor)
    editor.setText("로컬 입력")
    qtbot.wait(1)
    window._duplicate_contextual()
    window._delete_contextual()
    window._undo_contextual()

    assert window.controller.media_project is project
    assert (window.controller.history_count, window.controller.history_position) == history
