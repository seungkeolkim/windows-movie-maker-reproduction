from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
ROOT = Path(__file__).parents[2]
INSPECTOR = ROOT / "scripts" / "environment" / "inspect-runtime.ps1"
PREPARER = ROOT / "scripts" / "environment" / "prepare-runtime.ps1"


def _inspect(
    app_root: Path,
    *,
    uv: Path | None = None,
    ffmpeg_directory: Path | None = None,
    without_path_media: bool = False,
    environment_updates: dict[str, str] | None = None,
) -> dict[str, object]:
    powershell = shutil.which("powershell.exe")
    assert powershell is not None
    arguments = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSPECTOR),
        "-AppRoot",
        str(app_root),
        "-Format",
        "Json",
    ]
    if uv is not None:
        arguments.extend(("-UvExecutable", str(uv)))
    if ffmpeg_directory is not None:
        arguments.extend(("-FFmpegDirectory", str(ffmpeg_directory)))
    environment = os.environ.copy()
    if without_path_media:
        environment["PATH"] = str(Path(os.environ["WINDIR"]) / "System32")
        environment.pop("MOVIE_MAKER_FFMPEG_DIR", None)
    if environment_updates:
        environment.update(environment_updates)
    process = subprocess.run(arguments, check=False, capture_output=True, env=environment)
    assert process.returncode in (0, 2), process.stderr.decode(errors="replace")
    return json.loads(process.stdout.decode("utf-8-sig"))


def _component(report: dict[str, object], name: str) -> dict[str, object]:
    components = report["components"]
    assert isinstance(components, list)
    return next(value for value in components if value["name"] == name)


def _minimal_runtime_root(root: Path) -> Path:
    scripts = root / "scripts" / "environment"
    scripts.mkdir(parents=True)
    contract = json.loads(
        (ROOT / "scripts" / "environment" / "runtime-contract.json").read_text(encoding="utf-8")
    )
    (scripts / "runtime-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    for relative in (".python-version", "pyproject.toml", "uv.lock", "README.md"):
        (root / relative).write_text(
            "3.13.14" if relative == ".python-version" else "fixture", encoding="utf-8"
        )
    return root


def _probe_fixture() -> Path:
    fixture = ROOT / "build" / "launcher" / "RuntimeProbeFixture.exe"
    if not fixture.is_file():
        pytest.skip("Build launcher tests first to provide RuntimeProbeFixture.exe")
    return fixture


def _working_fake_uv(path: Path) -> Path:
    path.write_text(
        "if ($args[0] -eq '--version') { Write-Output 'uv 0.12.1'; exit 0 }\n"
        "if ($args[0] -eq 'python') { Write-Output 'C:\\\\uv\\\\python\\\\python.exe'; exit 0 }\n"
        "exit 0\n",
        encoding="ascii",
    )
    return path


def test_missing_runtime_contract_is_typed_not_ready(tmp_path: Path) -> None:
    report = _inspect(tmp_path)

    assert report["overallState"] == "NotReady"
    assert _component(report, "files")["state"] == "NotReady"


def test_old_uv_and_missing_environment_are_distinguished(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts" / "environment"
    scripts.mkdir(parents=True)
    contract = json.loads(
        (ROOT / "scripts" / "environment" / "runtime-contract.json").read_text(encoding="utf-8")
    )
    (scripts / "runtime-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    for relative in (".python-version", "pyproject.toml", "uv.lock", "README.md"):
        (tmp_path / relative).write_text(
            "3.13.14" if relative == ".python-version" else "fixture", encoding="utf-8"
        )
    fake_uv = tmp_path / "fake-uv.ps1"
    fake_uv.write_text("Write-Output 'uv 0.1.0'\n$global:LASTEXITCODE = 0\n", encoding="ascii")

    report = _inspect(tmp_path, uv=fake_uv, without_path_media=True)

    assert _component(report, "uv")["state"] == "NotReady"
    assert _component(report, "environment")["state"] == "NotReady"
    assert _component(report, "ffmpeg")["state"] == "NotReady"


def test_damaged_venv_is_repair_required(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts" / "environment"
    scripts.mkdir(parents=True)
    contract = json.loads(
        (ROOT / "scripts" / "environment" / "runtime-contract.json").read_text(encoding="utf-8")
    )
    (scripts / "runtime-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    for relative in (".python-version", "pyproject.toml", "uv.lock", "README.md"):
        (tmp_path / relative).write_text(
            "3.13.14" if relative == ".python-version" else "fixture", encoding="utf-8"
        )
    (tmp_path / ".venv").mkdir()
    fake_uv = tmp_path / "fake-uv.ps1"
    fake_uv.write_text("Write-Output 'uv 0.12.1'\n$global:LASTEXITCODE = 0\n", encoding="ascii")

    report = _inspect(tmp_path, uv=fake_uv)

    assert report["overallState"] == "RepairRequired"
    assert _component(report, "environment")["state"] == "RepairRequired"


def test_bundled_uv_is_preferred_over_path_uv(tmp_path: Path) -> None:
    _minimal_runtime_root(tmp_path)
    installed_uv = shutil.which("uv.exe")
    assert installed_uv is not None
    bundled_uv = tmp_path / "tools" / "uv" / "uv.exe"
    bundled_uv.parent.mkdir(parents=True)
    shutil.copy2(installed_uv, bundled_uv)

    report = _inspect(tmp_path)

    uv_component = _component(report, "uv")
    assert uv_component["state"] == "Ready"
    assert Path(str(uv_component["executablePath"])) == bundled_uv


def test_ffmpeg_capabilities_and_missing_filters_are_typed(tmp_path: Path) -> None:
    _minimal_runtime_root(tmp_path)
    fake_uv = tmp_path / "fake-uv.ps1"
    fake_uv.write_text("Write-Output 'uv 0.12.1'\n$global:LASTEXITCODE = 0\n", encoding="ascii")
    media = tmp_path / "explicit media"
    media.mkdir()
    shutil.copy2(_probe_fixture(), media / "ffmpeg.exe")
    shutil.copy2(_probe_fixture(), media / "ffprobe.exe")

    ready = _inspect(tmp_path, uv=fake_uv, ffmpeg_directory=media)
    assert _component(ready, "ffmpeg")["state"] == "Ready"
    assert Path(str(_component(ready, "ffmpeg")["executablePath"])) == media / "ffmpeg.exe"

    missing = _inspect(
        tmp_path,
        uv=fake_uv,
        ffmpeg_directory=media,
        environment_updates={"MMR_PROBE_MISSING_CAPABILITIES": "1"},
    )
    missing_component = _component(missing, "ffmpeg")
    assert missing_component["state"] == "NotReady"
    assert "filters:" in str(missing_component["detail"])


def test_explicit_ffmpeg_priority_detects_split_distribution(tmp_path: Path) -> None:
    _minimal_runtime_root(tmp_path)
    fake_uv = tmp_path / "fake-uv.ps1"
    fake_uv.write_text("Write-Output 'uv 0.12.1'\n$global:LASTEXITCODE = 0\n", encoding="ascii")
    explicit = tmp_path / "chosen ffmpeg"
    fallback = tmp_path / "path ffprobe"
    explicit.mkdir()
    fallback.mkdir()
    shutil.copy2(_probe_fixture(), explicit / "ffmpeg.exe")
    shutil.copy2(_probe_fixture(), fallback / "ffprobe.exe")
    search_path = f"{fallback};{Path(os.environ['WINDIR']) / 'System32'}"

    report = _inspect(
        tmp_path,
        uv=fake_uv,
        ffmpeg_directory=explicit,
        environment_updates={"PATH": search_path},
    )

    component = _component(report, "ffmpeg")
    assert component["state"] == "RepairRequired"
    assert component["summary"] == "FFmpeg and ffprobe are in different directories."


@pytest.mark.parametrize("had_environment", [False, True])
def test_interrupted_runtime_preparation_is_recovered(
    tmp_path: Path,
    had_environment: bool,
) -> None:
    _minimal_runtime_root(tmp_path)
    fake_uv = tmp_path / "fake-uv.ps1"
    fake_uv.write_text("Write-Output 'uv 0.12.1'\n", encoding="ascii")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "incomplete.txt").write_text("incomplete", encoding="utf-8")
    backup = tmp_path / ".venv.w11-repair-fixture"
    if had_environment:
        backup.mkdir()
        (backup / "original.txt").write_text("original", encoding="utf-8")
    marker = tmp_path / ".runtime-prepare-state.json"
    marker.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "hadEnvironment": had_environment,
                "backupPath": str(backup) if had_environment else None,
            }
        ),
        encoding="utf-8",
    )

    process = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PREPARER),
            "-Mode",
            "Repair" if had_environment else "Setup",
            "-AppRoot",
            str(tmp_path),
            "-UvExecutable",
            str(fake_uv),
            "-RecoveryOnly",
        ],
        check=False,
        capture_output=True,
    )

    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert not marker.exists()
    assert not backup.exists()
    if had_environment:
        assert (venv / "original.txt").read_text(encoding="utf-8") == "original"
        assert not (venv / "incomplete.txt").exists()
    else:
        assert not venv.exists()


