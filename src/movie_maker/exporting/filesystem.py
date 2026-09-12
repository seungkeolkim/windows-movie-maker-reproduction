"""Same-filesystem temporary output and atomic publication policy."""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path
from typing import Protocol

from movie_maker.exporting.contracts import ExportErrorCode
from movie_maker.exporting.plan import ExportPlan


class ExportFileError(OSError):
    """A typed output-path or publication failure."""

    def __init__(self, code: ExportErrorCode, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class ExportFileOperations(Protocol):
    def prepare(self, plan: ExportPlan) -> str: ...

    def commit(self, plan: ExportPlan, temporary_path: str) -> None: ...

    def cleanup(self, temporary_path: str) -> None: ...


def _file_error(error: OSError, *, write: bool) -> ExportFileError:
    if error.errno == errno.ENOSPC:
        return ExportFileError(
            ExportErrorCode.DISK_FULL,
            "디스크 공간이 부족해 동영상을 저장할 수 없습니다.",
            str(error),
        )
    return ExportFileError(
        ExportErrorCode.OUTPUT_NOT_WRITABLE if write else ExportErrorCode.IO_ERROR,
        (
            "선택한 위치에 동영상을 쓸 수 없습니다. 권한과 경로를 확인하세요."
            if write
            else "완성된 동영상 파일을 최종 위치로 옮기지 못했습니다."
        ),
        str(error),
    )


class LocalExportFileOperations:
    """Create a unique sibling temporary and publish only a complete file."""

    def prepare(self, plan: ExportPlan) -> str:
        target = Path(plan.target_path)
        parent = target.parent
        if not parent.is_dir():
            raise ExportFileError(
                ExportErrorCode.OUTPUT_PATH_INVALID,
                "출력 폴더를 찾을 수 없습니다. 존재하는 위치를 선택하세요.",
                str(parent),
            )
        if target.exists() and not plan.overwrite_existing:
            raise ExportFileError(
                ExportErrorCode.TARGET_EXISTS,
                "같은 이름의 파일이 이미 있습니다. 교체를 확인하거나 다른 이름을 선택하세요.",
            )
        try:
            descriptor, path = tempfile.mkstemp(
                prefix=f".{target.stem}.",
                suffix=".partial.mp4",
                dir=parent,
            )
            os.close(descriptor)
        except OSError as error:
            raise _file_error(error, write=True) from error
        return path

    def commit(self, plan: ExportPlan, temporary_path: str) -> None:
        target = Path(plan.target_path)
        temporary = Path(temporary_path)
        try:
            with temporary.open("rb+") as stream:
                os.fsync(stream.fileno())
            if plan.overwrite_existing:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
                temporary.unlink()
        except FileExistsError as error:
            raise ExportFileError(
                ExportErrorCode.TARGET_EXISTS,
                "출력 중 같은 이름의 파일이 생겼습니다. 기존 파일은 유지했습니다.",
            ) from error
        except OSError as error:
            raise _file_error(error, write=False) from error

    def cleanup(self, temporary_path: str) -> None:
        try:
            Path(temporary_path).unlink(missing_ok=True)
        except OSError:
            # The primary failure remains more useful than a best-effort cleanup error.
            pass
