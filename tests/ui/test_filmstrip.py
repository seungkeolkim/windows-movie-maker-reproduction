from __future__ import annotations

import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QFontMetrics, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QToolTip

from movie_maker.media import (
    FfprobeAnalyzer,
    MediaAnalysisSuccess,
    MediaArtifactGenerator,
    MediaCache,
)
from movie_maker.project import (
    Clip,
    MediaKind,
    PlaybackRate,
    ProjectTime,
    TrackKind,
)
from movie_maker.ui.filmstrip import (
    FILMSTRIP_HEIGHT,
    FILMSTRIP_ROLE,
    FilmstripClip,
    FilmstripDelegate,
    FilmstripFrames,
    FilmstripTitleHover,
    filmstrip_samples,
)
from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_model import TrackKind as UiTrackKind


def test_samples_follow_trimmed_source_span_and_zoom() -> None:
    clip = Clip(
        "clip", TrackKind.VISUAL, "media", "title", ProjectTime.zero(),
        ProjectTime.from_seconds(3), source_in=ProjectTime.from_seconds(2),
        source_out=ProjectTime.from_seconds(8), playback_rate=PlaybackRate(2),
    )
    samples = filmstrip_samples(clip, 324)
    assert [time.to_fractional_seconds() for _, _, time in samples] == [2, 4, 6]
    assert [(left, width) for left, width, _ in samples] == [(0, 108), (108, 108), (216, 108)]
    zoomed = filmstrip_samples(clip, 648)
    assert [time.to_fractional_seconds() for _, _, time in zoomed] == [2, 3, 4, 5, 6, 7]
    assert filmstrip_samples(clip, 1)[0][2] == clip.source_in
    assert len(filmstrip_samples(clip, 1_000_000)) == 128
    photo = replace(clip, source_in=ProjectTime.zero(), source_out=None, playback_rate=PlaybackRate(1))
    assert all(time == ProjectTime.zero() for _, _, time in filmstrip_samples(photo, 648))


def _source(tmp_path: Path):
    path = tmp_path / "A deliberately long sample video title for the timeline filmstrip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)],
        check=True, capture_output=True,
    )
    result = FfprobeAnalyzer().analyze(path)
    assert isinstance(result, MediaAnalysisSuccess)
    return path, result.analysis.to_media_reference("sample")


def test_timestamp_thumbnails_are_distinct_cached_and_preserve_source(tmp_path):
    path, media = _source(tmp_path)
    before = (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    generator = MediaArtifactGenerator(MediaCache(tmp_path / "cache"))
    paths = [
        generator.create_thumbnail(media, Event(), source_time=ProjectTime.from_seconds(second))
        for second in (0, 1, 2)
    ]
    assert len(set(paths)) == 3
    assert len({sha256(item.read_bytes()).hexdigest() for item in paths}) == 3
    assert all(not QImage.fromData(item.read_bytes()).isNull() for item in paths)
    assert generator.create_thumbnail(media, Event(), source_time=ProjectTime.zero()) == paths[0]
    assert (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before


def test_real_timeline_loads_frames_and_elides_caption(qtbot, tmp_path):
    path, _ = _source(tmp_path)
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.import_media_files((str(path),))
    window.controller.add_selected_to_timeline()
    window.show()
    view = window._timeline_lists[UiTrackKind.VISUAL]
    try:
        qtbot.waitUntil(lambda: len(window._filmstrip_frames._frames) >= 3, timeout=15000)
        frames = window._filmstrip_frames._frames.values()
        assert all(not image.isNull() for image in frames)
        assert len({image.toImage().cacheKey() for image in frames}) >= 3
        item = view.item(0)
        data = item.data(FILMSTRIP_ROLE)
        assert isinstance(data, FilmstripClip)
        assert data.media.kind is MediaKind.VIDEO
        assert item.sizeHint().height() == FILMSTRIP_HEIGHT
        font = FilmstripDelegate.caption_font(view.font())
        assert font.pixelSize() == 11
        assert QFontMetrics(font).elidedText(data.clip.label, Qt.TextElideMode.ElideRight, 80).endswith("…")
        before = window.controller.media_project
        history = window.controller.history_count
        window.controller.set_timeline_zoom(200)
        QApplication.processEvents()
        assert window.controller.media_project == before
        assert window.controller.history_count == history
    finally:
        window._allow_close = True
        window.close()


def test_full_title_waits_two_seconds_and_leaving_cancels(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    window.show()
    QApplication.processEvents()
    view = window._timeline_lists[UiTrackKind.VISUAL]
    hover = view.findChild(FilmstripTitleHover)
    assert hover is not None
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *args: shown.append(args[1]))
    point = view.visualItemRect(view.item(0)).center()
    def move(point):
        QApplication.sendEvent(view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        ))

    move(point)
    assert hover._timer.interval() == 2000
    assert hover._timer.isActive()
    qtbot.wait(1000)
    assert shown == []
    assert hover._timer.isActive()
    qtbot.waitUntil(lambda: len(shown) == 1, timeout=1800)
    assert window.controller.state.visual_clips[0].label in shown[0]
    shown.clear()
    move(point + QPoint(2, 0))
    QApplication.sendEvent(view.viewport(), QEvent(QEvent.Type.Leave))
    assert not hover._timer.isActive()
    window._allow_close = True
    window.close()


def test_destroying_view_cancels_unfinished_frame_workers(qapp, tmp_path, monkeypatch):
    _, media = _source(tmp_path)
    parent = QObject()
    frames = FilmstripFrames(parent)
    frames._artifacts = MediaArtifactGenerator(MediaCache(tmp_path / "cache"))
    started = Event()

    def delayed(_media, cancel, **_kwargs):
        started.set()
        cancel.wait(3)
        raise RuntimeError("cancelled test frame")

    monkeypatch.setattr(frames._artifacts, "create_thumbnail", delayed)
    frames.frame(media, ProjectTime.zero())
    assert started.wait(1)
    handle = next(iter(frames._pending.values()))
    parent.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert handle.cancel_event.is_set()
    assert handle.wait(1) is not None
    assert frames._closed


def test_failed_frames_are_cached_without_repeated_decode(qapp, qtbot, tmp_path, monkeypatch):
    _, media = _source(tmp_path)
    parent = QObject()
    frames = FilmstripFrames(parent)
    frames._artifacts = MediaArtifactGenerator(MediaCache(tmp_path / "cache"))
    attempts = []

    def fail(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("invalid frame")

    monkeypatch.setattr(frames._artifacts, "create_thumbnail", fail)
    try:
        assert frames.frame(media, ProjectTime.zero()) is None
        qtbot.waitUntil(lambda: bool(frames._frames))
        assert frames.frame(media, ProjectTime.zero()).isNull()
        assert frames.frame(media, ProjectTime.zero()).isNull()
        assert attempts == [1]
    finally:
        frames.close()