def test_failed_runtime_repair_restores_previous_environment(tmp_path: Path) -> None:
    _minimal_runtime_root(tmp_path)
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "original.txt").write_text("keep", encoding="utf-8")
    fake_uv = tmp_path / "failing-uv.ps1"
    fake_uv.write_text("exit 7\n", encoding="ascii")

    process = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PREPARER),
            "-Mode",
            "Repair",
            "-AppRoot",
            str(tmp_path),
            "-UvExecutable",
            str(fake_uv),
        ],
        check=False,
        capture_output=True,
    )

    assert process.returncode != 0
    assert (venv / "original.txt").read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / ".runtime-prepare-state.json").exists()
    assert not list(tmp_path.glob(".venv.w11-repair-*"))


def test_ready_state_and_wrong_python_version_are_distinguished(tmp_path: Path) -> None:
    _minimal_runtime_root(tmp_path)
    fake_uv = _working_fake_uv(tmp_path / "working-uv.ps1")
    venv = tmp_path / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "python.exe").write_bytes(b"fixture")
    config = venv / "pyvenv.cfg"
    config.write_text(
        "home = C:\\Users\\fixture\\uv\\python\\cpython-3.13.14\n"
        "version_info = 3.13.14\n",
        encoding="utf-8",
    )
    media = tmp_path / "media-tools"
    media.mkdir()
    shutil.copy2(_probe_fixture(), media / "ffmpeg.exe")
    shutil.copy2(_probe_fixture(), media / "ffprobe.exe")

    ready = _inspect(tmp_path, uv=fake_uv, ffmpeg_directory=media)
    assert ready["overallState"] == "Ready"
    assert _component(ready, "python")["state"] == "Ready"
    assert _component(ready, "environment")["state"] == "Ready"

    config.write_text(
        "home = C:\\Users\\fixture\\uv\\python\\cpython-3.12.0\n"
        "version_info = 3.12.0\n",
        encoding="utf-8",
    )
    mismatch = _inspect(tmp_path, uv=fake_uv, ffmpeg_directory=media)
    environment = _component(mismatch, "environment")
    assert environment["state"] == "RepairRequired"
    assert environment["summary"] == ".venv uses the wrong Python version."
