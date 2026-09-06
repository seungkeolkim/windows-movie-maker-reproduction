from PySide6.QtWidgets import QLabel

from movie_maker.application import MainWindow, runtime_report


def test_main_window_identifies_environment_shell(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "Movie Maker Reproduction"
    label = window.centralWidget()
    assert isinstance(label, QLabel)
    assert "실행 환경 준비" in label.text()


def test_runtime_report_contains_core_versions(qapp) -> None:
    report = runtime_report()

    assert any(line.startswith("Python: 3.13.") for line in report)
    assert any(line.startswith("Base interpreter: ") for line in report)
    assert any(line.startswith("Qt: 6.11.") for line in report)
    assert any(line.startswith("PySide6: 6.11.") for line in report)
