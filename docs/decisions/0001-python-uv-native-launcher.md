# ADR-0001: Python, uv 및 네이티브 런처

- 상태: 승인됨
- 결정일: 2026-09-06
- 적용 대상: 애플리케이션 구현, 개발 환경, 사용자 실행 방식

## 배경

프로젝트는 Windows Movie Maker의 간결한 편집 경험을 복각하면서도 작은 팀이 빠르게 기능을 구현하고 유지보수할 수 있어야 한다. Python은 UI와 편집 로직을 빠르게 개발하기 좋지만, 사용자의 시스템 Python과 패키지 상태에 의존하면 동일한 코드가 서로 다른 환경에서 다르게 동작할 수 있다.

Python 런타임과 모든 의존성을 저장소 또는 portable 배포본에 포함하는 방식도 검토했다. 이 방식은 사용이 간단하지만 배포 용량과 업데이트 책임이 커진다. 이 프로젝트에서는 런타임을 번들하지 않고, 고정된 환경 명세로 재현성을 확보하기로 한다.

## 결정

1. 애플리케이션 로직과 UI는 Python으로 작성한다.
2. 데스크톱 UI의 기본 프레임워크로 PySide6와 Qt Widgets를 사용한다.
3. 미디어 정보 분석은 ffprobe, 렌더링과 내보내기는 FFmpeg에 위임한다.
4. CPython 런타임, `.venv`, PySide6, FFmpeg 실행 파일은 Git 저장소에 포함하지 않는다.
5. 프로젝트 환경은 uv가 만들고 관리한다.
6. 정확한 Python 버전은 `.python-version`, 직접 의존성은 `pyproject.toml`, 전체 의존성 해상 결과는 `uv.lock`에 기록한다.
7. 개발자와 사용자가 실행할 수 있는 명령행 경로를 항상 유지한다.
8. 별도의 작은 Windows 네이티브 GUI 런처를 제공하되, 런처는 Python 런타임을 포함하거나 애플리케이션 로직을 구현하지 않는다.

첫 프로젝트 스캐폴딩에서 CPython 3.13.14와 uv 0.12.1 이상을 초기 실행 기준으로 확정했다. 환경 구성과 실행 명령은 `--managed-python`을 사용하여 기존 시스템 Python 대신 uv 관리형 Python을 강제한다. 버전을 변경할 때는 PySide6와 미디어 의존성을 다시 검증하고 `.python-version`, `pyproject.toml`, `uv.lock`, 설정 스크립트와 사용자 문서를 함께 갱신한다.

## 저장소의 예정 구조

```text
.
├─ .python-version
├─ pyproject.toml
├─ uv.lock
├─ src/
│  └─ movie_maker/
│     ├─ __main__.py
│     ├─ ui/
│     ├─ timeline/
│     ├─ media/
│     └─ project/
├─ launcher/
│  └─ Windows 네이티브 런처 소스
├─ scripts/
│  └─ environment/
│     ├─ check-prerequisites.ps1 / .sh
│     ├─ setup.ps1 / .sh
│     └─ run.ps1 / .sh
├─ tests/
└─ docs/
```

## 표준 환경 구성과 실행

처음 환경을 구성하거나 의존성 정의가 변경됐을 때 다음을 실행한다.

```powershell
uv --managed-python python install 3.13.14
uv --managed-python sync --locked --no-dev
```

애플리케이션은 프로젝트가 제공할 `movie-maker` 엔트리 포인트로 실행한다.

```powershell
uv --managed-python run --locked --no-sync -- movie-maker
```

`--managed-python`은 시스템에 기존 Python이 있더라도 uv가 설치한 고정 버전을 사용하게 한다. `--locked`는 실행 중 `uv.lock`이 변경되지 않아야 한다는 계약이고, `--no-sync`는 준비된 환경으로 실행할 때 암묵적인 패키지 변경을 막는다. 개발자가 의존성을 의도적으로 변경할 때만 lockfile을 다시 생성하고 테스트한다. 가상환경을 수동으로 활성화하는 절차는 표준 실행 방법으로 사용하지 않는다.

Windows에서는 `scripts/environment/*.ps1`, Linux에서는 대응하는 `scripts/environment/*.sh`를 사용한다. 두 스크립트 세트는 같은 Python 버전, 잠금 파일, FFmpeg 검사 항목과 앱 인자 전달 규약을 유지한다. Windows 전용 네이티브 런처는 이 교차 플랫폼 명령행 경로와 별개로 제공한다.

## 런처의 책임

`MovieMakerLauncher.exe`는 다음 순서로 동작한다.

1. 런처 자신의 위치를 기준으로 프로젝트 루트를 찾는다.
2. `uv`가 실행 가능한지 확인하고, 없으면 설치 안내를 표시한다.
3. `.python-version`, `pyproject.toml`, `uv.lock`의 존재를 확인한다.
4. `.venv`가 없거나 환경 명세가 변경되었으면 사용자의 동의를 받아 `uv --managed-python sync --locked --no-dev`를 실행한다.
5. FFmpeg와 ffprobe의 위치 및 실행 가능 여부를 점검한다.
6. 환경이 준비되면 `uv --managed-python run --locked --no-sync -- movie-maker`를 실행한다.
7. 표준 오류, 종료 코드와 진단 정보를 로그에 남긴다.

