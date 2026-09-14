"""Windows-first FFmpeg microphone backend for the narration coordinator."""

from __future__ import annotations

import queue
import subprocess
import sys
import wave
from array import array
from pathlib import Path
from threading import Thread

from PySide6.QtMultimedia import QMediaDevices

from movie_maker.creative import (
    CaptureControl,
    InputDevice,
    RecordingErrorCode,
    RecordingFailure,
)
from movie_maker.creative.recording import ProgressCallback
from movie_maker.media.process import resolve_media_tool

_END = object()


class FfmpegInputDeviceBackend:
    """Capture the selected system input as 48 kHz mono signed-16 PCM."""

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or resolve_media_tool("ffmpeg")

    def devices(self) -> tuple[InputDevice, ...]:
        result: list[InputDevice] = []
        for device in QMediaDevices.audioInputs():
            description = device.description().strip()
            raw_id = bytes(device.id().data()).decode("utf-8", errors="replace").strip()
            device_id = description if sys.platform == "win32" else raw_id or description
            if device_id and description:
                result.append(InputDevice(device_id, description))
        return tuple(result)

    def _arguments(self, device: InputDevice) -> tuple[str, ...]:
        base = [self._executable, "-v", "error", "-nostdin"]
        if sys.platform == "win32":
            base.extend(("-f", "dshow", "-i", f"audio={device.device_id}"))
        elif sys.platform == "darwin":
            base.extend(("-f", "avfoundation", "-i", f":{device.device_id}"))
        else:
            base.extend(("-f", "pulse", "-i", device.device_id))
        base.extend(("-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"))
        return tuple(base)

    def capture_wav(
        self,
        device: InputDevice,
        temporary_path: Path,
        control: CaptureControl,
        progress: ProgressCallback,
    ) -> None:
        try:
            process = subprocess.Popen(
                self._arguments(device),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except PermissionError as error:
            raise RecordingFailure(
                RecordingErrorCode.PERMISSION_DENIED,
                "마이크 권한이 거부됐습니다. Windows 개인 정보 설정을 확인하세요.",
            ) from error
        except OSError as error:
            raise RecordingFailure(
                RecordingErrorCode.WRITE_FAILED,
                f"오디오 입력 프로세스를 시작하지 못했습니다: {error}",
            ) from error
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = process.stdout
        stderr = process.stderr
        chunks: queue.Queue[bytes | object] = queue.Queue(maxsize=32)

        def read_stdout() -> None:
            try:
                while data := stdout.read(4_096):
                    chunks.put(data)
            finally:
                chunks.put(_END)

        reader = Thread(target=read_stdout, name="movie-maker-input-reader", daemon=True)
        reader.start()
        frames = 0
        try:
            with wave.open(str(temporary_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48_000)
                while True:
                    if control.cancelled.is_set() or control.finish_requested.is_set():
                        process.terminate()
                    try:
                        chunk = chunks.get(timeout=0.05)
                    except queue.Empty:
                        if process.poll() is not None:
                            break
                        continue
                    if chunk is _END:
                        break
                    assert isinstance(chunk, bytes)
                    if control.cancelled.is_set() or control.paused.is_set():
                        continue
                    usable = chunk[: len(chunk) - len(chunk) % 2]
                    if not usable:
                        continue
                    output.writeframesraw(usable)
                    sample_values = array("h")
                    sample_values.frombytes(usable)
                    if sys.byteorder != "little":
                        sample_values.byteswap()
                    peak = max((abs(value) for value in sample_values), default=0)
                    frames += len(usable) // 2
                    progress(frames, min(100, peak * 100 // 32_767))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            reader.join(timeout=0.5)
        if control.cancelled.is_set() or control.finish_requested.is_set():
            return
        detail = stderr.read().decode("utf-8", errors="replace").strip()[-1_000:]
        lowered = detail.casefold()
        if "permission" in lowered or "access is denied" in lowered:
            raise RecordingFailure(
                RecordingErrorCode.PERMISSION_DENIED,
                "마이크 권한이 거부됐습니다. Windows 개인 정보 설정을 확인하세요.",
            )
        raise RecordingFailure(
            RecordingErrorCode.DEVICE_DISCONNECTED,
            "입력 장치가 분리됐거나 오디오 기록이 중단됐습니다. " + detail,
        )


__all__ = ["FfmpegInputDeviceBackend"]
