from PySide6.QtWidgets import QDockWidget

from movie_maker.application import MainWindow, runtime_report


def test_main_window_exposes_interactive_mock_workspace(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert "Movie Maker Reproduction" in window.windowTitle()
    assert "W-07 실제 MP4 출력" in window.windowTitle()
    assert window.objectName() == "S-EDITOR"
    assert window.minimumWidth() == 1024
    assert window.minimumHeight() == 640
    assert window.findChild(QDockWidget, "S-LIBRARY") is not None
    assert window.findChild(QDockWidget, "S-INSPECTOR") is not None


def test_runtime_report_contains_core_versions(qapp) -> None:
    report = runtime_report()

    assert any(line.startswith("Python: 3.13.") for line in report)
    assert any(line.startswith("Base interpreter: ") for line in report)
    assert any(line.startswith("Qt: 6.11.") for line in report)
    assert any(line.startswith("PySide6: 6.11.") for line in report)