일반 실행 시 런처는 다음 상태를 명확히 구분한다.

- 준비되지 않음: uv, Python, 의존성 또는 FFmpeg가 없음
- 준비 중: 다운로드 또는 환경 동기화 진행 중
- 준비됨: 즉시 실행 가능
- 복구 필요: lockfile 불일치, 손상된 `.venv`, 실행 파일 누락

런처 UI는 최소한 환경 상태, 실행 모드, 환경 구성/복구 버튼, 실행 버튼과 진단 로그 진입점을 제공한다. 자주 쓰는 옵션은 체크박스나 선택 항목으로 노출하고, 고급 사용자를 위한 추가 인자 입력란을 둘 수 있다.

## 인자 전달 규약

런처가 소비하는 옵션과 Python 앱이 소비하는 옵션을 `--`로 분리한다.

```powershell
MovieMakerLauncher.exe --verbose -- --online --project "C:\Videos\travel.wmmr"
```

- `--verbose`: 런처 옵션
- 첫 번째 `--` 이후: Python 앱에 전달할 인자
- `--online`: 앱의 온라인 기능 사용 모드
- `--project`: 열 프로젝트 파일

런처가 실행하는 대응 명령은 다음과 같다.

```powershell
uv --managed-python run --locked --no-sync -- movie-maker --online --project "C:\Videos\travel.wmmr"
```

구현 시 인자를 하나의 셸 문자열로 결합해 `cmd.exe`에 넘기지 않는다. Windows 프로세스 API를 사용하고 인자 경계와 따옴표를 보존하여 공백이 있는 경로와 임의 입력을 안전하게 처리한다.

## PATH 정책

- 시스템 Python과 프로젝트 `.venv`를 `PATH`에 등록하지 않는다.
- FFmpeg를 프로젝트가 자동으로 전역 `PATH`에 등록하지 않는다.
- 런처가 내부·설정된 실행 파일을 사용할 때는 절대 경로를 사용한다.
- 터미널에서 `MovieMakerLauncher`를 호출하려는 사용자를 위해 런처 디렉터리만 선택적으로 사용자 `PATH`에 등록할 수 있다.
- PATH, 바로가기 또는 파일 연결을 등록했다면 런처의 등록 해제 기능이 동일한 변경을 되돌릴 수 있어야 한다.

PATH 등록은 선택 기능이다. 기본 사용 흐름은 저장소 또는 배포 디렉터리에서 런처를 직접 실행하는 것이다.

## 온라인과 오프라인의 의미

앱의 `--online` 옵션과 uv의 네트워크 사용은 서로 다른 개념이다.

- 앱 `--online`: 향후 온라인 공유나 네트워크 기반 앱 기능을 허용하는 모드
- uv 네트워크 사용: Python 또는 잠긴 의존성을 최초로 준비할 때 필요할 수 있음

이미 준비된 환경에서 앱을 실행하는 행위가 임의의 의존성 업데이트를 발생시켜서는 안 된다. 환경 변경은 명시적인 구성 또는 복구 단계에서만 수행한다.

## FFmpeg 정책

초기 버전은 FFmpeg와 ffprobe를 시스템 `PATH`, 사용자 설정 경로 또는 프로젝트의 `tools/` 디렉터리에서 찾는다. 발견된 실행 파일의 버전과 필요한 코덱 지원 여부를 검사하며, 찾지 못하면 실행 또는 내보내기 전에 해결 방법을 안내한다.

런처가 FFmpeg를 자동 다운로드하거나 특정 빌드를 함께 배포하는 기능은 이 결정에 포함하지 않는다. 배포 방식, 코덱 특허 및 FFmpeg 빌드의 LGPL/GPL 조건을 검토한 뒤 별도의 ADR로 결정한다.

## 결과

### 장점

- 저장소와 배포 산출물에 거대한 Python 환경을 포함하지 않는다.
- Python과 패키지 버전을 고정하여 개발 환경의 차이를 줄인다.
- Python의 개발 속도와 PySide6의 네이티브 데스크톱 UI를 활용한다.
- 명령행 실행과 사용자 친화적인 GUI 실행을 함께 제공한다.
- 런처와 애플리케이션 로직을 독립적으로 테스트하고 교체할 수 있다.

### 비용과 제약

- 첫 실행 전 사용자가 uv를 설치해야 한다.
- 최초 환경 구성에는 네트워크와 디스크 공간이 필요할 수 있다.
- Python, PySide6 및 FFmpeg의 호환 가능한 조합을 프로젝트가 검증해야 한다.
- 런처는 환경 오류를 이해하기 쉬운 형태로 변환하고 복구 수단을 제공해야 한다.
- 완전한 단일 파일 또는 무설치 portable 애플리케이션은 제공하지 않는다.

## 제외한 대안

- CPython과 모든 패키지를 포함하는 대용량 portable 폴더
- PyInstaller 등의 단일 파일 번들만을 주 배포 수단으로 사용
- 시스템에 임의로 설치된 Python과 패키지를 그대로 사용
- 애플리케이션 전체를 C++ 또는 C#으로 먼저 구현

이 대안들은 영구적으로 금지된 것이 아니다. 요구 사항이 바뀌면 새로운 ADR에서 비용과 이점을 다시 평가한다.
