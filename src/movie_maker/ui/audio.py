"""Qt-thread adapters for cancellable PCM decoding and device playback."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal, Slot
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from movie_maker.audio import (
    AudioDecodeCoordinator,
    AudioDecodeFailure,
    AudioGraph,
    DecodedAudio,
)


class AudioOutputError(RuntimeError):
    """Raised when PCM cannot be opened on the selected output device."""


class AudioOutput(Protocol):
    def play(self, pcm_bytes: bytes, *, sample_rate: int, channels: int) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class QtAudioOutput(QObject):
    """Play one decoded PCM range using Qt's current default audio device."""

    output_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sink: QAudioSink | None = None
        self._buffer: QBuffer | None = None

    def play(self, pcm_bytes: bytes, *, sample_rate: int, channels: int) -> None:
        self.stop()
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            raise AudioOutputError(
                "오디오 출력 장치를 찾을 수 없습니다. Windows 소리 설정을 확인하세요."
            )
        audio_format = QAudioFormat()
        audio_format.setSampleRate(sample_rate)
        audio_format.setChannelCount(channels)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(audio_format):
            raise AudioOutputError(
                "출력 장치가 48 kHz 스테레오 오디오를 지원하지 않습니다."
            )
        buffer = QBuffer(self)
        buffer.setData(QByteArray(pcm_bytes))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            buffer.deleteLater()
            raise AudioOutputError("오디오 재생 버퍼를 열 수 없습니다. 다시 시도하세요.")
        sink = QAudioSink(device, audio_format, self)
        self._buffer = buffer
        self._sink = sink
        sink.stateChanged.connect(self._check_state)
        sink.start(buffer)
        self._raise_current_error()

    @Slot()
    def _check_state(self) -> None:
        if self._sink is None or self._sink.error().value == QAudio.Error.NoError.value:
            return
        message = self._current_error_message()
        self.stop()
        self.output_failed.emit(message)

    def _raise_current_error(self) -> None:
        if (
            self._sink is not None
            and self._sink.error().value != QAudio.Error.NoError.value
        ):
            message = self._current_error_message()
            self.stop()
            raise AudioOutputError(message)

    def _current_error_message(self) -> str:
        if self._sink is None:
            return "오디오 출력 장치가 닫혔습니다. Windows 소리 설정을 확인하세요."
        error = self._sink.error()
        if error.value == QAudio.Error.OpenError.value:
            return "오디오 출력 장치를 열 수 없습니다. Windows 소리 설정을 확인하세요."
        if error.value == QAudio.Error.IOError.value:
            return "오디오 출력 장치와 통신할 수 없습니다. 장치 연결을 확인하세요."
        if error.value == QAudio.Error.UnderrunError.value:
            return "오디오 버퍼가 끊겼습니다. 다시 재생하세요."
        return "오디오 출력 장치가 중단됐습니다. 장치 연결을 확인하세요."

    def stop(self) -> None:
        sink, self._sink = self._sink, None
        buffer, self._buffer = self._buffer, None
        if sink is not None:
            sink.stop()
            sink.deleteLater()
        if buffer is not None:
            buffer.close()
            buffer.deleteLater()

    def close(self) -> None:
        self.stop()


class AudioPreviewBridge(QObject):
    """Deliver worker audio results on the owning Qt thread."""

    audio_ready = Signal(object)
    audio_failed = Signal(object)
    _worker_result = Signal(object)

    def __init__(
        self,
        coordinator: AudioDecodeCoordinator | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = coordinator or AudioDecodeCoordinator()
        self._closed = False
        self._active_key: tuple[object, ...] | None = None
        self._worker_result.connect(self._deliver)

    def request(self, graph: AudioGraph) -> None:
        if self._closed or self._active_key == graph.cache_key:
            return
        self._active_key = graph.cache_key
        try:
            self._coordinator.request(graph, self._worker_result.emit)
        except RuntimeError:
            self._active_key = None

    @Slot(object)
    def _deliver(self, result: object) -> None:
        if self._closed:
            return
        self._active_key = None
        if isinstance(result, DecodedAudio):
            self.audio_ready.emit(result)
        elif isinstance(result, AudioDecodeFailure):
            self.audio_failed.emit(result)

    def cancel(self) -> None:
        self._active_key = None
        self._coordinator.cancel()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._active_key = None
        self._coordinator.close()
