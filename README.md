# Windows Movie Maker Reproduction

> 사라진 Windows Movie Maker의 간결한 편집 경험을 오늘의 환경에 되살리는 데스크톱 영상 편집기

Windows Movie Maker Reproduction은 영상·사진·음악을 불러오고, 필요한 부분을 자르고 순서를
정한 뒤 자막과 효과를 더해 MP4로 저장할 수 있는 비공식 독립 프로젝트입니다. 전문 편집기의
복잡한 기능보다 **쉽게 배우고 빠르게 완성하는 경험**에 집중하며 원본 미디어를 수정하지 않는
비파괴 편집 방식을 사용합니다.

미디어 가져오기, 타임라인 편집, 실제 영상·오디오 미리 보기, 프로젝트 저장·복구,
내레이션·텍스트·전환·시각 효과와 H.264/AAC MP4 출력이 구현되어 있습니다. 첫 사용 가능
버전은 아직 정식 배포되지 않았으며 현재는 Python 소스 환경에서 실행합니다.

## 준비

다음 항목이 필요합니다.

- 64비트 Windows 또는 Linux
- [uv 0.12.1 이상](https://docs.astral.sh/uv/getting-started/installation/)
- 같은 배포본의 FFmpeg와 ffprobe
- Linux에서는 Qt GUI에 필요한 시스템 런타임 라이브러리

FFmpeg에는 H.264/AAC 출력에 필요한 `libx264` 및 `aac` 인코더가 포함되어야 합니다. 프로젝트는
FFmpeg를 자동으로 다운로드하거나 시스템 `PATH`를 변경하지 않습니다.

Windows에서는 [FFmpeg Windows 다운로드 안내](https://ffmpeg.org/download.html#build-windows)에
연결된 64비트 배포본을 내려받아 압축을 풉니다. `ffmpeg.exe`와 `ffprobe.exe`가 같은 `bin`
폴더에 있어야 합니다.

## 실행 환경 구성

### Windows

저장소 루트의 일반 PowerShell에서 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\environment\setup.ps1 `
  -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

테스트와 정적 검사 도구도 설치하려면 `-Dev`를 추가합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\environment\setup.ps1 `
  -Dev `
  -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

예시의 `C:\Tools\ffmpeg\bin`은 실제로 FFmpeg를 압축 해제한 `bin` 폴더로 바꿉니다.

### Linux

FFmpeg와 Qt GUI 런타임 라이브러리를 설치합니다. FFmpeg가 `PATH`에 있다면 저장소 루트에서
다음 명령을 실행합니다.

```bash
bash ./scripts/environment/setup.sh
```

개발 도구까지 설치하려면 `--dev`를 추가합니다.

```bash
bash ./scripts/environment/setup.sh --dev
```

배포판별 Linux 패키지와 사용자 지정 FFmpeg 경로는
[개발·실행 환경 안내](docs/guides/guide-0001-development-environment.md)를 참고합니다.

## 애플리케이션 실행

환경 구성이 끝나면 같은 FFmpeg 위치를 사용해 실행합니다.

### Windows

```powershell
.\scripts\environment\run.ps1 -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

### Linux

```bash
bash ./scripts/environment/run.sh
```

Windows에서 프로젝트 파일을 바로 열려면 앱 인자를 `--` 뒤에 전달합니다.

```powershell
.\scripts\environment\run.ps1 -FFmpegDirectory "C:\Tools\ffmpeg\bin" -- `
  --project "C:\Videos\여행 프로젝트.mmrproj"
```

GUI를 열지 않고 Python, Qt와 주요 패키지 로딩 상태만 확인할 수도 있습니다.

```powershell
.\scripts\environment\run.ps1 -FFmpegDirectory "C:\Tools\ffmpeg\bin" -- --check
```

```bash
bash ./scripts/environment/run.sh -- --check
```

## 로그 위치

Python 애플리케이션은 실행 방법과 관계없이 로컬 파일 로그를 기록합니다. Windows 기본 로그
폴더는 다음과 같습니다.

```text
C:\Users\<사용자 이름>\AppData\Local\OpenAI\MovieMakerReproduction\Logs
```

PowerShell에서는 다음 명령으로 바로 열 수 있습니다.

```powershell
explorer.exe "$env:LOCALAPPDATA\OpenAI\MovieMakerReproduction\Logs"
```

로그 파일 이름은 다음 형식입니다.

```text
app-<날짜>-<시각>-<프로세스 ID>.log
```

예:

```text
app-20260917-001112-2860.log
```

로그에는 앱 시작·종료, 복구 초기화 실패, 프로젝트 열기 실패, 미리 보기·오디오·출력 오류,
처리되지 않은 Python 예외와 Qt 진단이 기록됩니다. 사용자 프로필 경로와 일반적인 비밀값
형식은 저장 전에 가립니다.

로그는 자동 업로드하지 않으며 `app-*.log`만 14일/10MiB 범위에서 정리합니다. 로그 폴더를
만들거나 쓸 수 없어도 애플리케이션 실행은 계속됩니다.

## 개발 검사

먼저 Windows의 `setup.ps1 -Dev` 또는 Linux의 `setup.sh --dev`로 개발 환경을 구성합니다.

```powershell
uv --managed-python run --locked --no-sync -- pytest
uv --managed-python run --locked --no-sync -- ruff check .
uv --managed-python run --locked --no-sync -- mypy
git diff --check
```

## 더 알아보기

- [제품 개요·범위·로드맵·개발 상태](docs/product/product-0003-project-overview.md)
- [개발·실행 환경과 문제 해결](docs/guides/guide-0001-development-environment.md)
- [전체 프로젝트 문서 안내](docs/README.md)
