import subprocess
from threading import Event

import pytest

from movie_maker.media.process import ProcessCancelled, run_cancellable_process


class Process:
    def __init__(self, *, require_kill: bool = False) -> None:
        self.returncode = None
        self.require_kill = require_kill
        self.terminated = False
        self.killed = False

    def communicate(self, input=None, timeout=None):
        del input
        if self.require_kill and self.terminated and not self.killed and timeout is not None:
            raise subprocess.TimeoutExpired(("fake",), timeout)
        self.returncode = 0
        return b"out", b"err"

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_cancellation_terminates_then_kills_and_collects_output() -> None:
    process = Process(require_kill=True)
    cancel = Event()
    cancel.set()
    with pytest.raises(ProcessCancelled):
        run_cancellable_process(
            ("fake", "argument with spaces"),
            cancel,
            timeout=1,
            launcher=lambda _arguments: process,
        )
    assert process.terminated
    assert process.killed


def test_process_arguments_remain_a_structured_sequence() -> None:
    captured = []
    process = Process()

    result = run_cancellable_process(
        ("fake", "한글 경로\\", 'quote"value'),
        Event(),
        timeout=1,
        launcher=lambda arguments: (captured.append(tuple(arguments)) or process),
    )

    assert result.returncode == 0
    assert captured == [("fake", "한글 경로\\", 'quote"value')]
