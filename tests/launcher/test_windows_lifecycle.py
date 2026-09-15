from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
import winreg
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows only")
ROOT = Path(__file__).parents[2]
LIFECYCLE = ROOT / "scripts" / "packaging" / "install-lifecycle.ps1"
BUILT_PAYLOAD = (
    ROOT
    / "build"
    / "windows-package"
    / "MovieMakerReproduction-0.1.0-windows-x64"
    / "payload"
)


def _payload(
    root: Path,
    version: str,
    launcher_bytes: bytes,
    *,
    with_manifest: bool = False,
) -> Path:
    root.mkdir(parents=True)
    (root / "scripts" / "environment").mkdir(parents=True)
    (root / "tools" / "uv").mkdir(parents=True)
    (root / "MovieMakerLauncher.exe").write_bytes(launcher_bytes)
    (root / ".python-version").write_text("3.13.14\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    contract = {
        "schemaVersion": 1,
        "productId": "MovieMakerReproduction",
        "displayName": "Movie Maker Reproduction",
        "version": version,
        "uv": {"bundledCandidate": "tools/uv/uv.exe"},
    }
    (root / "tools" / "uv" / "uv.exe").write_bytes(b"fixture-uv")
    (root / "scripts" / "environment" / "runtime-contract.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )
    if with_manifest:
        files = []
        for path in sorted(value for value in root.rglob("*") if value.is_file()):
            files.append(
                {
                    "relativePath": str(path.relative_to(root)).replace("/", "\\"),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size,
                }
            )
        (root / "payload-manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "productId": "MovieMakerReproduction",
                    "files": files,
                }
            ),
            encoding="utf-8",
        )
    return root


def _run_lifecycle(
    mode: str,
    *,
    source: Path | None,
    install: Path,
    state: Path,
    start_menu: Path,
    desktop: Path,
    registry_root: str,
    path_file: Path,
    app_data_root: Path | None = None,
    purge_cache: bool = False,
    purge_logs: bool = False,
    purge_autosaves: bool = False,
    fail_after_swap: bool = False,
    fail_after_registrations: bool = False,
    exit_after_swap: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    arguments = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(LIFECYCLE),
        "-Mode",
        mode,
        "-InstallDirectory",
        str(install),
        "-StateDirectory",
        str(state),
        "-StartMenuDirectory",
        str(start_menu),
        "-DesktopDirectory",
        str(desktop),
        "-RegistryTestRoot",
        registry_root,
        "-UserPathTestFile",
        str(path_file),
    ]
    if source is not None:
        arguments.extend(("-SourceDirectory", str(source)))
    if app_data_root is not None:
        arguments.extend(("-AppDataTestRoot", str(app_data_root)))
    if mode != "Uninstall":
        arguments.extend(("-StartMenuShortcut", "-DesktopShortcut", "-FileAssociation", "-AddToPath"))
    if fail_after_swap:
        arguments.append("-FailAfterSwap")
    if fail_after_registrations:
        arguments.append("-FailAfterRegistrations")
    if exit_after_swap:
        arguments.append("-ExitAfterSwap")
    if purge_cache:
        arguments.append("-PurgeCache")
    if purge_logs:
        arguments.append("-PurgeLogs")
    if purge_autosaves:
        arguments.append("-PurgeAutosaves")
    return subprocess.run(arguments, check=False, capture_output=True)


