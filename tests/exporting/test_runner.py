from __future__ import annotations

import subprocess
from collections import deque
from pathlib import Path
from threading import Event

from movie_maker.exporting import (
    ExportCancelled,
    ExportErrorCode,
    ExportFailed,
    ExportPreset,
    ExportProgressStage,
    ExportSucceeded,
    ExportVerification,
    FfmpegExportRunner,
    LocalExportFileOperations,
    build_export_plan,
)
from movie_maker.project import ProjectTime

from .test_plan import export_project


class _Process:
    def __init__(
        self,
        lines: tuple[bytes, ...] = (b"out_time_us=1500000\n",),
        *,
        returncode: int = 0,
        stderr: bytes = b"",
        read_hook=None,
        resist_terminate: bool = False,
    ) -> None:
        self._lines = deque(lines)
        self._final_returncode = returncode
        self._returncode: int | None = None
        self._stderr = stderr
        self._read_hook = read_hook
        self._resist_terminate = resist_terminate
        self.terminated = False
        self.killed = False

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def read_progress(self, timeout: float) -> bytes | None:
        if self._read_hook is not None:
            self._read_hook()
            self._read_hook = None
        if self._lines:
            return self._lines.popleft()
        self._returncode = self._final_returncode
        return None

    def wait(self, timeout: float | None = None) -> int:
        if self._resist_terminate and self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        self._returncode = -9 if self.killed else self._final_returncode
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        if not self._resist_terminate:
            self._returncode = -15

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def stderr(self) -> bytes:
        return self._stderr


class _Verifier:
    def __init__(self, verification: ExportVerification) -> None:
        self.verification = verification
        self.calls: list[str] = []

    def verify(self, plan, temporary_path: str, cancelled: Event) -> ExportVerification:
        self.calls.append(temporary_path)
        return self.verification


def _plan(tmp_path: Path, *, overwrite: bool = False):
    video = tmp_path / "한글 video source.mp4"
    photo = tmp_path / "photo source.png"
    music = tmp_path / "music source.wav"
    for source in (video, photo, music):
        source.write_bytes(b"immutable")
    project = export_project(str(video), str(photo), str(music))
    return build_export_plan(
        project,
        str(tmp_path / "result.mp4"),
        ExportPreset.ORIGINAL,
        overwrite_existing=overwrite,
    )


def _verification() -> ExportVerification:
    return ExportVerification(640, 480, ProjectTime.from_seconds(3), 1234)


