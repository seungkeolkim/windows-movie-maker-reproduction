from __future__ import annotations

from pathlib import Path

from movie_maker import application
from movie_maker.__main__ import main, parse_arguments
from movie_maker.project import Project, ProjectFileStore


def test_parse_project_and_online_arguments_preserves_unicode_path() -> None:
    path = r"C:\Users\사용자\여행 영상\제주 프로젝트.mmrproj"

    options = parse_arguments(["--online", "--project", path])

    assert options.online is True
    assert options.project == path


def test_main_opens_requested_project_before_starting_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "공백 있는 프로젝트.mmrproj"
    ProjectFileStore().save(
        Project.empty(project_id="command-line-project", name="명령줄 프로젝트"),
        target,
    )
    captured: dict[str, object] = {}

    def fake_run(**options: object) -> int:
        captured.update(options)
        return 17

    monkeypatch.setattr("movie_maker.__main__.run", fake_run)

    assert main(["--project", str(target), "--online"]) == 17
    assert captured == {
        "check_only": False,
        "project_path": str(target),
        "online": True,
    }


def test_application_session_loads_project_path_before_showing_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "한글 경로" / "여행 프로젝트.mmrproj"
    target.parent.mkdir()
    ProjectFileStore().save(
        Project.empty(project_id="startup-project", name="시작 프로젝트"),
        target,
    )
    shown: dict[str, object] = {}

    class FakeApplication:
        def setProperty(self, name: str, value: object) -> None:
            shown[name] = value

        def exec(self) -> int:
            return 0

    class FakeWindow:
        def __init__(self, controller: object) -> None:
            shown["controller"] = controller

        def show(self) -> None:
            shown["visible"] = True

    def unavailable_runtime() -> None:
        raise OSError("isolated test")

    monkeypatch.setattr(application, "create_application", lambda: FakeApplication())
    monkeypatch.setattr(application.W10Runtime, "create", unavailable_runtime)
    monkeypatch.setattr(application, "MainWindow", FakeWindow)

    assert application.run(project_path=str(target), online=True) == 0
    controller = shown["controller"]
    assert controller.state.project_path == str(target)
    assert controller.state.project_name == "시작 프로젝트"
    assert shown["onlineMode"] is True
    assert shown["visible"] is True
