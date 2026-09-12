from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest

from movie_maker.exporting import (
    ExportBusyError,
    ExportCancelled,
    ExportCoordinator,
    ExportPreset,
    ExportProgress,
    ExportProgressStage,
    ExportSucceeded,
    ExportVerification,
    build_export_plan,
)
from movie_maker.project import ProjectTime

from .test_plan import export_project


class _BlockingRunner:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.cancelled: Event | None = None

    def run(self, plan, cancelled: Event, progress_callback):
        self.cancelled = cancelled
        self.started.set()
        progress_callback(ExportProgress(plan, 10, 1.0, 9.0, ExportProgressStage.ENCODING))
        self.release.wait(2.0)
        if cancelled.is_set():
            return ExportCancelled(plan)
        return ExportSucceeded(
            plan,
            plan.target_path,
            ExportVerification(640, 480, ProjectTime.from_seconds(3), 100),
            2.0,
        )


def _plan(tmp_path: Path):
    return build_export_plan(
        export_project("video.mp4", "photo.png", "music.wav"),
        str(tmp_path / "result.mp4"),
        ExportPreset.ORIGINAL,
    )


def test_coordinator_rejects_duplicate_and_delivers_one_current_result(tmp_path: Path) -> None:
    runner = _BlockingRunner()
    coordinator = ExportCoordinator(runner)
    progress = []
    results = []

    job_id = coordinator.start(
        _plan(tmp_path),
        lambda job, value: progress.append((job, value)),
        lambda job, value: results.append((job, value)),
    )
    assert runner.started.wait(1.0)
    with pytest.raises(ExportBusyError):
        coordinator.start(
            _plan(tmp_path),
            lambda job, value: progress.append((job, value)),
            lambda job, value: results.append((job, value)),
        )
    runner.release.set()

    for _ in range(100):
        if results:
            break
        Event().wait(0.01)

    assert progress[0][0] == job_id
    assert results[0][0] == job_id
    assert isinstance(results[0][1], ExportSucceeded)
    assert not coordinator.active
    coordinator.close()


def test_cancel_and_close_suppress_or_deliver_only_safe_terminal_state(tmp_path: Path) -> None:
    runner = _BlockingRunner()
    coordinator = ExportCoordinator(runner)
    results = []
    coordinator.start(
        _plan(tmp_path),
        lambda job, progress: None,
        lambda job, value: results.append((job, value)),
    )
    assert runner.started.wait(1.0)
    assert coordinator.cancel()
    assert runner.cancelled is not None and runner.cancelled.is_set()
    runner.release.set()
    for _ in range(100):
        if results:
            break
        Event().wait(0.01)
    assert isinstance(results[0][1], ExportCancelled)

    second = _BlockingRunner()
    closed = ExportCoordinator(second)
    late_results = []
    closed.start(
        _plan(tmp_path),
        lambda job, progress: None,
        lambda job, value: late_results.append((job, value)),
    )
    assert second.started.wait(1.0)
    second.release.set()
    closed.close()
    assert late_results == []
    assert closed.closed
