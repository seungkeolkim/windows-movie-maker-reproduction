"""Shared, shell-free process boundary for local FFmpeg tools."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from threading import Event
from typing import Protocol


class ProcessRunner(Protocol):
    """Run one process and return captured byte streams."""

    def __call__(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]: ...


class RunningProcess(Protocol):
    returncode: int | None

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class ProcessLauncher(Protocol):
    def __call__(self, arguments: Sequence[str]) -> RunningProcess: ...


class ProcessCancelled(RuntimeError):
    pass


def run_process(
    arguments: Sequence[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    """Run an argv sequence without a command shell or writable stdin."""

    return subprocess.run(
        tuple(arguments),
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
        shell=False,
        timeout=timeout,
    )


def resolve_media_tool(name: str) -> str:
    """Resolve an FFmpeg-family executable using the documented runtime order."""

    executable_name = f"{name}.exe" if os.name == "nt" else name
    configured_directory = os.environ.get("MOVIE_MAKER_FFMPEG_DIR")
    if configured_directory:
        return str(Path(configured_directory) / executable_name)

    repository_root = Path(__file__).resolve().parents[3]
    bundled_candidate = repository_root / "tools" / "ffmpeg" / "bin" / executable_name
    if bundled_candidate.is_file():
        return str(bundled_candidate)

    return shutil.which(name) or name


def launch_process(arguments: Sequence[str]) -> RunningProcess:
    return subprocess.Popen(
        tuple(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


def run_cancellable_process(
    arguments: Sequence[str],
    cancel: Event,
    *,
    timeout: float,
    terminate_timeout: float = 0.5,
    launcher: ProcessLauncher = launch_process,
) -> subprocess.CompletedProcess[bytes]:
    """Collect one shell-free process and terminate/kill it on cancellation or timeout."""

    if timeout <= 0 or terminate_timeout <= 0:
        raise ValueError("Process timeouts must be positive.")
    process = launcher(arguments)
    deadline = time.monotonic() + timeout
    while True:
        if cancel.is_set():
            _stop_process(process, terminate_timeout)
            raise ProcessCancelled("The media job was cancelled.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(process, terminate_timeout)
            raise subprocess.TimeoutExpired(tuple(arguments), timeout)
        try:
            stdout, stderr = process.communicate(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue
        return subprocess.CompletedProcess(tuple(arguments), process.returncode or 0, stdout, stderr)


def _stop_process(process: RunningProcess, timeout: float) -> None:
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.communicate()
