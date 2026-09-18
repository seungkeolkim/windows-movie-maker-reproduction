from __future__ import annotations

from itertools import pairwise

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import QApplication

from movie_maker.project import TrackKind as CoreTrackKind
from movie_maker.ui.main_window import LIBRARY_MEDIA_MIME, MainWindow
from movie_maker.ui.mock_model import AssetStatus, TrackKind


@pytest.fixture
def window(qtbot):
    widget = MainWindow()
    qtbot.addWidget(widget)
    widget.controller.import_sample_media()
    widget.show()
    QApplication.processEvents()
    return widget


def _mime(window, asset_id="media-beach"):
    item = next(
        window.library_list.item(row)
        for row in range(window.library_list.count())
        if window.library_list.item(row).data(Qt.ItemDataRole.UserRole) == asset_id
    )
    return window.library_list.mimeData([item])


def _enter(target, mime, point):
    event = QDragEnterEvent(
        point, Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target.viewport(), event)
    return event


def _drop(target, mime, point):
    assert _enter(target, mime, point).isAccepted()
    move = QDragMoveEvent(
        point, Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target.viewport(), move)
    assert move.isAccepted()
    assert target._asset_drop_x is not None
    event = QDropEvent(
        QPointF(point), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target.viewport(), event)
    assert event.isAccepted()
    assert event.dropAction() == Qt.DropAction.CopyAction
    assert target._asset_drop_x is None


@pytest.mark.parametrize("storyboard", [False, True])
def test_library_drop_into_empty_view_is_one_undoable_copy(window, storyboard):
    controller = window.controller
    if storyboard:
        controller.set_timeline_mode("스토리보드")
    target = window.storyboard_list if storyboard else window._timeline_lists[TrackKind.VISUAL]
    before = controller.media_project
    history = controller.history_count
    mime = _mime(window)
    # A later selection change must not change the identity being dragged.
    controller.select_asset("media-market")

    _drop(target, mime, QPoint(24, 20))

    result = controller.media_project
    clips = result.track(CoreTrackKind.VISUAL).clips
    assert len(clips) == 1
    assert clips[0].asset_id == "media-beach"
    assert clips[0].clip_id == controller.state.selected_clip_id
    assert result.media == before.media
    assert controller.history_count == history + 1
    assert controller.undo()
    assert controller.media_project == before
    assert controller.redo()
    assert controller.media_project == result


@pytest.mark.parametrize("index", [0, 1, 3])
@pytest.mark.parametrize("storyboard", [False, True])
def test_drop_inserts_at_visible_clip_boundary(window, index, storyboard):
    controller = window.controller
    controller.load_sample_project()
    if storyboard:
        controller.set_timeline_mode("스토리보드")
    target = window.storyboard_list if storyboard else window._timeline_lists[TrackKind.VISUAL]
    QApplication.processEvents()
    before = controller.media_project
    original = before.track(CoreTrackKind.VISUAL).clips
    row = min(index, target.count() - 1)
    rect = target.visualItemRect(target.item(row))
    point = QPoint(rect.left() + 3 if index < 3 else rect.right() - 3, rect.center().y())

    _drop(target, _mime(window, "media-photo"), point)

    clips = controller.media_project.track(CoreTrackKind.VISUAL).clips
    assert clips[index].asset_id == "media-photo"
    assert tuple(clip.clip_id for i, clip in enumerate(clips) if i != index) == tuple(
        clip.clip_id for clip in original
    )
    assert all(left.timeline_end == right.timeline_start for left, right in pairwise(clips))
    assert controller.undo()
    assert controller.media_project == before


@pytest.mark.parametrize("track", [TrackKind.MUSIC, TrackKind.NARRATION, TrackKind.TEXT])
def test_library_video_drop_rejects_incompatible_tracks(window, track):
    before = window.controller.media_project
    assert not _enter(window._timeline_lists[track], _mime(window), QPoint(10, 15)).isAccepted()
    assert window.controller.media_project == before


@pytest.mark.parametrize("payload", [b"unknown", b"media-music", b"\xff"])
def test_invalid_library_payload_does_not_change_project(window, payload):
    mime = QMimeData()
    mime.setData(LIBRARY_MEDIA_MIME, payload)
    before = window.controller.media_project
    target = window._timeline_lists[TrackKind.VISUAL]
    assert not _enter(target, mime, QPoint(10, 15)).isAccepted()
    assert window.controller.media_project == before


def test_cancel_and_asset_becoming_unavailable_do_not_add_clip(window):
    target = window._timeline_lists[TrackKind.VISUAL]
    mime = _mime(window)
    before = window.controller.media_project
    assert _enter(target, mime, QPoint(10, 15)).isAccepted()
    QApplication.sendEvent(target.viewport(), QDragLeaveEvent())
    assert target._asset_drop_x is None
    assert window.controller.media_project == before

    window.controller.state.assets["media-beach"].status = AssetStatus.MISSING
    assert not _enter(target, mime, QPoint(10, 15)).isAccepted()
    assert not window.controller.add_asset_to_timeline("media-beach", visual_index=0)
    assert window.controller.media_project == before


def test_library_starts_copy_drag_with_media_identity(window, monkeypatch, qtbot):
    recorded = []

    def execute(drag, action):
        recorded.append((drag.mimeData().data(LIBRARY_MEDIA_MIME).data(), action))
        return Qt.DropAction.IgnoreAction

    monkeypatch.setattr(QDrag, "exec", execute)
    window.controller.select_asset("media-market")
    item = window.library_list.item(0)
    assert item.data(Qt.ItemDataRole.UserRole) == "media-beach"
    point = window.library_list.visualItemRect(item).center()
    viewport = window.library_list.viewport()
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=point)
    for delta in (4, QApplication.startDragDistance() + 15):
        position = QPointF(point + QPoint(delta, 0))
        move = QMouseEvent(
            QEvent.Type.MouseMove, position, viewport.mapToGlobal(position),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, move)
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton)
    assert recorded == [(b"media-beach", Qt.DropAction.CopyAction)]
    assert not window.controller.state.visual_clips


def test_drag_scrolls_zoomed_timeline_and_inserts_at_scrolled_boundary(window, qtbot):
    window.controller.load_sample_project()
    window.controller.set_timeline_zoom(200)
    QApplication.processEvents()
    target = window._timeline_lists[TrackKind.VISUAL]
    mime = _mime(window)
    point = QPoint(target.viewport().width() - 4, 20)
    assert _enter(target, mime, point).isAccepted()
    move = QDragMoveEvent(
        point, Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target.viewport(), move)
    qtbot.waitUntil(lambda: target.horizontalScrollBar().value() > 0)
    target.horizontalScrollBar().setValue(target.horizontalScrollBar().maximum())
    QApplication.sendEvent(target.viewport(), QDragLeaveEvent())
    assert not target._asset_scroll_timer.isActive()

    _drop(target, mime, point)
    assert window.controller.state.visual_clips[-1].asset_id == "media-beach"
