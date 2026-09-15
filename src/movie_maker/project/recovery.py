"""Autosave generations, clean-session markers, and validated recovery choices."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from threading import Lock
from time import time
from typing import Protocol
from uuid import uuid4

from movie_maker.project.atomic import read_json, write_json_atomic
from movie_maker.project.model import Project
from movie_maker.project.persistence import (
    ProjectFileStore,
    ProjectReadError,
    UnsupportedProjectVersion,
    project_from_document,
    project_to_document,
)

AUTOSAVE_FORMAT_VERSION = 1
SESSION_FORMAT_VERSION = 1


class AutosaveError(RuntimeError):
    """An app-owned autosave could not be safely written or read."""


class RecoveryChoice(str, Enum):
    AUTOSAVE = "autosave"
    NORMAL = "normal"
    LATER = "later"


class AutosaveState(str, Enum):
    IDLE = "idle"
    WAITING = "waiting"
    SAVING = "saving"
    SAVED = "saved"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SessionMarker:
    session_id: str
    started_at: float
    clean_exit: bool


@dataclass(frozen=True, slots=True)
class AutosaveMetadata:
    project_id: str
    project_name: str
    session_id: str
    generation: int
    history_position: int
    autosaved_at: float
    normal_path: str | None
    normal_saved_at: float | None
    change_summary: str
    deferred: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    path: Path
    metadata: AutosaveMetadata
    project: Project
    normal_project: Project | None


@dataclass(frozen=True, slots=True)
class RecoveryOpenResult:
    project: Project | None
    normal_path: str | None
    dirty: bool
    deferred: bool = False


class WorkSubmitter(Protocol):
    def submit(self, function: Callable[[], Path]) -> Future[Path]: ...


class SessionJournal:
    """Persist one small clean/unclean marker without touching user projects."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time) -> None:
        self.path = path
        self._clock = clock
        self.current: SessionMarker | None = None

    def begin(self, session_id: str | None = None) -> SessionMarker | None:
        previous = self.read()
        current = SessionMarker(session_id or str(uuid4()), self._clock(), False)
        self._write(current)
        self.current = current
        return previous

    def read(self) -> SessionMarker | None:
        if not self.path.is_file():
            return None
        try:
            value = read_json(self.path)
            if not isinstance(value, dict) or value.get("format_version") != SESSION_FORMAT_VERSION:
                return None
            session_id = value.get("session_id")
            started_at = value.get("started_at")
            clean_exit = value.get("clean_exit")
            if (
                not isinstance(session_id, str)
                or not session_id
                or not isinstance(started_at, (int, float))
                or isinstance(started_at, bool)
                or not isinstance(clean_exit, bool)
            ):
                return None
            return SessionMarker(session_id, float(started_at), clean_exit)
        except (OSError, ValueError, TypeError):
            return None

    def mark_clean(self) -> None:
        if self.current is None:
            return
        self.current = replace(self.current, clean_exit=True)
        self._write(self.current)

    def _write(self, marker: SessionMarker) -> None:
        write_json_atomic(
            {
                "format_version": SESSION_FORMAT_VERSION,
                "session_id": marker.session_id,
                "started_at": marker.started_at,
                "clean_exit": marker.clean_exit,
            },
            self.path,
        )


