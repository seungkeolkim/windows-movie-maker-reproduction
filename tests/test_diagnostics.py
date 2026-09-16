import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from movie_maker.diagnostics import (
    LOG_RETENTION_DAYS,
    MAXIMUM_LOG_BYTES,
    close_application_logging,
    configure_application_logging,
)


def test_application_logging_creates_redacted_utf8_log(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\Example")
    log_path = configure_application_logging(tmp_path)
    try:
        logging.getLogger("movie_maker.test").warning(
            "project=%s token=%s",
            "C:\\Users\\Example\\비디오\\여행.mmrproj",
            "private-value",
        )
    finally:
        close_application_logging()

    assert log_path is not None
    text = log_path.read_text(encoding="utf-8")
    assert "Application logging started" in text
    assert "Application logging stopped" in text
    assert "%USERPROFILE%" in text
    assert "token=<redacted>" in text
    assert "private-value" not in text


def test_application_logging_prunes_expired_and_oversized_owned_logs(tmp_path: Path) -> None:
    expired = tmp_path / "app-expired.log"
    expired.write_text("old", encoding="utf-8")
    expired_time = (
        datetime.now(UTC) - timedelta(days=LOG_RETENTION_DAYS + 1)
    ).timestamp()
    os.utime(expired, (expired_time, expired_time))
    oversized = tmp_path / "app-oversized.log"
    oversized.write_bytes(b"x" * (MAXIMUM_LOG_BYTES + 1))
    unrelated = tmp_path / "launcher-keep.log"
    unrelated.write_text("keep", encoding="utf-8")

    log_path = configure_application_logging(tmp_path)
    close_application_logging()

    assert log_path is not None
    assert not expired.exists()
    assert not oversized.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_application_logging_failure_never_blocks_startup(tmp_path: Path) -> None:
    unavailable_root = tmp_path / "not-a-directory"
    unavailable_root.write_text("occupied", encoding="utf-8")

    assert configure_application_logging(unavailable_root) is None
    close_application_logging()
