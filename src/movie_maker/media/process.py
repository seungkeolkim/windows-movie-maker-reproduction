"""Shared, shell-free process boundary for local FFmpeg tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class ProcessRunner(Protocol):
    """Run one process and return captured byte streams."""

    def __call__(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]: ...


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
