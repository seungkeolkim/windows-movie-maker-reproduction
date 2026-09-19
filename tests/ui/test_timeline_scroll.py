from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_model import TrackKind


@pytest.fixture
def window(qtbot):
    widget = MainWindow()
    qtbot.addWidget(widget)
    widget.controller.load_sample_project()
    widget.show()
    QApplication.processEvents()
    yield widget
    widget._allow_close = True
    widget.close()


def test_scroll_reaches_offscreen_time_and_preserves_project(window, qtbot, tmp_path):
    controller = window.controller
    before = controller.media_project
    history = controller.history_count
    controller.set_timeline_zoom(200)
    QApplication.processEvents()
    bar = window.timeline_scrollbar
    assert bar.isVisible()
    assert bar.maximum() > 0
    bar.setFocus()
    qtbot.keyClick(bar, Qt.Key.Key_End)
    QApplication.processEvents()
    assert bar.value() == bar.maximum()
    for view in window._timeline_lists.values():
        track_bar = view.horizontalScrollBar()
        assert track_bar.value() == track_bar.maximum()
    controller.seek(round(controller.state.total_duration_ms * 0.75))
    QApplication.processEvents()
    playhead = window.timeline_playhead
    viewport = window._timeline_lists[TrackKind.VISUAL].viewport()
    origin = viewport.mapTo(window.timeline_page, QPoint(0, 0)).x()
    qtbot.mousePress(playhead, Qt.MouseButton.LeftButton, pos=QPoint(playhead._x, 4))
    point = QPoint(origin + viewport.width() - 20, 4)
    qtbot.mouseMove(playhead, point)
    assert controller.state.playhead_ms > controller.state.total_duration_ms * 0.9
    qtbot.mouseRelease(playhead, Qt.MouseButton.LeftButton, pos=point)
    QApplication.processEvents()
    assert controller.state.playhead_ms > controller.state.total_duration_ms * 0.9
    assert bar.value() > 0
    assert window.timeline_playhead._x >= 0
    assert controller.media_project == before
    assert controller.history_count == history
    assert window.grab().save(str(tmp_path / "timeline-scrolled.png"))


def test_scroll_range_tracks_zoom_resize_and_track_navigation(window):
    window.controller.set_timeline_zoom(200)
    QApplication.processEvents()
    bar = window.timeline_scrollbar
    view = window._timeline_lists[TrackKind.VISUAL]
    view.horizontalScrollBar().setValue(bar.maximum() // 2)
    assert bar.value() == view.horizontalScrollBar().value()
    window.resize(window.width() + 150, window.height())
    QApplication.processEvents()
    assert bar.maximum() == view.horizontalScrollBar().maximum()
    assert bar.pageStep() == view.horizontalScrollBar().pageStep()
    window.controller.set_timeline_zoom(50)
    QApplication.processEvents()
    assert not bar.isVisible()
    assert bar.maximum() == 0
    assert bar.value() == 0


def test_playhead_drag_clamps_to_project_and_leaves_track_input_available(window, qtbot):
    controller = window.controller
    controller.set_timeline_zoom(50)
    controller.seek(controller.state.total_duration_ms // 2)
    QApplication.processEvents()
    playhead = window.timeline_playhead
    view = window._timeline_lists[TrackKind.VISUAL]
    origin = view.viewport().mapTo(window.timeline_page, QPoint(0, 0))
    assert window.timeline_page.childAt(QPoint(playhead._x, 4)) is playhead
    assert window.timeline_page.childAt(origin + QPoint(10, 20)) is view.viewport()
    assert playhead.geometry().bottom() < window.timeline_page.height()
    for target, expected in ((origin.x() - 100, 0),
                             (origin.x() + view.viewport().width() + 100,
                              controller.state.total_duration_ms)):
        qtbot.mousePress(playhead, Qt.MouseButton.LeftButton, pos=QPoint(playhead._x, 4))
        qtbot.mouseMove(playhead, QPoint(target, 4))
        qtbot.mouseRelease(playhead, Qt.MouseButton.LeftButton, pos=QPoint(target, 4))
        assert controller.state.playhead_ms == expected
        assert window.preview_seek.value() == expected


def test_empty_timeline_has_no_draggable_playhead(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    QApplication.processEvents()
    assert not window.timeline_playhead.isVisible()


@pytest.mark.parametrize("zoom", [125, 175, 200])
def test_scrolled_playhead_stays_visible_through_continuous_drag(window, qtbot, zoom):
    controller = window.controller
    controller.set_timeline_zoom(zoom)
    QApplication.processEvents()
    bar = window.timeline_scrollbar
    bar.setValue(bar.maximum())
    controller.seek(round(controller.state.total_duration_ms * 0.8))
    QApplication.processEvents()
    playhead = window.timeline_playhead
    hidden = []

    class VisibilityObserver(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Hide:
                hidden.append(True)
            return False

    observer = VisibilityObserver(playhead)
    playhead.installEventFilter(observer)
    scroll_position = bar.value()
    start_x = playhead._x
    handle = window.windowHandle()
    QTest.mousePress(handle, Qt.MouseButton.LeftButton,
                     pos=playhead.mapTo(window, QPoint(start_x, 4)))
    try:
        assert QWidget.mouseGrabber() is playhead
        previous = controller.state.playhead_ms
        for delta in (8, 16, 32, 48, 64):
            QTest.mouseMove(handle, playhead.mapTo(window, QPoint(start_x + delta, 4)))
            QApplication.processEvents()
            assert not hidden, "Hiding the playhead releases Qt's mouse grab during a drag"
            assert controller.state.playhead_ms > previous
            assert abs(playhead._x - (start_x + delta)) <= 1
            assert bar.value() == scroll_position
            assert QWidget.mouseGrabber() is playhead
            assert playhead.mask().isEmpty()
            previous = controller.state.playhead_ms
    finally:
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton,
                           pos=playhead.mapTo(window, QPoint(playhead._x, 4)))
    assert QWidget.mouseGrabber() is not playhead
    assert not playhead._dragging
    assert playhead.mask().contains(QPoint(playhead._x, 4))
    assert not playhead.mask().contains(QPoint(playhead._x - 30, 4))


def test_drag_survives_transient_offscreen_position_and_cancels_on_hide(window, qtbot):
    playhead = window.timeline_playhead
    start_x = playhead._x
    qtbot.mousePress(playhead, Qt.MouseButton.LeftButton, pos=QPoint(start_x, 4))
    playhead.set_position(-1, 0)
    assert playhead.isVisible()
    assert QWidget.mouseGrabber() is playhead
    playhead.set_position(start_x, 0)
    window.hide()
    assert not playhead.isVisible()
    assert not playhead._dragging
    assert QWidget.mouseGrabber() is not playhead
