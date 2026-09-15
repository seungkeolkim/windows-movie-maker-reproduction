"""Small failure-tolerant JSON list of successfully opened or saved projects."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import time

from movie_maker.project.atomic import read_json, write_json_atomic

RECENT_FORMAT_VERSION = 1


class RecentProjectStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    INACCESSIBLE = "inaccessible"


@dataclass(frozen=True, slots=True)
class RecentProject:
    path: str
    name: str
    touched_at: float
    status: RecentProjectStatus = RecentProjectStatus.AVAILABLE


class RecentProjectsStore:
    def __init__(
        self,
        path: Path,
        *,
        limit: int = 12,
        clock: Callable[[], float] = time,
    ) -> None:
        if limit <= 0:
            raise ValueError("Recent project limit must be positive.")
        self.path = path
        self.limit = limit
        self._clock = clock

    def record(self, path: str | os.PathLike[str], name: str) -> tuple[RecentProject, ...]:
        normalized = str(Path(path).resolve(strict=False))
        existing = [item for item in self.load() if _path_key(item.path) != _path_key(normalized)]
        entries = (RecentProject(normalized, name, self._clock()), *existing)
        self._write(entries[: self.limit])
        return entries[: self.limit]

    def load(self) -> tuple[RecentProject, ...]:
        if not self.path.is_file():
            return ()
        try:
            value = read_json(self.path)
            if not isinstance(value, dict) or value.get("format_version") != RECENT_FORMAT_VERSION:
                return ()
            raw_entries = value.get("entries")
            if not isinstance(raw_entries, list):
                return ()
            entries: list[RecentProject] = []
            for raw in raw_entries[: self.limit]:
                if not isinstance(raw, dict):
                    continue
                project_path = raw.get("path")
                name = raw.get("name")
                touched_at = raw.get("touched_at")
                if (
                    not isinstance(project_path, str)
                    or not project_path
                    or not isinstance(name, str)
                    or not name
                    or not isinstance(touched_at, (int, float))
                    or isinstance(touched_at, bool)
                ):
                    continue
                entries.append(
                    RecentProject(
                        project_path,
                        name,
                        float(touched_at),
                        _path_status(Path(project_path)),
                    )
                )
            return tuple(entries)
        except (OSError, ValueError, TypeError):
            return ()

    def remove(self, path: str | os.PathLike[str]) -> tuple[RecentProject, ...]:
        key = _path_key(str(path))
        entries = tuple(item for item in self.load() if _path_key(item.path) != key)
        self._write(entries)
        return entries

    def _write(self, entries: tuple[RecentProject, ...]) -> None:
        write_json_atomic(
            {
                "format_version": RECENT_FORMAT_VERSION,
                "entries": [
                    {"path": item.path, "name": item.name, "touched_at": item.touched_at}
                    for item in entries
                ],
            },
            self.path,
        )


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).resolve(strict=False))))


def _path_status(path: Path) -> RecentProjectStatus:
    try:
        if path.is_file():
            with path.open("rb"):
                pass
            return RecentProjectStatus.AVAILABLE
        return RecentProjectStatus.MISSING
    except PermissionError:
        return RecentProjectStatus.INACCESSIBLE
    except OSError:
        return RecentProjectStatus.INACCESSIBLE
