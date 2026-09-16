"""Local application diagnostics independent from the native launcher."""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

from movie_maker.project import ApplicationPaths

LOG_RETENTION_DAYS = 14
MAXIMUM_LOG_BYTES = 10 * 1024 * 1024
_LOGGER_NAME = "movie_maker"
_SECRET_PATTERN = re.compile(
    r"(?i)(password|token|secret|apikey|api_key)(\s*[=:]\s*)([^\s,;]+)"
)
_active_handler: logging.FileHandler | None = None
_previous_exception_hook: (
    Callable[[type[BaseException], BaseException, TracebackType | None], None] | None
) = None


class _PrivateDataFormatter(logging.Formatter):
    """Redact common local identifiers from a fully formatted record."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        profile = os.environ.get("USERPROFILE")
        if profile:
            text = re.sub(re.escape(profile), "%USERPROFILE%", text, flags=re.IGNORECASE)
        return _SECRET_PATTERN.sub(r"\1\2<redacted>", text)


def _prune_logs(log_root: Path, *, now: datetime) -> None:
    cutoff = now - timedelta(days=LOG_RETENTION_DAYS)
    entries: list[tuple[Path, float, int]] = []
    for path in log_root.glob("app-*.log"):
        try:
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime, UTC)
            if modified < cutoff:
                path.unlink(missing_ok=True)
                continue
            entries.append((path, stat.st_mtime, stat.st_size))
        except OSError:
            continue
    total = sum(size for _path, _modified, size in entries)
    for path, _modified, size in sorted(entries, key=lambda entry: entry[1]):
        if total <= MAXIMUM_LOG_BYTES:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size


def _qt_message_handler(message_type: QtMsgType, context, message: str) -> None:  # type: ignore[no-untyped-def]
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    category = getattr(context, "category", None) or "qt"
    logging.getLogger(f"{_LOGGER_NAME}.qt").log(
        levels.get(message_type, logging.INFO), "%s: %s", category, message
    )


def configure_application_logging(log_root: Path | None = None) -> Path | None:
    """Create a per-run UTF-8 log without ever preventing application startup."""

    global _active_handler, _previous_exception_hook
    close_application_logging()
    root = log_root or ApplicationPaths.default().log_root
    now = datetime.now(UTC)
    try:
        root.mkdir(parents=True, exist_ok=True)
        _prune_logs(root, now=now)
        local_now = now.astimezone()
        path = root / f"app-{local_now:%Y%m%d-%H%M%S}-{os.getpid()}.log"
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        return None

    handler.setFormatter(
        _PrivateDataFormatter(
            "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"
        )
    )
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    _active_handler = handler

    _previous_exception_hook = sys.excepthook

    def log_unhandled_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        logger.critical(
            "Unhandled Python exception",
            exc_info=(exception_type, exception, traceback),
        )
        if _previous_exception_hook is not None:
            _previous_exception_hook(exception_type, exception, traceback)

    sys.excepthook = log_unhandled_exception
    qInstallMessageHandler(_qt_message_handler)
    logger.info("Application logging started")
    return path


def close_application_logging() -> None:
    """Flush the active app log and restore process-wide hooks."""

    global _active_handler, _previous_exception_hook
    if _active_handler is not None:
        logger = logging.getLogger(_LOGGER_NAME)
        logger.info("Application logging stopped")
        logger.removeHandler(_active_handler)
        _active_handler.close()
        _active_handler = None
    qInstallMessageHandler(None)
    if _previous_exception_hook is not None:
        sys.excepthook = _previous_exception_hook
        _previous_exception_hook = None
