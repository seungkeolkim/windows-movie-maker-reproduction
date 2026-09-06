"""Qt application bootstrap and runtime diagnostics."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib.metadata import version

import numpy
import platformdirs
import pydantic
from PySide6.QtCore import QCoreApplication, QLibraryInfo, qVersion
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from movie_maker import __version__
from movie_maker.ui.main_window import MainWindow


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


def run(*, check_only: bool = False) -> int:
    """Run the environment check or show the interactive product mock-up."""

    app = create_application()
    if check_only:
        print("\n".join(runtime_report()))
        return 0

    window = MainWindow()
    window.show()
    return app.exec()
