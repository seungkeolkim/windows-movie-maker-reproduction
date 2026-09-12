from __future__ import annotations

import json
import subprocess
from pathlib import Path
from threading import Event

import pytest

from movie_maker.exporting import (
    ExportErrorCode,
    ExportPreset,
    ExportVerificationCancelled,
    ExportVerificationError,
    FfprobeExportVerifier,
    build_export_plan,
)

from .test_plan import export_project


class _Process:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        *,
        timeout_hook=None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timeout_hook = timeout_hook
        self.terminated = False
        self.killed = False

    def communicate(self, input=None, timeout=None):
        if self.timeout_hook is not None:
            hook, self.timeout_hook = self.timeout_hook, None
            hook()
            raise subprocess.TimeoutExpired("verify", timeout)
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def _plan(tmp_path: Path):
    return build_export_plan(
        export_project("video.mp4", "photo.png", "music.wav"),
        str(tmp_path / "result.mp4"),
        ExportPreset.ORIGINAL,
    )


def _probe_payload(
    *,
    video_codec: str = "h264",
    audio_codec: str = "aac",
    frame_count: int = 90,
) -> bytes:
    return json.dumps(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "3.000"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": video_codec,
                    "width": 640,
                    "height": 480,
                    "nb_read_frames": str(frame_count),
                },
                {"codec_type": "audio", "codec_name": audio_codec},
            ],
        }
    ).encode()


def test_verifier_requires_expected_metadata_and_full_decode(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    temporary = tmp_path / "temporary.mp4"
    temporary.write_bytes(b"complete mp4 bytes")
    calls = []
    processes = iter((_Process(_probe_payload()), _Process()))

    def launch(arguments):
        calls.append(tuple(arguments))
        return next(processes)

    result = FfprobeExportVerifier(
        ffprobe_executable="custom ffprobe",
        ffmpeg_executable="custom ffmpeg",
        launcher=launch,
    ).verify(plan, str(temporary), Event())

    assert (result.width, result.height, result.video_codec, result.audio_codec) == (
        640,
        480,
        "h264",
        "aac",
    )
    assert calls[0][0] == "custom ffprobe"
    assert "-count_frames" in calls[0]
    assert calls[1][0] == "custom ffmpeg"
    assert calls[1][-2:] == ("null", "-")
    assert result.frame_count == plan.output_frame_count


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        (_probe_payload(video_codec="vp9"), ExportErrorCode.VALIDATION_FAILED),
        (_probe_payload(frame_count=89), ExportErrorCode.VALIDATION_FAILED),
        (b"not-json", ExportErrorCode.VALIDATION_FAILED),
    ),
)
def test_invalid_probe_results_are_typed(
    tmp_path: Path,
    payload: bytes,
    code: ExportErrorCode,
) -> None:
    plan = _plan(tmp_path)
    temporary = tmp_path / "temporary.mp4"
    temporary.write_bytes(b"bytes")
    verifier = FfprobeExportVerifier(launcher=lambda arguments: _Process(payload))

    with pytest.raises(ExportVerificationError) as raised:
        verifier.verify(plan, str(temporary), Event())

    assert raised.value.code is code


def test_missing_probe_and_cancelled_validation_are_distinct(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    temporary = tmp_path / "temporary.mp4"
    temporary.write_bytes(b"bytes")

    def missing(arguments):
        raise FileNotFoundError("ffprobe")

    with pytest.raises(ExportVerificationError) as absent:
        FfprobeExportVerifier(launcher=missing).verify(plan, str(temporary), Event())
    assert absent.value.code is ExportErrorCode.FFPROBE_NOT_FOUND

    cancelled = Event()
    process = _Process(timeout_hook=cancelled.set)
    with pytest.raises(ExportVerificationCancelled):
        FfprobeExportVerifier(launcher=lambda arguments: process).verify(
            plan,
            str(temporary),
            cancelled,
        )
    assert process.terminated