def _delete_registry_tree(path: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            children: list[str] = []
            index = 0
            while True:
                try:
                    children.append(winreg.EnumKey(key, index))
                    index += 1
                except OSError:
                    break
        for child in children:
            _delete_registry_tree(f"{path}\\{child}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        return


def _registry_default(path: str) -> str:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
        return str(winreg.QueryValueEx(key, "")[0])


def test_install_update_failure_rollback_and_uninstall_preserve_user_state(tmp_path: Path) -> None:
    registry_root = rf"Software\MovieMakerReproduction\Tests\{uuid.uuid4().hex}"
    install = tmp_path / "설치 경로 with spaces" / "MovieMakerReproduction"
    state = tmp_path / "상태 기록"
    start_menu = tmp_path / "시작 메뉴"
    desktop = tmp_path / "바탕 화면"
    path_file = tmp_path / "user-path.txt"
    app_data = tmp_path / "app-data"
    path_file.write_text(r"C:\Existing Tool", encoding="utf-8")
    first = _payload(tmp_path / "payload-v1", "1.0.0", b"launcher-v1")
    second = _payload(tmp_path / "payload-v2", "1.1.0", b"launcher-v2")
    broken = _payload(tmp_path / "payload-v3", "1.2.0", b"launcher-v3")
    older = _payload(tmp_path / "payload-old", "0.9.0", b"launcher-old")

    try:
        installed = _run_lifecycle(
            "Install",
            source=first,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
        )
        assert installed.returncode == 0, installed.stderr.decode(errors="replace")
        assert (install / "MovieMakerLauncher.exe").read_bytes() == b"launcher-v1"
        assert (start_menu / "Movie Maker Reproduction.lnk").is_file()
        desktop_link = desktop / "Movie Maker Reproduction.lnk"
        assert desktop_link.is_file()
        association_key = rf"{registry_root}\Classes\.mmrproj"
        assert _registry_default(association_key) == "MovieMakerReproduction.Project"
        assert str(install) in path_file.read_text(encoding="utf-8-sig")

        # These changes belong to the user and must survive both update and removal.
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, association_key) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "User.Custom.Project")
        desktop_link.write_bytes(b"user-modified-shortcut")
        user_project = install / "내 프로젝트.mmrproj"
        user_media = install / "원본 영상.mp4"
        user_project.write_text("user project", encoding="utf-8")
        user_media.write_bytes(b"source media")
        path_file.write_text(
            path_file.read_text(encoding="utf-8-sig") + r";C:\User Added",
            encoding="utf-8",
        )

        updated = _run_lifecycle(
            "Update",
            source=second,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
        )
        assert updated.returncode == 0, updated.stderr.decode(errors="replace")
        assert (install / "MovieMakerLauncher.exe").read_bytes() == b"launcher-v2"
        assert user_project.read_text(encoding="utf-8") == "user project"
        assert user_media.read_bytes() == b"source media"
        assert desktop_link.read_bytes() == b"user-modified-shortcut"
        assert _registry_default(association_key) == "User.Custom.Project"

        downgraded = _run_lifecycle(
            "Update",
            source=older,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
        )
        assert downgraded.returncode != 0
        assert b"Downgrade refused" in downgraded.stderr
        assert (install / "MovieMakerLauncher.exe").read_bytes() == b"launcher-v2"

        failed = _run_lifecycle(
            "Update",
            source=broken,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
            fail_after_swap=True,
        )
        assert failed.returncode != 0
        assert (install / "MovieMakerLauncher.exe").read_bytes() == b"launcher-v2"
        assert user_project.is_file()
        assert user_media.is_file()

        registration_failed = _run_lifecycle(
            "Update",
            source=broken,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
            fail_after_registrations=True,
        )
        assert registration_failed.returncode != 0
        assert (install / "MovieMakerLauncher.exe").read_bytes() == b"launcher-v2"
        assert desktop_link.read_bytes() == b"user-modified-shortcut"
        assert _registry_default(association_key) == "User.Custom.Project"
        assert r"C:\User Added" in path_file.read_text(encoding="utf-8-sig")

        interrupted = _run_lifecycle(
            "Update",
            source=broken,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
            exit_after_swap=True,
        )
        assert interrupted.returncode == 91
        recovered = _run_lifecycle(
            "Recover",
            source=None,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
        )
        assert recovered.returncode == 0, recovered.stderr.decode(errors="replace")
        assert (install / "MovieMakerLauncher.exe").read_bytes() == b"launcher-v2"

        cache = app_data / "Local" / "OpenAI" / "MovieMakerReproduction" / "Cache"
        logs = app_data / "Local" / "OpenAI" / "MovieMakerReproduction" / "Logs"
        autosaves = app_data / "Roaming" / "OpenAI" / "MovieMakerReproduction" / "autosaves"
        for directory in (cache, logs, autosaves):
            directory.mkdir(parents=True)
            (directory / "fixture.bin").write_bytes(b"generated")

        removed = _run_lifecycle(
            "Uninstall",
            source=None,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
            app_data_root=app_data,
            purge_cache=True,
            purge_logs=True,
        )
        assert removed.returncode == 0, removed.stderr.decode(errors="replace")
        assert not (install / "MovieMakerLauncher.exe").exists()
        assert user_project.is_file()
        assert user_media.is_file()
        assert desktop_link.read_bytes() == b"user-modified-shortcut"
        assert not (start_menu / "Movie Maker Reproduction.lnk").exists()
        assert _registry_default(association_key) == "User.Custom.Project"
        final_path = path_file.read_text(encoding="utf-8-sig")
        assert str(install) not in final_path
        assert r"C:\User Added" in final_path
        assert not (state / "install-state.json").exists()
        assert not cache.exists()
        assert not logs.exists()
        assert (autosaves / "fixture.bin").read_bytes() == b"generated"
    finally:
        _delete_registry_tree(registry_root)


