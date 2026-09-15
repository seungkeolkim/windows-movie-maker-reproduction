from pathlib import Path

from movie_maker.project import (
    ApplicationPaths,
    AutosaveCoordinator,
    AutosaveMetadata,
    AutosaveStore,
    Project,
    ProjectFileStore,
    SessionJournal,
    SessionMarker,
)
from movie_maker.runtime import W10Runtime
from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_controller import MockController


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_runtime_separates_owned_data_cache_logs_and_marks_clean(tmp_path: Path) -> None:
    paths = ApplicationPaths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "logs",
    )
    runtime = W10Runtime.create(paths)

    assert runtime.paths.autosave_root.is_relative_to(paths.data_root)
    assert runtime.paths.recent_projects_path.is_relative_to(paths.data_root)
    assert runtime.paths.media_cache_root.is_relative_to(paths.cache_root)
    assert paths.log_root not in paths.data_root.parents
    assert runtime.session_journal.current is not None
    assert not runtime.session_journal.current.clean_exit

    session_id = runtime.session_journal.current.session_id
    runtime.close(clean_exit=True)

    assert runtime.session_journal.read() == SessionMarker(
        session_id,
        runtime.session_journal.current.started_at,
        True,
    )


def test_runtime_discovers_crash_autosave_and_controller_opens_it_dirty(tmp_path: Path) -> None:
    paths = ApplicationPaths(tmp_path / "data", tmp_path / "cache", tmp_path / "logs")
    journal = SessionJournal(paths.session_marker_path, clock=lambda: 10.0)
    journal.begin("crashed-session")
    project = Project.empty(project_id="recovery-project", name="복구 프로젝트")
    AutosaveStore(paths.autosave_root, paths.recovery_quarantine_root).write(
        project,
        AutosaveMetadata(
            project.project_id,
            project.name,
            "crashed-session",
            1,
            2,
            11.0,
            None,
            None,
            "클립 두 개 편집",
        ),
    )

    runtime = W10Runtime.create(paths)
    controller = MockController(runtime=runtime)
    assert controller.pending_recovery is not None

    controller.apply_recovery_choice("자동 저장본")

    assert controller.media_project == project
    assert controller.state.is_dirty
    assert controller.state.project_path is None
    assert controller.pending_recovery is None
    controller.close_runtime(clean_exit=True)


def test_start_page_uses_successful_recent_project(tmp_path: Path, qtbot) -> None:
    paths = ApplicationPaths(tmp_path / "data", tmp_path / "cache", tmp_path / "logs")
    runtime = W10Runtime.create(paths)
    project = Project.empty(project_id="recent-project", name="최근 편집")
    project_path = tmp_path / "recent.mmrproj"
    ProjectFileStore().save(project, project_path)
    runtime.recent_projects.record(project_path, project.name)
    window = MainWindow(MockController(runtime=runtime))
    qtbot.addWidget(window)

    assert window.start_recent.isEnabled()
    assert "최근 편집" in window.start_recent.text()

    window.close()


def test_controller_autosave_preserves_dirty_path_and_history(tmp_path: Path) -> None:
    paths = ApplicationPaths(tmp_path / "data", tmp_path / "cache", tmp_path / "logs")
    runtime = W10Runtime.create(paths)
    runtime.autosave.shutdown()
    clock = _Clock()
    assert runtime.session_journal.current is not None
    runtime.autosave = AutosaveCoordinator(
        runtime.autosave_store,
        runtime.session_journal.current.session_id,
        idle_seconds=5,
        maximum_seconds=20,
        clock=clock,
    )
    controller = MockController(runtime=runtime)
    controller.load_sample_project()
    assert controller.rename_project("자동 저장 테스트")
    before_path = controller.state.project_path
    before_history = controller.history_position
    before_project = controller.media_project
    assert controller.state.is_dirty

    clock.value += 5
    controller.autosave_tick()
    assert runtime.autosave._future is not None
    runtime.autosave._future.result(timeout=2)

    assert controller.media_project == before_project
    assert controller.state.project_path == before_path
    assert controller.history_position == before_history
    assert controller.state.is_dirty
    assert runtime.autosave_store.path_for(before_project.project_id, 1).is_file()
    controller.close_runtime(clean_exit=True)
