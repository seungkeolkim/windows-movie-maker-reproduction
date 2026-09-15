from __future__ import annotations

import json
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path

import pytest

from movie_maker.project import (
    AutosaveCoordinator,
    AutosaveError,
    AutosaveMetadata,
    AutosaveState,
    AutosaveStore,
    Project,
    ProjectFileStore,
    RecoveryChoice,
    SessionJournal,
    SessionMarker,
)


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class ManualSubmitter:
    def __init__(self) -> None:
        self.functions = []
        self.futures: list[Future[Path]] = []

    def submit(self, function):
        future: Future[Path] = Future()
        self.functions.append(function)
        self.futures.append(future)
        return future

    def finish(self, index: int) -> None:
        self.futures[index].set_result(self.functions[index]())


def _metadata(project: Project, *, generation: int = 1, session: str = "session-1"):
    return AutosaveMetadata(
        project.project_id,
        project.name,
        session,
        generation,
        generation,
        200.0 + generation,
        None,
        None,
        "클립 편집",
    )


def test_autosave_is_atomic_validated_and_separate_from_normal_file(tmp_path: Path) -> None:
    normal = tmp_path / "normal.mmrproj"
    project = Project.empty(project_id="project-1", name="정상 프로젝트")
    ProjectFileStore().save(project, normal)
    before = normal.read_bytes()
    store = AutosaveStore(tmp_path / "data" / "autosaves")

    path = store.write(project, replace(_metadata(project), normal_path=str(normal)))
    candidate = store.load(path)

    assert candidate.project == project
    assert candidate.normal_project == project
    assert normal.read_bytes() == before
    assert path != normal
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_generation_saved_during_new_edit_remains_pending(tmp_path: Path) -> None:
    clock = Clock()
    submitter = ManualSubmitter()
    store = AutosaveStore(tmp_path / "autosaves")
    coordinator = AutosaveCoordinator(
        store,
        "session",
        idle_seconds=5,
        maximum_seconds=20,
        clock=clock,
        submitter=submitter,
    )
    first = Project.empty(project_id="project-1")
    second = replace(first, name="두 번째 세대")

    assert coordinator.note_change(first, normal_path=None, history_position=1, summary="1") == 1
    clock.value += 5
    assert coordinator.tick()
    assert coordinator.state is AutosaveState.SAVING
    coordinator.note_change(second, normal_path=None, history_position=2, summary="2")
    submitter.finish(0)

    assert coordinator.saved_generation == 1
    assert coordinator.generation == 2
    assert coordinator.pending
    assert coordinator.state is AutosaveState.WAITING
    clock.value += 5
    assert coordinator.tick()
    submitter.finish(1)
    assert coordinator.saved_generation == 2
    assert store.load(store.path_for("project-1", 2)).project == second
    coordinator.shutdown()


def test_normal_save_removes_only_completed_generations(tmp_path: Path) -> None:
    store = AutosaveStore(tmp_path / "autosaves")
    project = Project.empty(project_id="project-1")
    store.write(project, _metadata(project, generation=1))
    store.write(project, _metadata(project, generation=2))
    store.write(project, _metadata(project, generation=3))

    store.remove_through_generation("project-1", 2)

    assert not store.path_for("project-1", 1).exists()
    assert not store.path_for("project-1", 2).exists()
    assert store.path_for("project-1", 3).exists()


def test_normal_save_during_autosave_cleans_late_old_generation(tmp_path: Path) -> None:
    clock = Clock()
    submitter = ManualSubmitter()
    store = AutosaveStore(tmp_path / "autosaves")
    coordinator = AutosaveCoordinator(
        store,
        "session",
        idle_seconds=5,
        maximum_seconds=20,
        clock=clock,
        submitter=submitter,
    )
    project = Project.empty(project_id="project-1")
    coordinator.note_change(project, normal_path=None, history_position=1, summary="편집")
    clock.value += 5
    assert coordinator.tick()

    coordinator.normal_save_completed(project)
    coordinator.normal_save_completed(Project.empty(project_id="project-2"))
    submitter.finish(0)

    assert not store.path_for(project.project_id, 1).exists()
    assert coordinator.state is AutosaveState.SAVED
    coordinator.shutdown()


