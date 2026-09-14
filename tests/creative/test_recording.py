from __future__ import annotations

import errno
import os
import time
import wave
from pathlib import Path
from threading import Event

import pytest

from movie_maker.creative import (
    CaptureControl,
    InputDevice,
    NarrationRecordingCoordinator,
    RecordingErrorCode,
    RecordingFailure,
    RecordingSnapshot,
    RecordingState,
)


class FakeInputBackend:
    def __init__(self, failure: RecordingFailure | None = None) -> None:
        self.failure = failure
        self.started = Event()

    def devices(self) -> tuple[InputDevice, ...]:
        return (InputDevice("fake-device", "가짜 입력 장치"),)

    def capture_wav(
        self,
        device: InputDevice,
        temporary_path: Path,
        control: CaptureControl,
        progress: object,
    ) -> None:
        assert device.device_id == "fake-device"
        self.started.set()
        frames = 0
        with wave.open(str(temporary_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48_000)
            while not control.finish_requested.is_set() and not control.cancelled.is_set():
                if not control.paused.is_set():
                    output.writeframesraw(b"\x01\x00" * 480)
                    frames += 480
                    assert callable(progress)
                    progress(frames, 25)
                time.sleep(0.002)
        if self.failure is not None:
            raise self.failure


def _wait_for(
    coordinator: NarrationRecordingCoordinator,
    state: RecordingState,
    timeout: float = 2,
) -> RecordingSnapshot:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = coordinator.snapshot
        if snapshot.state is state:
            return snapshot
        time.sleep(0.005)
    raise AssertionError(f"recording did not reach {state}: {coordinator.snapshot}")


def test_record_pause_resume_finish_publishes_one_verified_wav(tmp_path: Path) -> None:
    backend = FakeInputBackend()
    coordinator = NarrationRecordingCoordinator(backend)
    target = tmp_path / "voice.wav"
    snapshots: list[RecordingSnapshot] = []
    try:
        coordinator.start("fake-device", target, snapshots.append)
        assert backend.started.wait(1)
        coordinator.pause()
        assert coordinator.snapshot.state is RecordingState.PAUSED
        paused_frames = coordinator.snapshot.elapsed_frames
        time.sleep(0.02)
        assert coordinator.snapshot.elapsed_frames == paused_frames
        coordinator.resume()
        time.sleep(0.01)
        coordinator.finish()

        completed = _wait_for(coordinator, RecordingState.COMPLETE)
        assert completed.final_path == str(target)
        assert target.is_file()
        assert not list(tmp_path.glob("*.recording.wav"))
        with wave.open(str(target), "rb") as result:
            assert result.getnframes() > 0
            assert result.getframerate() == 48_000
        assert RecordingState.PAUSED in {snapshot.state for snapshot in snapshots}
    finally:
        coordinator.close()


def test_cancel_and_backend_failure_remove_unique_temporary_file(tmp_path: Path) -> None:
    backend = FakeInputBackend()
    coordinator = NarrationRecordingCoordinator(backend)
    target = tmp_path / "cancel.wav"
    try:
        coordinator.start("fake-device", target, lambda _snapshot: None)
        assert backend.started.wait(1)
        coordinator.cancel()
        _wait_for(coordinator, RecordingState.CANCELLED)
        assert not target.exists()
        assert not list(tmp_path.glob("*.recording.wav"))
    finally:
        coordinator.close()

    failure = RecordingFailure(
        RecordingErrorCode.DEVICE_DISCONNECTED,
        "장치가 분리됐습니다.",
    )
    failed_backend = FakeInputBackend(failure)
    failed = NarrationRecordingCoordinator(failed_backend)
    try:
        failed.start("fake-device", tmp_path / "failed.wav", lambda _snapshot: None)
        assert failed_backend.started.wait(1)
        failed.finish()
        snapshot = _wait_for(failed, RecordingState.FAILED)
        assert snapshot.error_code is RecordingErrorCode.DEVICE_DISCONNECTED
        assert not list(tmp_path.glob("*.recording.wav"))
    finally:
        failed.close()


def test_no_device_and_disk_full_are_distinct_and_preserve_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeInputBackend()
    coordinator = NarrationRecordingCoordinator(backend)
    with pytest.raises(RecordingFailure) as no_device:
        coordinator.start("missing", tmp_path / "missing.wav", lambda _snapshot: None)
    assert no_device.value.code is RecordingErrorCode.NO_DEVICE

    target = tmp_path / "full.wav"

    def no_space(_source: os.PathLike[str], _target: os.PathLike[str]) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(os, "link", no_space)
    try:
        coordinator.start("fake-device", target, lambda _snapshot: None)
        assert backend.started.wait(1)
        deadline = time.monotonic() + 1
        while coordinator.snapshot.elapsed_frames == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        coordinator.finish()
        snapshot = _wait_for(coordinator, RecordingState.FAILED)
        assert snapshot.error_code is RecordingErrorCode.DISK_FULL
        assert not target.exists()
        assert not list(tmp_path.glob("*.recording.wav"))
    finally:
        coordinator.close()


@pytest.mark.parametrize(
    "code",
    [RecordingErrorCode.PERMISSION_DENIED, RecordingErrorCode.WRITE_FAILED],
)
def test_permission_and_write_failures_are_typed_and_publish_nothing(
    tmp_path: Path, code: RecordingErrorCode
) -> None:
    backend = FakeInputBackend(RecordingFailure(code, f"typed failure: {code.value}"))
    coordinator = NarrationRecordingCoordinator(backend)
    target = tmp_path / f"{code.value}.wav"
    try:
        coordinator.start("fake-device", target, lambda _snapshot: None)
        assert backend.started.wait(1)
        coordinator.finish()
        snapshot = _wait_for(coordinator, RecordingState.FAILED)

        assert snapshot.error_code is code
        assert not target.exists()
        assert not list(tmp_path.glob("*.recording.wav"))
    finally:
        coordinator.close()
