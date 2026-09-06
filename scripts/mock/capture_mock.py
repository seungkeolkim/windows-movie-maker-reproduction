"""Capture deterministic screenshots of the interactive Qt mock-up."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QSize

from movie_maker.application import create_application
from movie_maker.ui.dialogs import ExportSettingsDialog
from movie_maker.ui.main_window import MainWindow


def capture_widget(widget, destination: Path) -> None:
    """Process layout events and save one QWidget capture."""

    app = create_application([])
    app.processEvents()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not widget.grab().save(str(destination)):
        raise RuntimeError(f"Could not save mock-up capture: {destination}")


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture Movie Maker mock-up screenshots")
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("docs/mock/screenshots"),
        help="Directory that receives PNG captures.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    options = parse_arguments(arguments)
    app = create_application([])
    window = MainWindow()
    window.resize(QSize(1280, 720))
    window.show()
    app.processEvents()
    capture_widget(window, options.output_directory / "editor-empty-1280x720.png")

    window.controller.load_sample_project()
    for width, height in ((1024, 640), (1280, 720), (1920, 1080)):
        window.resize(QSize(width, height))
        app.processEvents()
        capture_widget(
            window,
            options.output_directory / f"editor-sample-{width}x{height}.png",
        )

    dialog = ExportSettingsDialog(window.controller.state, window)
    dialog.show()
    app.processEvents()
    capture_widget(dialog, options.output_directory / "export-settings-original.png")
    dialog.close()
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
