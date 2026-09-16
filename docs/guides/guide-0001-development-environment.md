# GUIDE-0001: 개발·실행 환경 안내

- 문서 번호: GUIDE-0001
- 상태: 승인됨
- 목적: 소스 환경, FFmpeg 탐색, 개발 검사, 문제 해결과 Windows 패키징 절차를 설명한다.

모든 명령은 별도 설명이 없으면 저장소 루트에서 실행한다. 일반 사용자를 위한 최소 설치와
실행 절차는 [프로젝트 README](../../README.md)를 먼저 확인한다.

## 기술 및 실행 방식

애플리케이션 로직과 UI는 Python + PySide6(Qt Widgets)로 구현하고 미디어 분석과 렌더링은
FFmpeg/ffprobe에 위임한다. 저장소는 Python 런타임이나 완성된 가상환경을 포함하지 않는다.
Windows 설치 패키지는 검증된
[uv](https://docs.astral.sh/uv/) 실행 파일을 포함해 프로젝트 전용 `.venv`를 구성하고,
소스에서 실행하는 개발자는 설치된 uv를 사용한다.

재현 가능한 실행 환경은 다음 파일로 관리한다.

- `.python-version`: 프로젝트가 요구하는 정확한 CPython 버전
- `pyproject.toml`: 애플리케이션 메타데이터와 직접 의존성
- `uv.lock`: 전이 의존성을 포함한 정확한 패키지 조합

기준 환경은 uv가 관리하는 CPython 3.13.14와 uv 0.12.1 이상이다. `--managed-python`을
사용하므로 컴퓨터에 설치된 다른 Python을 사용하지 않는다. 의존성을 변경할 때는 `uv.lock`도
함께 갱신하고 검증한다.

## 사전 요구 사항

### Windows 설치 패키지

- 64비트 Windows
- 최초 Python 및 패키지 다운로드를 위한 인터넷 연결
- 같은 배포본에서 가져온 FFmpeg와 ffprobe

별도 .NET 런타임, C/C++ 빌드 도구, uv, Python, Qt SDK 또는 PySide6는 필요하지 않다.
네이티브 EXE는 릴리스 컴퓨터에서 미리 빌드하며 Windows 시스템 구성 요소만 사용한다.
패키지의 고정 uv가 사용자 승인 후 CPython 3.13.14와 잠긴 Python 패키지를 구성한다.

### 소스 환경

- 64비트 Windows 또는 64비트 Linux(`x86_64`, `aarch64`)
- [uv 0.12.1 이상](https://docs.astral.sh/uv/getting-started/installation/)
- 같은 배포본에서 가져온 FFmpeg와 ffprobe
- Linux의 Qt GUI에 필요한 X11/Wayland, OpenGL/EGL, XKB/XCB, 글꼴, DBus와 오디오
  런타임 라이브러리

Python, Qt SDK, PySide6 또는 가상환경을 별도로 설치하거나 활성화할 필요가 없다. 설정
스크립트가 고정 CPython을 uv 관리 영역에 설치하고 저장소의 `.venv`를 구성한다. Linux
시스템 GUI·오디오 라이브러리는 uv의 관리 대상이 아니므로 별도로 준비한다.

현재 PySide6 Linux 휠을 기준으로 `x86_64`는 glibc 2.34 이상, `aarch64`는 glibc 2.39
이상인 배포판을 권장한다.

## FFmpeg 준비와 탐색

FFmpeg는 Python 패키지가 아니며 uv가 설치하지 않는다. `libx264` H.264 인코더와 FFmpeg의
`aac` 인코더를 지원하는 빌드의 FFmpeg와 ffprobe를 함께 설치한다.

Windows에서는 [FFmpeg Windows 다운로드 안내](https://ffmpeg.org/download.html#build-windows)에
연결된 64비트 빌드를 사용할 수 있다. Linux에서는 배포판 패키지 관리자를 사용한다.

```bash
# Debian/Ubuntu
sudo apt update
sudo apt install ffmpeg

# Arch Linux
sudo pacman -S ffmpeg
```

Fedora 계열은 FFmpeg 패키지를 제공하는 저장소를 먼저 활성화해야 할 수 있다. 두 실행 파일은
항상 같은 배포본과 `bin` 디렉터리에서 가져온다.

설정·실행 스크립트는 다음 순서로 FFmpeg를 찾는다.

1. Windows의 `-FFmpegDirectory` 또는 Linux의 `--ffmpeg-dir` 인자
2. `MOVIE_MAKER_FFMPEG_DIR` 환경 변수
3. 저장소의 `tools/ffmpeg/bin` 디렉터리
4. 현재 `PATH`

점검만 별도로 실행할 수도 있다.

```powershell
.\scripts\environment\check-prerequisites.ps1 `
  -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

```bash
bash ./scripts/environment/check-prerequisites.sh \
  --ffmpeg-dir /opt/ffmpeg/bin
```

`tools/`는 Git에서 제외되므로 개인 환경에서는 FFmpeg를 저장소 아래에 둘 수 있다. 프로젝트는
FFmpeg를 자동 다운로드하거나 시스템 `PATH`를 수정하지 않는다.

점검 스크립트는 실행 여부와 함께 `libx264`/`aac` 인코더, 트리밍, 합성, 크기 조절, 오디오
믹싱과 텍스트 출력에 필요한 필터를 확인한다.

## 실행 환경 구성

### Windows

PowerShell 실행 정책이 로컬 스크립트를 차단하는 환경에서도 현재 프로세스에 한해 실행할 수
있는 명령이다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\environment\setup.ps1 `
  -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

테스트와 린트 도구까지 설치하려면 `-Dev`를 추가한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\environment\setup.ps1 `
  -Dev `
  -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

### Linux

Debian/Ubuntu의 일반적인 Qt GUI 런타임 의존성은 다음과 같이 준비할 수 있다. 배포판 버전과
데스크톱 환경에 따라 패키지 이름이나 추가 패키지가 달라질 수 있다.

```bash
sudo apt update
sudo apt install \
  ffmpeg libdbus-1-3 libegl1 libfontconfig1 libgl1 libglib2.0-0 libpulse0 \
  libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
  libxcb-xfixes0 libxcb-xinerama0 libxcb-xinput0
```

FFmpeg가 `PATH`에 있다면 별도 인자 없이 설정한다.

```bash
bash ./scripts/environment/setup.sh
```

사용자 지정 FFmpeg와 개발 의존성을 함께 사용하려면 다음과 같이 실행한다.

```bash
bash ./scripts/environment/setup.sh \
  --dev \
  --ffmpeg-dir /opt/ffmpeg/bin
```

### 설정 스크립트의 공통 동작

1. 운영체제와 CPU 아키텍처, uv, FFmpeg와 ffprobe를 검사한다.
2. uv 관리형 CPython 3.13.14를 설치하거나 검증한다.
3. `uv.lock` 그대로 프로젝트 전용 `.venv`를 구성한다.
4. PySide6와 Qt Multimedia/SVG 플러그인을 실제로 불러온다.
5. 설치된 런타임 버전을 출력한다.

의존성을 바꾼 개발자만 `uv lock`으로 잠금 파일을 갱신한다. 일반 사용자와 CI는 기존
`uv.lock`을 사용한다.

## 애플리케이션 실행과 점검

```powershell
.\scripts\environment\run.ps1 -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

```bash
bash ./scripts/environment/run.sh
```

버전과 DLL 로딩만 확인하고 창을 열지 않으려면 다음과 같이 실행한다.

```powershell
.\scripts\environment\run.ps1 `
  -FFmpegDirectory "C:\Tools\ffmpeg\bin" `
  -- --check
```

```bash
bash ./scripts/environment/run.sh -- --check
```

스크립트를 거치지 않는 대응 명령은 다음과 같다. 먼저 FFmpeg가 `PATH` 또는
`MOVIE_MAKER_FFMPEG_DIR`에서 발견되어야 한다.

```powershell
uv --managed-python python install 3.13.14
uv --managed-python sync --locked --no-dev
uv --managed-python run --locked --no-sync -- movie-maker
```

`--locked`는 실행 중 잠금 파일 변경을 막고 `--no-sync`는 실행 시 암묵적인 패키지 설치나
제거를 막는다. `.venv`가 없거나 의존성이 맞지 않으면 운영체제에 맞는 설정 스크립트를 다시
사용한다.

## 개발 검사

`setup.ps1 -Dev` 또는 `setup.sh --dev`로 구성한 환경에서 실행한다.

```powershell
uv --managed-python run --locked --no-sync -- pytest
uv --managed-python run --locked --no-sync -- ruff check .
uv --managed-python run --locked --no-sync -- mypy
git diff --check
```

GUI 테스트는 `pytest-qt`와 PySide6를 사용하도록 `pyproject.toml`에 고정되어 있다.

## 문제 해결

- uv가 CPython 3.13.14를 찾지 못하면 `uv self update` 또는 uv를 설치한 패키지 관리자의
  업데이트 명령을 실행한다.
- `DLL load failed`가 발생하면 Windows Update를 적용하고
  [Microsoft Visual C++ 재배포 가능 패키지 x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)를
  설치한 뒤 다시 시도한다.
- Qt 플랫폼 플러그인 오류가 발생하면 외부 프로그램이 설정한 `QT_PLUGIN_PATH`나
  `PYTHONPATH`를 제거한 새 PowerShell에서 실행한다.
- Linux에서 `xcb` 또는 OpenGL 플러그인 오류가 발생하면 GUI 런타임 패키지와 그래픽
  드라이버를 확인한다. 디스플레이 서버가 없는 SSH·CI 세션에서는 일반 GUI 창을 열 수 없다.
- `.venv`가 손상된 경우 생성물인 `.venv`를 이름 변경하거나 제거한 뒤 설정 스크립트를 다시
  실행한다. uv 관리형 Python과 패키지 캐시는 재사용된다.
- FFmpeg 검사에서 실패하면 FFmpeg와 ffprobe가 같은 배포본인지, 필수 인코더와 필터가
  포함된 빌드인지 확인한다.

## Windows 설치 패키지와 런처

배포 ZIP을 푼 뒤 `SIGNING-STATUS.txt`를 확인하고 `MovieMakerSetup.exe`를 실행한다. 시작
메뉴, 바탕 화면 바로가기, `.mmrproj` 파일 연결과 런처 디렉터리의 사용자 `PATH` 등록을
각각 선택할 수 있다. `PATH`에는 `MovieMakerLauncher.exe`가 있는 디렉터리만 등록하며
Python, `.venv`, uv와 FFmpeg는 등록하지 않는다.

첫 실행에서 `MovieMakerLauncher.exe`는 패키지의 uv, 고정 Python, `.venv`와
FFmpeg/ffprobe를 점검한다. 환경 구성 또는 복구는 설명 후 사용자가 승인한 경우에만 실행하며,
일반 앱 실행은 네트워크나 환경 변경을 일으키지 않는다.

```powershell
MovieMakerLauncher.exe -- --online
MovieMakerLauncher.exe -- --project "C:\Videos\여행 프로젝트.mmrproj"
```

첫 번째 `--` 뒤의 인자는 Python 애플리케이션에 원래 argv 단위로 전달한다. `--online`은
앱 동작 모드이며 의존성 설치나 제품 업데이트를 뜻하지 않는다. 기본 로그 위치는
`%LOCALAPPDATA%\OpenAI\MovieMakerReproduction\Logs`다. 유지관리 화면에서 새 패키지로
명시적 업데이트하거나 제거할 수 있으며, 제거는 사용자 프로젝트와 원본 미디어를 보존한다.

릴리스 빌드는 MSVC Developer PowerShell 또는 검증된 portable Zig가 있는 개발 컴퓨터에서
실행한다. 이 도구들은 사용자 패키지에 포함하지 않는다.

```powershell
.\launcher\build.ps1 -ZigPath C:\Tools\zig\zig.exe
.\scripts\packaging\build-windows.ps1 -ZigPath C:\Tools\zig\zig.exe
```

관련 결정과 세부 계약은 다음 문서를 따른다.

- [ADR-0001: Python, uv 및 네이티브 런처](../decisions/adr-0001-python-uv-native-launcher.md)
- [ADR-0005: Windows 패키지에 검증된 uv 포함](../decisions/adr-0005-bundle-verified-uv-on-windows.md)
- [DESIGN-0013: Windows 런처와 설치 계약](../design/design-0013-windows-launcher-installer-contract.md)
- [POLICY-0002: 실행 환경 개인정보와 네트워크](../policies/policy-0002-runtime-privacy-and-network.md)
- [프로젝트 스크립트 안내](../../scripts/README.md)

라이선스와 영상 출력물의 권리 경계는
[POLICY-0001: 라이선스와 출력물 권리](../policies/policy-0001-licensing-and-output-rights.md)를
확인한다.
