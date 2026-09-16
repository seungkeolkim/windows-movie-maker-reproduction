# Windows Movie Maker Reproduction

> 사라진 Windows Movie Maker의 간결한 편집 경험을 오늘의 환경에 되살리는 데스크톱 영상 편집기

Windows Movie Maker Reproduction은 영상 편집을 처음 접하는 사람도 영상·사진·음악을
불러오고, 필요한 부분을 자르고 순서를 정한 뒤 자막과 효과를 더해 MP4로 저장할 수 있게 만드는
비공식 독립 프로젝트입니다. 전문 편집기의 복잡한 기능보다 **쉽게 배우고 빠르게 완성하는
경험**에 집중하며, 원본 미디어를 수정하지 않는 비파괴 편집 방식을 사용합니다.

현재 미디어 가져오기, 타임라인 편집, 실제 영상·오디오 미리 보기, 프로젝트 저장·복구,
내레이션·텍스트·전환·시각 효과, H.264/AAC MP4 출력과 Windows 설치 흐름이 구현되어 있습니다.
첫 사용 가능 버전은 아직 정식 배포되지 않았습니다.

## 설치

### Windows 배포 패키지

배포 ZIP을 받은 경우 압축을 푼 뒤 `SIGNING-STATUS.txt`를 확인하고
`MovieMakerSetup.exe`를 실행합니다. 설치 과정에서 시작 메뉴·바탕 화면 바로가기와
`.mmrproj` 파일 연결을 선택할 수 있습니다.

배포 패키지는 FFmpeg와 ffprobe를 포함하지 않습니다. 같은 배포본의 두 실행 파일을 준비하고
첫 실행 화면에서 두 파일이 있는 `bin` 폴더를 선택합니다.

첫 실행에는 Python과 잠긴 패키지를 내려받기 위한 인터넷 연결이 필요합니다. 별도의 Python,
uv, Qt SDK 또는 빌드 도구는 설치하지 않아도 됩니다.

### 소스에서 설치

다음 항목을 먼저 준비합니다.

- 64비트 Windows 또는 Linux
- [uv 0.12.1 이상](https://docs.astral.sh/uv/getting-started/installation/)
- 같은 배포본의 FFmpeg와 ffprobe(`libx264` 및 `aac` 인코더 포함)

Windows에서는 [FFmpeg Windows 다운로드 안내](https://ffmpeg.org/download.html#build-windows)에
연결된 64비트 빌드를 내려받아 압축을 푼 뒤, 저장소 루트의 PowerShell에서 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\environment\setup.ps1 `
  -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

Linux에서는 FFmpeg와 Qt GUI 런타임 라이브러리를 설치한 뒤 실행합니다. FFmpeg가 `PATH`에
있으면 별도 경로 인자가 필요하지 않습니다.

```bash
bash ./scripts/environment/setup.sh
```

배포판별 Linux 패키지, 사용자 지정 FFmpeg 경로, 개발 의존성 설치 방법은
[개발·실행 환경 안내](docs/guides/guide-0001-development-environment.md)를 참고합니다.

## 실행

Windows 설치 패키지를 사용했다면 시작 메뉴 또는 바탕 화면의 Movie Maker 바로가기를
실행합니다.

소스에서 설치했다면 저장소 루트에서 운영체제에 맞는 명령을 실행합니다.

```powershell
.\scripts\environment\run.ps1 -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

```bash
bash ./scripts/environment/run.sh
```

## 더 알아보기

- [제품 개요·범위·로드맵·개발 상태](docs/product/product-0003-project-overview.md)
- [개발·실행 환경, 문제 해결과 패키징](docs/guides/guide-0001-development-environment.md)
- [전체 프로젝트 문서 안내](docs/README.md)
