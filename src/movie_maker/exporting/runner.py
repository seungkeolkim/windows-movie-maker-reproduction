"""Cancellable FFmpeg export execution with monotonic machine-readable progress."""

from __future__ import annotations

import errno
import queue
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event, Thread
from typing import BinaryIO, Protocol

from movie_maker.exporting.contracts import (
    ExportCancelled,
    ExportErrorCode,
    ExportFailed,
    ExportProgress,
    ExportProgressStage,
    ExportResult,
    ExportSucceeded,
)
from movie_maker.exporting.filesystem import (
    ExportFileError,
    ExportFileOperations,
    LocalExportFileOperations,
)
from movie_maker.exporting.plan import ExportPlan, ffmpeg_export_arguments
from movie_maker.exporting.verifier import (
    ExportVerificationCancelled,
    ExportVerificationError,
    ExportVerifier,
    FfprobeExportVerifier,
)
from movie_maker.media.process import resolve_media_tool

type ProgressCallback = Callable[[ExportProgress], None]
MAX_CAPTURED_STDERR_BYTES = 64 * 1024


class ExportProcess(Protocol):
    @property
    def returncode(self) -> int | None: ...

    def poll(self) -> int | None: ...

    def read_progress(self, timeout: float) -> bytes | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def stderr(self) -> bytes: ...


class ExportProcessLauncher(Protocol):
    def __call__(self, arguments: Sequence[str]) -> ExportProcess: ...


