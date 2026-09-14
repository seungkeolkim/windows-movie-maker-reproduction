"""Injectable, background narration recording with atomic WAV publication."""

from __future__ import annotations

import errno
import os
import tempfile
import wave
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event, Lock
from typing import Protocol


class RecordingState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"
    CLOSED = "closed"


class RecordingErrorCode(str, Enum):
    NO_DEVICE = "no_device"
    PERMISSION_DENIED = "permission_denied"
    DEVICE_DISCONNECTED = "device_disconnected"
    WRITE_FAILED = "write_failed"
    DISK_FULL = "disk_full"
    INVALID_STATE = "invalid_state"


class RecordingFailure(RuntimeError):
    """A typed capture or publication failure."""

    def __init__(self, code: RecordingErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class InputDevice:
    """A session-only input selection; its ID is never persisted."""

    device_id: str
    name: str

    def __post_init__(self) -> None:
        if not self.device_id.strip() or not self.name.strip():
            raise ValueError("Input device identity and name cannot be blank.")


@dataclass(frozen=True, slots=True)
class CaptureControl:
    """Thread-safe control events observed by an input backend."""

    paused: Event
    finish_requested: Event
    cancelled: Event


type ProgressCallback = Callable[[int, int], None]


class InputDeviceBackend(Protocol):
    """Injectable capture boundary used by fake and operating-system backends."""

    def devices(self) -> tuple[InputDevice, ...]: ...

    def capture_wav(
        self,
        device: InputDevice,
        temporary_path: Path,
        control: CaptureControl,
        progress: ProgressCallback,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RecordingSnapshot:
    """Observable state without device IDs or temporary paths."""

    state: RecordingState = RecordingState.IDLE
    elapsed_frames: int = 0
    sample_rate: int = 48_000
    input_level_percent: int = 0
    final_path: str | None = None
    error_code: RecordingErrorCode | None = None
    message: str = "녹음 준비"

    @property
    def elapsed_milliseconds(self) -> int:
        return self.elapsed_frames * 1_000 // self.sample_rate


type RecordingCallback = Callable[[RecordingSnapshot], None]


def _classify_os_error(error: OSError) -> RecordingFailure:
    if isinstance(error, PermissionError):
        return RecordingFailure(
            RecordingErrorCode.PERMISSION_DENIED,
            "입력 장치 또는 저장 위치 권한이 거부됐습니다.",
        )
    if error.errno == errno.ENOSPC:
        return RecordingFailure(
            RecordingErrorCode.DISK_FULL,
            "디스크 공간이 부족해 내레이션을 저장하지 못했습니다.",
        )
    return RecordingFailure(
        RecordingErrorCode.WRITE_FAILED,
        f"내레이션 WAV 파일을 기록하지 못했습니다: {error}",
    )


def _verify_wav(path: Path) -> tuple[int, int]:
    try:
        with wave.open(str(path), "rb") as stream:
            frames = stream.getnframes()
            sample_rate = stream.getframerate()
            if (
                frames <= 0
                or sample_rate <= 0
                or stream.getnchannels() not in {1, 2}
                or stream.getsampwidth() not in {1, 2, 3, 4}
            ):
                raise RecordingFailure(
                    RecordingErrorCode.WRITE_FAILED,
                    "녹음 결과가 유효한 WAV 오디오가 아닙니다.",
                )
            return frames, sample_rate
    except RecordingFailure:
        raise
    except (OSError, EOFError, wave.Error) as error:
        raise RecordingFailure(
            RecordingErrorCode.WRITE_FAILED,
            "녹음 결과가 유효한 WAV 오디오가 아닙니다.",
        ) from error


class NarrationRecordingCoordinator:
    """Own one capture worker and publish only a verified complete WAV."""

    def __init__(self, backend: InputDeviceBackend) -> None:
        self._backend = backend
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="movie-maker-narration",
        )
        self._lock = Lock()
        self._snapshot = RecordingSnapshot()
        self._control: CaptureControl | None = None
        self._future: Future[None] | None = None
        self._temporary_path: Path | None = None
        self._target_path: Path | None = None
        self._callback: RecordingCallback | None = None
        self._closed = False

    @property
    def snapshot(self) -> RecordingSnapshot:
        with self._lock:
            return self._snapshot

    def devices(self) -> tuple[InputDevice, ...]:
        devices = self._backend.devices()
        if any(not isinstance(device, InputDevice) for device in devices):
            raise TypeError("Input backends must return InputDevice values.")
        return devices

    def start(
        self,
        device_id: str,
        final_path: str | os.PathLike[str],
        callback: RecordingCallback,
    ) -> None:
        devices = self.devices()
        device = next((item for item in devices if item.device_id == device_id), None)
        if device is None:
            raise RecordingFailure(
                RecordingErrorCode.NO_DEVICE,
                "선택한 입력 장치를 찾을 수 없습니다. 장치 연결을 확인하세요.",
            )
        target = Path(final_path).expanduser().resolve(strict=False)
        if target.suffix.casefold() != ".wav":
            raise RecordingFailure(
                RecordingErrorCode.WRITE_FAILED,
                "내레이션 대상 경로에는 .wav 파일 이름이 필요합니다.",
            )
        if target.exists():
            raise RecordingFailure(
                RecordingErrorCode.WRITE_FAILED,
                "기존 WAV를 보호하기 위해 다른 파일 이름을 선택하세요.",
            )
        with self._lock:
            if self._closed:
                raise RecordingFailure(
                    RecordingErrorCode.INVALID_STATE, "녹음 서비스가 종료됐습니다."
                )
            if self._snapshot.state in {
                RecordingState.RECORDING,
                RecordingState.PAUSED,
                RecordingState.FINALIZING,
            }:
                raise RecordingFailure(
                    RecordingErrorCode.INVALID_STATE, "이미 내레이션을 녹음하고 있습니다."
                )
            try:
                descriptor, name = tempfile.mkstemp(
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".recording.wav",
                )
                os.close(descriptor)
            except OSError as error:
                raise _classify_os_error(error) from error
            temporary = Path(name)
            control = CaptureControl(Event(), Event(), Event())
            self._control = control
            self._temporary_path = temporary
            self._target_path = target
            self._callback = callback
            self._snapshot = RecordingSnapshot(
                RecordingState.RECORDING,
                message="내레이션 녹음 중",
            )
            future = self._executor.submit(
                self._capture,
                device,
                temporary,
                target,
                control,
            )
            self._future = future
        callback(self.snapshot)

    def _capture(
        self,
        device: InputDevice,
        temporary: Path,
        target: Path,
        control: CaptureControl,
    ) -> None:
        try:
            self._backend.capture_wav(
                device,
                temporary,
                control,
                self._publish_progress,
            )
            if control.cancelled.is_set():
                self._finish_cancelled(temporary)
                return
            if not control.finish_requested.is_set():
                raise RecordingFailure(
                    RecordingErrorCode.DEVICE_DISCONNECTED,
                    "입력 장치 연결이 끊어져 녹음이 중단됐습니다.",
                )
            frames, sample_rate = _verify_wav(temporary)
            with temporary.open("rb+") as stream:
                os.fsync(stream.fileno())
            os.link(temporary, target)
            temporary.unlink()
            self._finish_complete(target, frames, sample_rate)
        except RecordingFailure as error:
            self._finish_failed(temporary, error)
        except OSError as error:
            self._finish_failed(temporary, _classify_os_error(error))
        except Exception as error:  # noqa: BLE001 - isolate backend failures
            self._finish_failed(
                temporary,
                RecordingFailure(
                    RecordingErrorCode.WRITE_FAILED,
                    f"내레이션 기록 작업이 실패했습니다: {error}",
                ),
            )

    def _publish_progress(self, frames: int, level_percent: int) -> None:
        with self._lock:
            if self._snapshot.state not in {
                RecordingState.RECORDING,
                RecordingState.PAUSED,
                RecordingState.FINALIZING,
            }:
                return
            self._snapshot = RecordingSnapshot(
                self._snapshot.state,
                max(self._snapshot.elapsed_frames, frames),
                self._snapshot.sample_rate,
                min(100, max(0, level_percent)),
                message=self._snapshot.message,
            )
            callback = self._callback
            snapshot = self._snapshot
        if callback is not None:
            callback(snapshot)

    def pause(self) -> None:
        with self._lock:
            if self._snapshot.state is not RecordingState.RECORDING or self._control is None:
                raise RecordingFailure(
                    RecordingErrorCode.INVALID_STATE, "진행 중인 녹음만 일시 정지할 수 있습니다."
                )
            self._control.paused.set()
            self._snapshot = RecordingSnapshot(
                RecordingState.PAUSED,
                self._snapshot.elapsed_frames,
                self._snapshot.sample_rate,
                self._snapshot.input_level_percent,
                message="내레이션 녹음 일시 정지",
            )
            callback, snapshot = self._callback, self._snapshot
        if callback is not None:
            callback(snapshot)

    def resume(self) -> None:
        with self._lock:
            if self._snapshot.state is not RecordingState.PAUSED or self._control is None:
                raise RecordingFailure(
                    RecordingErrorCode.INVALID_STATE, "일시 정지된 녹음만 계속할 수 있습니다."
                )
            self._control.paused.clear()
            self._snapshot = RecordingSnapshot(
                RecordingState.RECORDING,
                self._snapshot.elapsed_frames,
                self._snapshot.sample_rate,
                self._snapshot.input_level_percent,
                message="내레이션 녹음 중",
            )
            callback, snapshot = self._callback, self._snapshot
        if callback is not None:
            callback(snapshot)

    def finish(self) -> None:
        with self._lock:
            if self._snapshot.state not in {
                RecordingState.RECORDING,
                RecordingState.PAUSED,
            } or self._control is None:
                raise RecordingFailure(
                    RecordingErrorCode.INVALID_STATE, "완료할 녹음이 없습니다."
                )
            self._control.paused.clear()
            self._control.finish_requested.set()
            self._snapshot = RecordingSnapshot(
                RecordingState.FINALIZING,
                self._snapshot.elapsed_frames,
                self._snapshot.sample_rate,
                self._snapshot.input_level_percent,
                message="WAV 파일 확인 및 확정 중",
            )
            callback, snapshot = self._callback, self._snapshot
        if callback is not None:
            callback(snapshot)

    def cancel(self) -> None:
        with self._lock:
            control = self._control
            temporary = self._temporary_path
            if control is not None:
                control.cancelled.set()
                control.paused.clear()
            elif temporary is not None:
                temporary.unlink(missing_ok=True)

    def _finish_cancelled(self, temporary: Path) -> None:
        temporary.unlink(missing_ok=True)
        self._finish_snapshot(
            RecordingSnapshot(RecordingState.CANCELLED, message="내레이션 녹음을 취소했습니다")
        )

    def _finish_complete(self, target: Path, frames: int, sample_rate: int) -> None:
        self._finish_snapshot(
            RecordingSnapshot(
                RecordingState.COMPLETE,
                frames,
                sample_rate,
                final_path=str(target),
                message="내레이션 WAV를 안전하게 확정했습니다",
            )
        )

    def _finish_failed(self, temporary: Path, error: RecordingFailure) -> None:
        temporary.unlink(missing_ok=True)
        self._finish_snapshot(
            RecordingSnapshot(
                RecordingState.FAILED,
                error_code=error.code,
                message=str(error),
            )
        )

    def _finish_snapshot(self, snapshot: RecordingSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
            callback = self._callback
            self._control = None
            self._future = None
            self._temporary_path = None
            self._target_path = None
        if callback is not None:
            callback(snapshot)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            control = self._control
            temporary = self._temporary_path
            if control is not None:
                control.cancelled.set()
                control.paused.clear()
        self._executor.shutdown(wait=True, cancel_futures=True)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        with self._lock:
            self._snapshot = RecordingSnapshot(
                RecordingState.CLOSED, message="내레이션 녹음 서비스 종료"
            )
