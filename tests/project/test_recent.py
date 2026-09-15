from pathlib import Path

from movie_maker.project import RecentProjectsStore, RecentProjectStatus


def test_recent_projects_record_only_explicit_success_and_trim(tmp_path: Path) -> None:
    store = RecentProjectsStore(tmp_path / "recent.json", limit=2, clock=lambda: 10.0)
    first = tmp_path / "one.mmrproj"
    second = tmp_path / "two.mmrproj"
    third = tmp_path / "three.mmrproj"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")

    store.record(first, "One")
    store.record(second, "Two")
    entries = store.record(third, "Three")

    assert [item.name for item in entries] == ["Three", "Two"]
    assert entries[0].status is RecentProjectStatus.AVAILABLE
    assert store.load()[0].status is RecentProjectStatus.MISSING


def test_recent_projects_corruption_isolated_and_entries_can_be_removed(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    path.write_text("not json", encoding="utf-8")
    store = RecentProjectsStore(path)
    assert store.load() == ()

    project = tmp_path / "project.mmrproj"
    project.write_text("{}", encoding="utf-8")
    store.record(project, "Project")
    assert len(store.load()) == 1
    assert store.remove(project) == ()
    assert store.load() == ()