class _SubprocessExportProcess:
    def __init__(self, arguments: Sequence[str]) -> None:
        self._process = subprocess.Popen(
            tuple(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        self._progress: queue.Queue[bytes | None] = queue.Queue()
        self._stderr_chunks: list[bytes] = []
        self._stdout_thread = Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            daemon=True,
        )
        self._stderr_thread = Thread(
            target=self._read_stderr,
            args=(self._process.stderr,),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    def _read_stdout(self, stream: BinaryIO) -> None:
        for line in iter(stream.readline, b""):
            self._progress.put(line)
        self._progress.put(None)

    def _read_stderr(self, stream: BinaryIO) -> None:
        captured = bytearray()
        while chunk := stream.read(8_192):
            captured.extend(chunk)
            if len(captured) > MAX_CAPTURED_STDERR_BYTES:
                del captured[:-MAX_CAPTURED_STDERR_BYTES]
        self._stderr_chunks.append(bytes(captured))

    def poll(self) -> int | None:
        return self._process.poll()

    def read_progress(self, timeout: float) -> bytes | None:
        try:
            return self._progress.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def stderr(self) -> bytes:
        self._stderr_thread.join(timeout=1.0)
        return b"".join(self._stderr_chunks)


def _launch(arguments: Sequence[str]) -> ExportProcess:
    return _SubprocessExportProcess(arguments)


def _detail(stderr: bytes) -> str | None:
    text = stderr.decode("utf-8", errors="replace").strip()
    return text[-2000:] or None


def _classify_failure(stderr: bytes) -> ExportErrorCode:
    message = stderr.decode("utf-8", errors="replace").casefold()
    if "no space left" in message or "disk full" in message:
        return ExportErrorCode.DISK_FULL
    if "no such filter" in message or "filter not found" in message:
        return ExportErrorCode.FILTER_UNAVAILABLE
    if "unknown encoder" in message or "encoder not found" in message:
        return ExportErrorCode.ENCODER_UNAVAILABLE
    if "no such file" in message:
        return ExportErrorCode.SOURCE_NOT_FOUND
    if "permission denied" in message or "i/o error" in message:
        return ExportErrorCode.IO_ERROR
    if "invalid data" in message or "unsupported" in message or "decoder" in message:
        return ExportErrorCode.UNSUPPORTED_MEDIA
    return ExportErrorCode.PROCESS_FAILED


def _message_for_code(code: ExportErrorCode) -> str:
    return {
        ExportErrorCode.DISK_FULL: "디스크 공간이 부족합니다. 공간을 확보한 뒤 다시 시도하세요.",
        ExportErrorCode.FILTER_UNAVAILABLE: "필수 FFmpeg 필터가 없습니다. 실행 환경을 점검하세요.",
        ExportErrorCode.ENCODER_UNAVAILABLE: "H.264 또는 AAC 인코더가 없습니다.",
        ExportErrorCode.SOURCE_NOT_FOUND: "출력에 필요한 원본 미디어를 찾을 수 없습니다.",
        ExportErrorCode.IO_ERROR: "동영상 파일을 기록하는 중 I/O 오류가 발생했습니다.",
        ExportErrorCode.UNSUPPORTED_MEDIA: "지원하지 않는 미디어 또는 스트림 조합입니다.",
        ExportErrorCode.PROCESS_FAILED: "FFmpeg가 동영상 출력을 완료하지 못했습니다.",
    }[code]


def _stop(process: ExportProcess) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        if process.poll() is not None:
            return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            if process.poll() is not None:
                return
        try:
            process.wait()
        except OSError:
            pass


class FfmpegExportRunner:
    """Run and verify one plan, preserving the target until atomic commit."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        launcher: ExportProcessLauncher = _launch,
        file_operations: ExportFileOperations | None = None,
        verifier: ExportVerifier | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        minimum_timeout: float = 60.0,
        timeout_factor: float = 20.0,
    ) -> None:
        self._executable = executable or resolve_media_tool("ffmpeg")
        self._launcher = launcher
        self._files = file_operations or LocalExportFileOperations()
        self._verifier = verifier or FfprobeExportVerifier(ffmpeg_executable=self._executable)
        self._monotonic = monotonic
        self._minimum_timeout = minimum_timeout
        self._timeout_factor = timeout_factor

    def run(
        self,
        plan: ExportPlan,
        cancelled: Event,
        progress_callback: ProgressCallback,
    ) -> ExportResult:
        source_paths = dict.fromkeys(
            (
                *(source.source_path for source in plan.video_sources),
                *(source.source_path for source in plan.audio_graph.sources),
            )
        )
        missing = next((path for path in source_paths if not Path(path).is_file()), None)
        if missing is not None:
            return ExportFailed(
                plan,
                ExportErrorCode.SOURCE_NOT_FOUND,
                "출력에 필요한 원본 미디어를 찾을 수 없습니다. 다시 연결하세요.",
                missing,
            )
        if cancelled.is_set():
            return ExportCancelled(plan)
        try:
            temporary_path = self._files.prepare(plan)
        except ExportFileError as error:
            return ExportFailed(plan, error.code, str(error), error.detail)
        except OSError as error:
            code = ExportErrorCode.DISK_FULL if error.errno == errno.ENOSPC else ExportErrorCode.IO_ERROR
            return ExportFailed(plan, code, "출력 임시 파일을 준비하지 못했습니다.", str(error))

        started = self._monotonic()
        last_percent = 0

        def publish(percent: int, stage: ExportProgressStage) -> None:
            nonlocal last_percent
            percent = max(last_percent, min(percent, 100))
            last_percent = percent
            elapsed = max(0.0, self._monotonic() - started)
            eta = None if percent <= 0 or percent >= 100 else elapsed * (100 - percent) / percent
            progress_callback(ExportProgress(plan, percent, elapsed, eta, stage))

        publish(0, ExportProgressStage.ENCODING)
        arguments = ffmpeg_export_arguments(self._executable, plan, temporary_path)
        try:
            process = self._launcher(arguments)
        except FileNotFoundError as error:
            self._files.cleanup(temporary_path)
            return ExportFailed(
                plan,
                ExportErrorCode.FFMPEG_NOT_FOUND,
                "FFmpeg를 찾을 수 없습니다. 실행 환경을 점검하세요.",
                str(error),
            )
        except OSError as error:
            self._files.cleanup(temporary_path)
            return ExportFailed(
                plan,
                ExportErrorCode.FFMPEG_NOT_FOUND,
                "FFmpeg를 실행할 수 없습니다. 실행 환경을 점검하세요.",
                str(error),
            )

        duration_us = max(1, plan.duration.nanoseconds // 1_000)
        timeout = max(
            self._minimum_timeout,
            float(plan.duration.to_fractional_seconds()) * self._timeout_factor,
        )
        deadline = started + timeout
        try:
            while process.poll() is None:
                if cancelled.is_set():
                    _stop(process)
                    self._files.cleanup(temporary_path)
                    return ExportCancelled(plan)
                if self._monotonic() >= deadline:
                    _stop(process)
                    detail = _detail(process.stderr())
                    self._files.cleanup(temporary_path)
                    return ExportFailed(
                        plan,
                        ExportErrorCode.TIMEOUT,
                        "동영상 출력 시간이 초과됐습니다. 다시 시도하세요.",
                        detail,
                        process.returncode,
                    )
                line = process.read_progress(0.05)
                if line is None:
                    continue
                key, separator, raw_value = line.decode("utf-8", errors="replace").strip().partition("=")
                if separator and key in {"out_time_us", "out_time_ms"}:
                    try:
                        out_time_us = max(0, int(raw_value))
                    except ValueError:
                        continue
                    publish(min(99, out_time_us * 100 // duration_us), ExportProgressStage.ENCODING)
            process.wait()
            stderr = process.stderr()
            if process.returncode != 0:
                code = _classify_failure(stderr)
                self._files.cleanup(temporary_path)
                return ExportFailed(
                    plan,
                    code,
                    _message_for_code(code),
                    _detail(stderr),
                    process.returncode,
                )
            if cancelled.is_set():
                self._files.cleanup(temporary_path)
                return ExportCancelled(plan)
            publish(99, ExportProgressStage.VERIFYING)
            try:
                verification = self._verifier.verify(plan, temporary_path, cancelled)
            except ExportVerificationCancelled:
                self._files.cleanup(temporary_path)
                return ExportCancelled(plan)
            except ExportVerificationError as error:
                self._files.cleanup(temporary_path)
                return ExportFailed(plan, error.code, str(error), error.detail)
            if cancelled.is_set():
                self._files.cleanup(temporary_path)
                return ExportCancelled(plan)
            try:
                self._files.commit(plan, temporary_path)
            except ExportFileError as error:
                self._files.cleanup(temporary_path)
                return ExportFailed(plan, error.code, str(error), error.detail)
            publish(100, ExportProgressStage.COMPLETE)
            return ExportSucceeded(
                plan,
                plan.target_path,
                verification,
                max(0.0, self._monotonic() - started),
            )
        except OSError as error:
            _stop(process)
            self._files.cleanup(temporary_path)
            code = ExportErrorCode.DISK_FULL if error.errno == errno.ENOSPC else ExportErrorCode.IO_ERROR
            return ExportFailed(plan, code, "동영상 출력 중 파일 오류가 발생했습니다.", str(error))
        except Exception as error:  # noqa: BLE001 - worker boundary becomes typed failure
            try:
                if process.poll() is None:
                    _stop(process)
            finally:
                self._files.cleanup(temporary_path)
            return ExportFailed(
                plan,
                ExportErrorCode.INTERNAL_ERROR,
                "동영상 출력 작업에서 예기치 않은 오류가 발생했습니다.",
                str(error),
            )
