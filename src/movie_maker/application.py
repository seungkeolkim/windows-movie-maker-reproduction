"""Qt application bootstrap and runtime diagnostics."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from functools import partial
from importlib.metadata import version

import numpy
import platformdirs
import pydantic
from PySide6.QtCore import QCoreApplication, QLibraryInfo, qVersion
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from movie_maker import __version__
from movie_maker.diagnostics import close_application_logging, configure_application_logging
from movie_maker.media import MediaLibrary
from movie_maker.runtime import W10Runtime
from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_controller import MockController


def create_application(arguments: Sequence[str] | None = None) -> QApplication:
    """Return the process-wide QApplication, creating it when necessary."""

    existing = QApplication.instance()
    if existing is not None:
        if not isinstance(existing, QApplication):
            raise RuntimeError("A non-GUI QCoreApplication already exists in this process.")
        return existing

    app = QApplication(list(arguments) if arguments is not None else sys.argv)
    QCoreApplication.setOrganizationName("MovieMakerReproduction")
    QCoreApplication.setApplicationName("Movie Maker Reproduction")
    QCoreApplication.setApplicationVersion(__version__)
    return app


def runtime_report() -> list[str]:
    """Load required native Qt modules and return diagnostic version details."""

    QMediaDevices.audioOutputs()
    QSvgRenderer()
    return [
        f"Python: {sys.version.split()[0]}",
        f"Interpreter: {sys.executable}",
        f"Base interpreter: {sys.base_prefix}",
        f"Qt: {qVersion()}",
        f"Qt plugins: {QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)}",
        f"PySide6: {version('PySide6')}",
        f"NumPy: {numpy.__version__}",
        f"Pydantic: {pydantic.__version__}",
        f"platformdirs: {platformdirs.__version__}",
    ]


def run(
    *,
    check_only: bool = False,
    project_path: str | None = None,
    online: bool = False,
) -> int:
    """Run the environment check or show the desktop editor."""

    log_path = configure_application_logging()
    logger = logging.getLogger(__name__)
    logger.info(
        "Application startup version=%s check_only=%s online=%s log_available=%s",
        __version__,
        check_only,
        online,
        log_path is not None,
    )
    try:
        app = create_application()
        app.setProperty("onlineMode", online)
        if check_only:
            print("\n".join(runtime_report()))
            logger.info("Runtime check completed")
            return 0

        runtime: W10Runtime | None = None
        try:
            runtime = W10Runtime.create()
        except (OSError, RuntimeError, ValueError):
            # App-owned recovery metadata must never prevent direct project editing.
            logger.exception("Recovery and background runtime initialization failed")
            runtime = None
        library = MediaLibrary.create_background_default() if runtime is not None else None
        controller = MockController(media_library=library, runtime=runtime)
        if project_path is not None and not controller.open_project(project_path):
            detail = controller.last_persistence_error or "Unknown project read error."
            logger.error("Startup project could not be opened: %s", detail)
            print(f"Unable to open project: {detail}", file=sys.stderr)
            if runtime is not None:
                runtime.close(clean_exit=True)
            return 2
        window = MainWindow(controller)
        if runtime is not None:
            app.aboutToQuit.connect(partial(runtime.close, clean_exit=True))
        window.show()
        exit_code = app.exec()
        logger.info("Application event loop stopped exit_code=%s", exit_code)
        return exit_code
    except BaseException:
        logger.exception("Application terminated because of an unhandled exception")
        raise
    finally:
        close_application_logging()