def test_unclean_session_discovers_candidate_and_choices_preserve_files(tmp_path: Path) -> None:
    root = tmp_path / "autosaves"
    normal = tmp_path / "normal.mmrproj"
    normal_project = Project.empty(project_id="project-1", name="정상")
    recovered = replace(normal_project, name="자동 저장")
    ProjectFileStore().save(normal_project, normal)
    normal_bytes = normal.read_bytes()
    store = AutosaveStore(root)
    metadata = replace(
        _metadata(recovered),
        project_id=recovered.project_id,
        project_name=recovered.name,
        normal_path=str(normal),
        normal_saved_at=normal.stat().st_mtime,
        autosaved_at=normal.stat().st_mtime + 1,
    )
    autosave = store.write(recovered, metadata)

    candidates = store.discover(SessionMarker("session-1", 100.0, False))
    assert len(candidates) == 1
    assert store.choose(candidates[0], RecoveryChoice.AUTOSAVE).dirty
    normal_result = store.choose(candidates[0], RecoveryChoice.NORMAL)
    assert normal_result.project == normal_project
    assert not normal_result.dirty
    assert store.choose(candidates[0], RecoveryChoice.LATER).deferred
    assert autosave.exists()
    assert normal.read_bytes() == normal_bytes
    assert len(store.discover(SessionMarker("other", 300.0, True))) == 1

    assert store.discard(candidates[0])
    assert not autosave.exists()


def test_corrupt_and_newer_schema_autosaves_are_quarantined(tmp_path: Path) -> None:
    store = AutosaveStore(tmp_path / "autosaves", tmp_path / "quarantine")
    corrupt = store.root / "x" / "000.autosave.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{broken", encoding="utf-8")
    project = Project.empty(project_id="project-1")
    newer = store.write(project, _metadata(project))
    payload = json.loads(newer.read_text(encoding="utf-8"))
    payload["project"]["schema_version"] = 999
    newer.write_text(json.dumps(payload), encoding="utf-8")

    assert store.discover(SessionMarker("session-1", 0.0, False)) == ()
    assert not corrupt.exists()
    assert not newer.exists()
    assert len(tuple(store.quarantine_root.glob("*.invalid-autosave"))) == 2


def test_previous_schema_autosave_migrates_from_copy_without_rewriting_source(
    tmp_path: Path,
) -> None:
    store = AutosaveStore(tmp_path / "autosaves")
    project = Project.empty(project_id="project-1")
    autosave = store.write(project, _metadata(project))
    payload = json.loads(autosave.read_text(encoding="utf-8"))
    payload["project"]["schema_version"] = 0
    autosave.write_text(json.dumps(payload), encoding="utf-8")
    before = autosave.read_bytes()

    candidate = store.load(autosave)

    assert candidate.project == project
    assert autosave.read_bytes() == before


def test_session_journal_reports_previous_cleanliness(tmp_path: Path) -> None:
    clock = Clock()
    journal = SessionJournal(tmp_path / "session.json", clock=clock)
    assert journal.begin("first") is None
    previous = SessionJournal(tmp_path / "session.json", clock=clock).begin("second")
    assert previous == SessionMarker("first", 100.0, False)
    journal = SessionJournal(tmp_path / "session.json", clock=clock)
    journal.begin("third")
    journal.mark_clean()
    assert journal.read() == SessionMarker("third", 100.0, True)


def test_failed_autosave_keeps_previous_complete_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AutosaveStore(tmp_path / "autosaves")
    project = Project.empty(project_id="project-1")
    previous = store.write(project, _metadata(project))
    previous_bytes = previous.read_bytes()

    def fail_write(_value: object, _path: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("movie_maker.project.recovery.write_json_atomic", fail_write)
    with pytest.raises(AutosaveError):
        store.write(project, _metadata(project, generation=2))
    assert previous.read_bytes() == previous_bytes