def test_success_reports_monotonic_progress_and_publishes_after_verification(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    process = _Process((b"out_time_us=300000\n", b"out_time_us=2400000\n"))
    verifier = _Verifier(_verification())
    progress = []
    moment = 0.0

    def monotonic() -> float:
        nonlocal moment
        moment += 0.25
        return moment

    runner = FfmpegExportRunner(
        executable="custom ffmpeg",
        launcher=lambda arguments: process,
        verifier=verifier,
        monotonic=monotonic,
    )

    result = runner.run(plan, Event(), progress.append)

    assert isinstance(result, ExportSucceeded)
    assert Path(plan.target_path).exists()
    assert [item.percent for item in progress] == [0, 10, 80, 99, 100]
    assert progress[-2].stage is ExportProgressStage.VERIFYING
    assert progress[-1].stage is ExportProgressStage.COMPLETE
    assert verifier.calls


def test_existing_target_is_preserved_without_confirmation_and_on_failure(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    target = Path(plan.target_path)
    target.write_bytes(b"existing-good-file")
    runner = FfmpegExportRunner(
        launcher=lambda arguments: _Process(),
        verifier=_Verifier(_verification()),
    )

    blocked = runner.run(plan, Event(), lambda progress: None)

    assert isinstance(blocked, ExportFailed)
    assert blocked.code is ExportErrorCode.TARGET_EXISTS
    assert target.read_bytes() == b"existing-good-file"

    overwrite_plan = _plan(tmp_path, overwrite=True)
    failed = FfmpegExportRunner(
        launcher=lambda arguments: _Process(returncode=1, stderr=b"No space left on device"),
        verifier=_Verifier(_verification()),
    ).run(overwrite_plan, Event(), lambda progress: None)

    assert isinstance(failed, ExportFailed)
    assert failed.code is ExportErrorCode.DISK_FULL
    assert target.read_bytes() == b"existing-good-file"
    assert not tuple(tmp_path.glob("*.partial.mp4"))


def test_cancel_terminates_then_kills_and_removes_temporary_file(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    cancelled = Event()
    process = _Process(read_hook=cancelled.set, resist_terminate=True)
    runner = FfmpegExportRunner(
        launcher=lambda arguments: process,
        verifier=_Verifier(_verification()),
    )

    result = runner.run(plan, cancelled, lambda progress: None)

    assert isinstance(result, ExportCancelled)
    assert process.terminated and process.killed
    assert not Path(plan.target_path).exists()
    assert not tuple(tmp_path.glob("*.partial.mp4"))


def test_missing_source_and_process_failures_are_typed(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    Path(plan.video_sources[0].source_path).unlink()
    missing = FfmpegExportRunner(
        launcher=lambda arguments: _Process(),
        verifier=_Verifier(_verification()),
    ).run(plan, Event(), lambda progress: None)
    assert isinstance(missing, ExportFailed)
    assert missing.code is ExportErrorCode.SOURCE_NOT_FOUND

    plan = _plan(tmp_path)
    cases = (
        (b"No such filter: concat", ExportErrorCode.FILTER_UNAVAILABLE),
        (b"Unknown encoder 'libx264'", ExportErrorCode.ENCODER_UNAVAILABLE),
        (b"Invalid data found when processing input", ExportErrorCode.UNSUPPORTED_MEDIA),
        (b"Permission denied", ExportErrorCode.IO_ERROR),
    )
    for stderr, expected in cases:
        result = FfmpegExportRunner(
            launcher=lambda arguments, value=stderr: _Process(returncode=1, stderr=value),
            verifier=_Verifier(_verification()),
        ).run(plan, Event(), lambda progress: None)
        assert isinstance(result, ExportFailed)
        assert result.code is expected


def test_missing_ffmpeg_invalid_output_folder_and_timeout_are_typed(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    def missing_ffmpeg(arguments):
        raise FileNotFoundError("ffmpeg")

    absent = FfmpegExportRunner(
        launcher=missing_ffmpeg,
        verifier=_Verifier(_verification()),
    ).run(plan, Event(), lambda progress: None)
    assert isinstance(absent, ExportFailed)
    assert absent.code is ExportErrorCode.FFMPEG_NOT_FOUND

    invalid_plan = build_export_plan(
        plan.project,
        str(tmp_path / "missing-folder" / "result.mp4"),
        ExportPreset.ORIGINAL,
    )
    invalid = FfmpegExportRunner(
        launcher=lambda arguments: _Process(),
        verifier=_Verifier(_verification()),
    ).run(invalid_plan, Event(), lambda progress: None)
    assert isinstance(invalid, ExportFailed)
    assert invalid.code is ExportErrorCode.OUTPUT_PATH_INVALID

    process = _Process(lines=(b"progress=continue\n",), resist_terminate=True)
    moment = 0.0

    def monotonic() -> float:
        nonlocal moment
        moment += 1.0
        return moment

    timeout = FfmpegExportRunner(
        launcher=lambda arguments: process,
        verifier=_Verifier(_verification()),
        monotonic=monotonic,
        minimum_timeout=0.1,
        timeout_factor=0.01,
    ).run(plan, Event(), lambda progress: None)
    assert isinstance(timeout, ExportFailed)
    assert timeout.code is ExportErrorCode.TIMEOUT
    assert process.terminated and process.killed


def test_local_file_operations_replace_only_after_confirmed_success(tmp_path: Path) -> None:
    plan = _plan(tmp_path, overwrite=True)
    target = Path(plan.target_path)
    target.write_bytes(b"old")
    files = LocalExportFileOperations()
    temporary = files.prepare(plan)
    Path(temporary).write_bytes(b"new-complete")

    assert target.read_bytes() == b"old"
    files.commit(plan, temporary)

    assert target.read_bytes() == b"new-complete"
    assert not Path(temporary).exists()
