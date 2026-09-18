"""Asynchronous source-frame strips and compact timeline captions."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from html import escape
from math import ceil

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QHelpEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
)

from movie_maker.media import MediaArtifactGenerator, MediaCache, PriorityMediaQueue
from movie_maker.media.background import JobHandle, JobPriority, JobResult, JobState
from movie_maker.project import ApplicationPaths, Clip, MediaReference, ProjectTime
from movie_maker.runtime import W10Runtime

FILMSTRIP_ROLE = int(Qt.ItemDataRole.UserRole) + 20
FILMSTRIP_HEIGHT = 84
TITLE_HOVER_DELAY_MS = 2000


@dataclass(frozen=True, slots=True)
class FilmstripClip:
    clip: Clip
    media: MediaReference
    available: bool = True


def filmstrip_samples(clip: Clip, width: int) -> tuple[tuple[int, int, ProjectTime], ...]:
    """Map equally spaced displayed tiles onto the clip's trimmed source interval."""
    if width <= 0:
        return ()
    count = min(128, max(1, ceil(width / 108)))
    start = clip.source_in.nanoseconds
    span = max(0, (clip.source_out.nanoseconds - start) if clip.source_out else 0)
    return tuple(
        (
            width * index // count,
            width * (index + 1) // count - width * index // count,
            ProjectTime(start + span * index // count),
        )
        for index in range(count)
    )


class FilmstripFrames(QObject):
    """Request only visible tiles, sharing the application's media queue and cache."""

    changed = Signal()
    _completed = Signal(object)

    def __init__(self, parent: QObject, runtime: W10Runtime | None = None) -> None:
        super().__init__(parent)
        self._queue = runtime.media_queue if runtime else None
        self._artifacts = runtime.artifacts if runtime else None
        self._owns_queue = runtime is None
        self._frames: OrderedDict[str, QPixmap] = OrderedDict()
        self._pending: dict[str, JobHandle | None] = {}
        self._closed = False
        self._generation = 0
        self._completed.connect(self._accept, Qt.ConnectionType.QueuedConnection)
        parent.destroyed.connect(lambda: self.close())

    def frame(self, media: MediaReference, time: ProjectTime) -> QPixmap | None:
        if self._closed or media.primary_stream_index is None:
            return QPixmap()
        if self._artifacts is None:
            cache = MediaCache(ApplicationPaths.default().media_cache_root)
            self._artifacts = MediaArtifactGenerator(cache)
        try:
            key = self._artifacts.thumbnail_key(media, source_time=time)
        except (OSError, ValueError):
            return QPixmap()
        if key.digest in self._frames:
            self._frames.move_to_end(key.digest)
            return self._frames[key.digest]
        if key.digest in self._pending or len(self._pending) >= 64:
            return None
        if self._queue is None:
            self._queue = PriorityMediaQueue(self._artifacts.cache, workers=2)
        self._pending[key.digest] = None
        artifacts = self._artifacts
        generation = self._generation
        self._pending[key.digest] = self._queue.submit(
            key, JobPriority.VISIBLE_THUMBNAIL,
            lambda cancel: artifacts.create_thumbnail(media, cancel, source_time=time),
            callback=lambda result: self._publish(generation, result),
        )
        return None

    def _publish(self, generation: int, result: JobResult) -> None:
        if self._closed:
            return
        try:
            self._completed.emit((generation, result))
        except RuntimeError:
            # Qt may destroy the receiver while a worker is finishing its callback.
            return

    def _accept(self, payload: tuple[int, JobResult]) -> None:
        generation, result = payload
        if generation != self._generation:
            return
        self._pending.pop(result.key.digest, None)
        if self._closed or not result.key.source_is_current():
            return
        pixmap = QPixmap()
        ready = (
            result.state is JobState.READY and result.path is not None
            and pixmap.load(str(result.path))
        )
        self._frames[result.key.digest] = pixmap if ready else QPixmap()
        while len(self._frames) > 256:
            self._frames.popitem(last=False)
        self.changed.emit()

    def reset(self) -> None:
        self._generation += 1
        for handle in self._pending.values():
            if handle is not None:
                handle.cancel()
        self._pending.clear()
        self._frames.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.reset()
        if self._owns_queue and self._queue is not None:
            self._queue.shutdown()


class FilmstripDelegate(QStyledItemDelegate):
    """Draw frames above a single small, elided title without changing hit targets."""

    def __init__(self, view: QListWidget, frames: FilmstripFrames) -> None:
        super().__init__(view)
        self._view = view
        self._frames = frames
        frames.changed.connect(view.viewport().update)

    @staticmethod
    def caption_font(base: QFont) -> QFont:
        font = QFont(base)
        font.setPixelSize(11)
        font.setBold(False)
        return font

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        data = index.data(FILMSTRIP_ROLE)
        if not isinstance(data, FilmstripClip):
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setClipRect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect.adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QColor("#dcecff" if selected else "#eef2f6"))
        image_rect = rect.adjusted(2, 2, -2, -22)
        painter.fillRect(image_rect, QColor("#26313d"))
        viewport = self._view.viewport().rect()
        for offset, width, time in filmstrip_samples(data.clip, image_rect.width()):
            tile = QRect(image_rect.left() + offset, image_rect.top(), width, image_rect.height())
            if not tile.intersects(viewport):
                continue
            pixmap = self._frames.frame(data.media, time) if data.available else None
            if pixmap is not None and not pixmap.isNull():
                scaled = pixmap.scaled(
                    tile.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                painter.save()
                painter.setClipRect(tile, Qt.ClipOperation.IntersectClip)
                painter.drawPixmap(
                    tile.center().x() - scaled.width() // 2,
                    tile.center().y() - scaled.height() // 2, scaled,
                )
                painter.restore()
            elif width >= 70:
                painter.setPen(QColor("#c3ccd6"))
                painter.setFont(self.caption_font(option.font))
                message = "장면 준비 중" if pixmap is None else "미리 보기 없음"
                painter.drawText(
                    tile, Qt.AlignmentFlag.AlignCenter, message if data.available else "원본 없음"
                )
            painter.setPen(QColor("#536270"))
            painter.drawLine(tile.topRight(), tile.bottomRight())
        title_rect = QRect(rect.left() + 5, image_rect.bottom() + 3, max(0, rect.width() - 10), 17)
        font = self.caption_font(option.font)
        painter.setFont(font)
        painter.setPen(QColor("#183651" if selected else "#485564"))
        title = QFontMetrics(font).elidedText(data.clip.label, Qt.TextElideMode.ElideRight, title_rect.width())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        painter.setPen(QColor("#2878ce" if selected else "#bac6d2"))
        painter.drawRect(rect)
        painter.restore()

    def helpEvent(
        self, event: QHelpEvent, view: QAbstractItemView,
        option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        if isinstance(index.data(FILMSTRIP_ROLE), FilmstripClip):
            return True  # The view's two-second timer owns these tooltips.
        return super().helpEvent(event, view, option, index)


class FilmstripTitleHover(QObject):
    """Show the complete caption after two seconds over the same clip."""

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self._view = view
        self._point = QPoint()
        self._clip_id: str | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TITLE_HOVER_DELAY_MS)
        self._timer.timeout.connect(self._show)
        view.setMouseTracking(True)
        view.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(event, QMouseEvent) and event.type() is QEvent.Type.MouseMove:
            point = event.position().toPoint()
            data = self._view.indexAt(point).data(FILMSTRIP_ROLE)
            clip_id = data.clip.clip_id if isinstance(data, FilmstripClip) else None
            if point != self._point or clip_id != self._clip_id:
                self._timer.stop()
                QToolTip.hideText()
                self._point, self._clip_id = point, clip_id
                if clip_id is not None and not event.buttons():
                    self._timer.start()
        elif event.type() in {
            QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel,
            QEvent.Type.Hide, QEvent.Type.DragEnter,
        }:
            self._timer.stop()
            self._clip_id = None
            QToolTip.hideText()
        return super().eventFilter(watched, event)

    def _show(self) -> None:
        index = self._view.indexAt(self._point)
        data = index.data(FILMSTRIP_ROLE)
        if isinstance(data, FilmstripClip) and data.clip.clip_id == self._clip_id:
            QToolTip.showText(
                self._view.viewport().mapToGlobal(self._point),
                f"<qt>{escape(data.clip.label)}</qt>", self._view.viewport(),
                self._view.visualRect(index), 10000,
            )