class AutosaveStore:
    """Write and discover self-contained validated autosave envelopes."""

    def __init__(self, root: Path, quarantine_root: Path | None = None) -> None:
        self.root = root
        self.quarantine_root = quarantine_root or root.parent / "recovery-quarantine"

    def path_for(self, project_id: str, generation: int) -> Path:
        identity = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]
        return self.root / identity / f"{generation:020d}.autosave.json"

    def write(self, project: Project, metadata: AutosaveMetadata) -> Path:
        if metadata.project_id != project.project_id or metadata.project_name != project.name:
            raise AutosaveError("자동 저장 메타데이터가 프로젝트와 일치하지 않습니다.")
        path = self.path_for(project.project_id, metadata.generation)
        envelope: dict[str, object] = {
            "autosave_format_version": AUTOSAVE_FORMAT_VERSION,
            "metadata": _metadata_document(metadata),
            "project": project_to_document(project),
        }
        try:
            write_json_atomic(envelope, path)
        except (OSError, TypeError, ValueError) as error:
            raise AutosaveError(f"자동 저장본을 기록할 수 없습니다: {path}") from error
        return path

    def load(self, path: Path) -> RecoveryCandidate:
        try:
            value = read_json(path)
            if not isinstance(value, dict):
                raise TypeError("Autosave envelope must be an object.")
            if value.get("autosave_format_version") != AUTOSAVE_FORMAT_VERSION:
                raise ValueError("Unsupported autosave envelope version.")
            metadata = _metadata_from_document(value.get("metadata"))
            project = project_from_document(deepcopy(value.get("project")))
            if metadata.project_id != project.project_id or metadata.project_name != project.name:
                raise ValueError("Autosave metadata does not match the project.")
            normal_project = _load_normal_project(metadata.normal_path)
            return RecoveryCandidate(path, metadata, project, normal_project)
        except UnsupportedProjectVersion:
            raise
        except (OSError, ValueError, TypeError, ProjectReadError) as error:
            raise AutosaveError(f"자동 저장본을 읽을 수 없습니다: {path}") from error

    def discover(self, previous_session: SessionMarker | None) -> tuple[RecoveryCandidate, ...]:
        candidates: list[RecoveryCandidate] = []
        if not self.root.exists():
            return ()
        for path in sorted(self.root.glob("*/*.autosave.json")):
            try:
                candidate = self.load(path)
            except (AutosaveError, UnsupportedProjectVersion):
                self.quarantine(path)
                continue
            metadata = candidate.metadata
            from_unclean_session = (
                previous_session is not None
                and not previous_session.clean_exit
                and metadata.session_id == previous_session.session_id
            )
            if metadata.deferred or (from_unclean_session and _newer_than_normal(metadata)):
                candidates.append(candidate)
        return tuple(sorted(candidates, key=lambda item: item.metadata.autosaved_at, reverse=True))

    def defer(self, candidate: RecoveryCandidate) -> RecoveryCandidate:
        metadata = replace(candidate.metadata, deferred=True)
        self.write(candidate.project, metadata)
        return replace(candidate, metadata=metadata)

    def choose(self, candidate: RecoveryCandidate, choice: RecoveryChoice) -> RecoveryOpenResult:
        if choice is RecoveryChoice.LATER:
            self.defer(candidate)
            return RecoveryOpenResult(None, candidate.metadata.normal_path, False, deferred=True)
        if choice is RecoveryChoice.AUTOSAVE:
            return RecoveryOpenResult(candidate.project, candidate.metadata.normal_path, True)
        if candidate.normal_project is None:
            raise AutosaveError("정상 저장본을 열 수 없습니다.")
        return RecoveryOpenResult(candidate.normal_project, candidate.metadata.normal_path, False)

    def remove_through_generation(self, project_id: str, generation: int) -> None:
        directory = self.path_for(project_id, 0).parent
        if not directory.exists():
            return
        for path in directory.glob("*.autosave.json"):
            try:
                candidate = self.load(path)
            except (AutosaveError, UnsupportedProjectVersion):
                continue
            if candidate.metadata.generation <= generation:
                path.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass

    def discard(self, candidate: RecoveryCandidate) -> bool:
        """Remove one explicitly rejected owned recovery file."""

        try:
            path = candidate.path.resolve(strict=True)
            path.relative_to(self.root.resolve(strict=True))
            path.unlink()
        except (OSError, ValueError):
            return False
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True

    def quarantine(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
            root = self.root.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        target = self.quarantine_root / f"{uuid4().hex}.invalid-autosave"
        try:
            shutil.move(str(resolved), str(target))
        except OSError:
            return None
        return target


class AutosaveCoordinator:
    """Schedule immutable project snapshots by idle and maximum-delay policies."""

    def __init__(
        self,
        store: AutosaveStore,
        session_id: str,
        *,
        idle_seconds: float = 5.0,
        maximum_seconds: float = 120.0,
        clock: Callable[[], float] = time,
        submitter: WorkSubmitter | None = None,
    ) -> None:
        if idle_seconds <= 0 or maximum_seconds < idle_seconds:
            raise ValueError("Autosave intervals must be positive and maximum >= idle.")
        self.store = store
        self.session_id = session_id
        self.idle_seconds = idle_seconds
        self.maximum_seconds = maximum_seconds
        self._clock = clock
        self._owned_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autosave")
        self._submitter = submitter or self._owned_executor
        self._lock = Lock()
        self._generation = 0
        self._saved_generation = 0
        self._dirty_since: float | None = None
        self._last_change_at: float | None = None
        self._snapshot: tuple[Project, str | None, int, str] | None = None
        self._future: Future[Path] | None = None
        self._cleanup_through: dict[str, int] = {}
        self.state = AutosaveState.IDLE
        self.last_error: str | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def saved_generation(self) -> int:
        return self._saved_generation

    @property
    def pending(self) -> bool:
        return self._generation > self._saved_generation

    def note_change(
        self,
        project: Project,
        *,
        normal_path: str | None,
        history_position: int,
        summary: str,
    ) -> int:
        now = self._clock()
        with self._lock:
            self._generation += 1
            if self._dirty_since is None:
                self._dirty_since = now
            self._last_change_at = now
            self._snapshot = (project, normal_path, history_position, summary)
            self.state = AutosaveState.WAITING
            return self._generation

    def tick(self) -> bool:
        with self._lock:
            if self.state is AutosaveState.CLOSED or self._future is not None:
                return False
            if self._snapshot is None or self._last_change_at is None or self._dirty_since is None:
                return False
            now = self._clock()
            if (
                now - self._last_change_at < self.idle_seconds
                and now - self._dirty_since < self.maximum_seconds
            ):
                return False
            project, normal_path, history_position, summary = self._snapshot
            generation = self._generation
            metadata = AutosaveMetadata(
                project.project_id,
                project.name,
                self.session_id,
                generation,
                history_position,
                now,
                normal_path,
                _normal_mtime(normal_path),
                summary,
            )
            self.state = AutosaveState.SAVING
        future = self._submitter.submit(lambda: self.store.write(project, metadata))
        with self._lock:
            self._future = future
        future.add_done_callback(
            lambda completed: self._complete(completed, generation, project.project_id)
        )
        return True

    def _complete(self, future: Future[Path], generation: int, project_id: str) -> None:
        cleanup: tuple[str, int] | None = None
        with self._lock:
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - isolates the background worker boundary
                self.last_error = str(error)
                self.state = AutosaveState.FAILED
            else:
                self._saved_generation = max(self._saved_generation, generation)
                self.last_error = None
                self.state = (
                    AutosaveState.WAITING
                    if self._generation > self._saved_generation
                    else AutosaveState.SAVED
                )
                if self._generation == self._saved_generation:
                    self._dirty_since = None
                if generation <= self._cleanup_through.get(project_id, 0):
                    cleanup = (project_id, generation)
            finally:
                self._future = None
        if cleanup is not None:
            self.store.remove_through_generation(*cleanup)

    def normal_save_completed(self, project: Project | None = None) -> None:
        with self._lock:
            generation = self._generation
            saved_project = project or (self._snapshot[0] if self._snapshot is not None else None)
            self._saved_generation = generation
            if saved_project is not None:
                self._cleanup_through[saved_project.project_id] = max(
                    generation,
                    self._cleanup_through.get(saved_project.project_id, 0),
                )
            self._dirty_since = None
            self._snapshot = None
            self.state = AutosaveState.IDLE
        if saved_project is not None:
            self.store.remove_through_generation(saved_project.project_id, generation)

    def shutdown(self, timeout_seconds: float = 2.0) -> None:
        future = self._future
        if future is not None:
            try:
                future.result(timeout=timeout_seconds)
            except TimeoutError:
                future.cancel()
            except Exception as error:  # noqa: BLE001 - shutdown must not lose the user session
                self.last_error = str(error)
        with self._lock:
            self.state = AutosaveState.CLOSED
        self._owned_executor.shutdown(wait=False, cancel_futures=True)


def _metadata_document(metadata: AutosaveMetadata) -> dict[str, object]:
    return {
        "project_id": metadata.project_id,
        "project_name": metadata.project_name,
        "session_id": metadata.session_id,
        "generation": metadata.generation,
        "history_position": metadata.history_position,
        "autosaved_at": metadata.autosaved_at,
        "normal_path": metadata.normal_path,
        "normal_saved_at": metadata.normal_saved_at,
        "change_summary": metadata.change_summary,
        "deferred": metadata.deferred,
    }


def _metadata_from_document(value: object) -> AutosaveMetadata:
    if not isinstance(value, Mapping):
        raise TypeError("Autosave metadata must be an object.")
    required_strings = ("project_id", "project_name", "session_id", "change_summary")
    if any(not isinstance(value.get(key), str) or not value.get(key) for key in required_strings):
        raise ValueError("Autosave metadata contains invalid text.")
    generation = value.get("generation")
    history_position = value.get("history_position")
    autosaved_at = value.get("autosaved_at")
    normal_path = value.get("normal_path")
    normal_saved_at = value.get("normal_saved_at")
    deferred = value.get("deferred", False)
    if type(generation) is not int or generation <= 0:
        raise ValueError("Autosave generation must be positive.")
    if type(history_position) is not int or history_position < 0:
        raise ValueError("Autosave history position must be non-negative.")
    if not isinstance(autosaved_at, (int, float)) or isinstance(autosaved_at, bool):
        raise TypeError("Autosave timestamp is invalid.")
    if normal_path is not None and not isinstance(normal_path, str):
        raise ValueError("Autosave normal path is invalid.")
    if normal_saved_at is not None and (
        not isinstance(normal_saved_at, (int, float)) or isinstance(normal_saved_at, bool)
    ):
        raise ValueError("Autosave normal timestamp is invalid.")
    if not isinstance(deferred, bool):
        raise TypeError("Autosave deferred flag is invalid.")
    return AutosaveMetadata(
        str(value["project_id"]),
        str(value["project_name"]),
        str(value["session_id"]),
        generation,
        history_position,
        float(autosaved_at),
        normal_path,
        float(normal_saved_at) if normal_saved_at is not None else None,
        str(value["change_summary"]),
        deferred,
    )


def _normal_mtime(path: str | None) -> float | None:
    if path is None:
        return None
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def _newer_than_normal(metadata: AutosaveMetadata) -> bool:
    if metadata.normal_path is None or metadata.normal_saved_at is None:
        return True
    try:
        return metadata.autosaved_at > Path(metadata.normal_path).stat().st_mtime
    except OSError:
        return True


def _load_normal_project(path: str | None) -> Project | None:
    if path is None:
        return None
    try:
        return ProjectFileStore().load(path)
    except ProjectReadError:
        return None