def test_payload_manifest_rejects_modified_file_before_install(tmp_path: Path) -> None:
    registry_root = rf"Software\MovieMakerReproduction\Tests\{uuid.uuid4().hex}"
    payload = _payload(tmp_path / "payload", "1.0.0", b"original", with_manifest=True)
    (payload / "MovieMakerLauncher.exe").write_bytes(b"tampered")
    install = tmp_path / "install"

    try:
        result = _run_lifecycle(
            "Install",
            source=payload,
            install=install,
            state=tmp_path / "state",
            start_menu=tmp_path / "start-menu",
            desktop=tmp_path / "desktop",
            registry_root=registry_root,
            path_file=tmp_path / "path.txt",
        )

        assert result.returncode != 0
        assert not install.exists()
        assert b"integrity" in result.stderr.lower()
    finally:
        _delete_registry_tree(registry_root)


def test_payload_manifest_rejects_unlisted_file_before_install(tmp_path: Path) -> None:
    registry_root = rf"Software\MovieMakerReproduction\Tests\{uuid.uuid4().hex}"
    payload = _payload(tmp_path / "payload", "1.0.0", b"original", with_manifest=True)
    (payload / "unexpected.exe").write_bytes(b"not-listed")
    install = tmp_path / "install"

    try:
        result = _run_lifecycle(
            "Install",
            source=payload,
            install=install,
            state=tmp_path / "state",
            start_menu=tmp_path / "start-menu",
            desktop=tmp_path / "desktop",
            registry_root=registry_root,
            path_file=tmp_path / "path.txt",
        )

        assert result.returncode != 0
        assert not install.exists()
        assert b"unlisted file" in result.stderr.lower()
    finally:
        _delete_registry_tree(registry_root)


@pytest.mark.skipif(not BUILT_PAYLOAD.is_dir(), reason="Build the Windows package first")
def test_built_release_payload_install_and_uninstall_round_trip(tmp_path: Path) -> None:
    registry_root = rf"Software\MovieMakerReproduction\Tests\{uuid.uuid4().hex}"
    install = tmp_path / "실제 패키지 설치 경로"
    state = tmp_path / "installer-state"
    start_menu = tmp_path / "start-menu"
    desktop = tmp_path / "desktop"
    path_file = tmp_path / "path.txt"

    try:
        assert not any(path.name == "__pycache__" for path in BUILT_PAYLOAD.rglob("*"))
        assert not any(path.suffix in {".pyc", ".pyo"} for path in BUILT_PAYLOAD.rglob("*"))
        installed = _run_lifecycle(
            "Install",
            source=BUILT_PAYLOAD,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
        )
        assert installed.returncode == 0, installed.stderr.decode(errors="replace")
        contract = json.loads(
            (install / "scripts" / "environment" / "runtime-contract.json").read_text(
                encoding="utf-8-sig"
            )
        )
        bundled_uv = install / Path(contract["uv"]["bundledCandidate"])
        assert hashlib.sha256(bundled_uv.read_bytes()).hexdigest() == contract["uv"]["sha256"]
        assert (install / "MovieMakerLauncher.exe").is_file()
        assert (install / "MovieMakerSetup.exe").is_file()

        removed = _run_lifecycle(
            "Uninstall",
            source=None,
            install=install,
            state=state,
            start_menu=start_menu,
            desktop=desktop,
            registry_root=registry_root,
            path_file=path_file,
        )
        assert removed.returncode == 0, removed.stderr.decode(errors="replace")
        assert not (install / "MovieMakerLauncher.exe").exists()
        assert not bundled_uv.exists()
        assert not install.exists()
        assert not (state / "install-state.json").exists()
    finally:
        _delete_registry_tree(registry_root)
