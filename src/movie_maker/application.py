"""Qt application bootstrap and the temporary environment smoke-test window."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib.metadata import version

import numpy
import platformdirs
import pydantic
from PySide6.QtCore import QCoreApplication, QLibraryInfo, Qt, qVersion
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from movie_maker import __version__


class MainWindow(QMainWindow):
    """Minimal window proving that the packaged Qt runtime can start."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Movie Maker Reproduction")
        self.resize(760, 420)

        message = QLabel(
            "실행 환경 준비가 완료되었습니다.\n\n"
            "영상 편집 UI는 다음 개발 단계에서 이 창에 추가됩니다."
        )
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        self.setCentralWidget(message)


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
    """Run the environment check or show the temporary application window."""

    app = create_application()
    if check_only:
        print("\n".join(runtime_report()))
        return 0

    window = MainWindow()
    window.show()
    return app.exec()
