# Windows 네이티브 런처와 설치 수명주기 계약

- 문서 번호: `DESIGN-0013`
- 상태: 승인됨
- 적용 작업: `W-11`
- 관련 결정: [ADR-0001](../decisions/adr-0001-python-uv-native-launcher.md)
- 관련 정책:
  [POLICY-0001](../policies/policy-0001-licensing-and-output-rights.md),
  [POLICY-0002](../policies/policy-0002-runtime-privacy-and-network.md)

## 목적과 구현 경계

`MovieMakerLauncher.exe`는 C++17과 Win32 API로 만든 64비트 Windows GUI다. 설치되는 실행
파일은 Windows 시스템 DLL만 사용하며 .NET, Python 또는 C/C++ 빌드 도구를 요구하지 않는다.
MSVC Build Tools나 검증된 Zig는 릴리스 빌드 컴퓨터에서만 사용하고 제품에 포함하지 않는다.
점검·설치 스크립트에는 Windows 11 기본 구성 요소인 Windows PowerShell 5.1을 사용하지만 별도
.NET SDK나 최신 .NET Desktop Runtime 설치를 요구하지 않는다.

런처는 편집·프로젝트·미디어 로직을 구현하지 않는다. 환경 규칙은
`scripts/environment/runtime-contract.json`에 한 번 정의하고 PowerShell·Linux 스크립트와
런처가 함께 읽는다. Python 앱, Windows PowerShell 및 Linux Bash 실행 경로는 계속 지원한다.

## 제품 디렉터리와 빌드 산출물

설치 패키지의 `payload/`는 다음 구조를 사용한다.

```text
payload/
├─ MovieMakerLauncher.exe
├─ MovieMakerSetup.exe
├─ .python-version
├─ pyproject.toml
├─ uv.lock
├─ src/movie_maker/
├─ tools/uv/uv.exe
├─ scripts/environment/
├─ scripts/packaging/install-lifecycle.ps1
├─ licenses/uv-LICENSE-MIT.txt
├─ payload-manifest.json
├─ SIGNING-STATUS.txt
├─ RELEASE-NOTES.md
└─ THIRD_PARTY_NOTICES.md
```

기본 설치 위치는 `%LOCALAPPDATA%\Programs\MovieMakerReproduction`이다. Python, `.venv`,
PySide6/Qt와 FFmpeg/ffprobe 실행 파일은 payload에 포함하지 않는다. 사용자가 패키지 관리 도구를
별도로 설치하지 않도록 공식 Windows x64 uv 0.12.1 실행 파일과 라이선스 고지를 포함하고,
빌드 때 버전과 고정 SHA-256을 검사한다. 런처는 앱 루트의 uv를 시스템 PATH보다 우선한다.
런처는 자신의 실행 파일 디렉터리부터 최대 다섯 부모를 올라가며 런타임 계약을 찾아 앱 루트를
절대 경로로 확정한다. 따라서 소스 트리의 `build/launcher`와 설치 제품 루트에서 같은 바이너리를
쓸 수 있다.

`launcher/build.ps1`은 런처, GUI 설치 관리자와 네이티브 계약 테스트를 빌드한다. 기본은 활성
MSVC Developer PowerShell을 사용하고 `-ZigPath`로 검증된 휴대형 Zig를 지정할 수 있다.
`scripts/packaging/build-windows.ps1`은 명시한 파일만 stage하고 고정 ZIP 시각과 정렬된 경로로
재현 가능한 x64 ZIP을 만든다. 같은 입력·도구·출력 위치의 연속 빌드는 같은 SHA-256을 내야 한다.

## 환경 상태 모델

전체 상태는 다음 네 가지다.

| 상태 | 의미 | 실행 버튼 |
| --- | --- | --- |
| `NotReady` | uv, Python, `.venv`, 앱 파일 또는 FFmpeg 준비가 필요함 | 비활성 |
| `InProgress` | 점검, 구성, 복구 또는 시작 확인 중 | 비활성, 취소 활성 |
| `Ready` | 잠금 환경과 모든 외부 실행 파일을 검증함 | 활성 |
| `RepairRequired` | `.venv` 손상·불일치 또는 FFmpeg 쌍 불일치가 있음 | 비활성 |

`inspect-runtime.ps1`은 앱 파일, uv, 고정 Python, `.venv`, FFmpeg를 별도 타입 상태로 반환한다.
필수 파일과 최소 uv 버전은 런타임 계약에서 읽는다. `.venv/pyvenv.cfg`의 Python 패치 버전과
uv 관리형 home을 확인하고 `uv --managed-python sync --locked --no-dev --check`로 잠금 일치를
읽기 전용 검사한다.

FFmpeg 탐색 우선순위는 다음과 같다.

1. 런처 또는 스크립트에서 사용자가 선택한 경로
2. `MOVIE_MAKER_FFMPEG_DIR`
3. 앱 루트의 `tools/ffmpeg/bin`
4. 현재 `PATH`

ffmpeg와 ffprobe의 부모 디렉터리와 버전 토큰이 같아야 한다. `libx264`, `aac`과 W-06~W-10에
필요한 필터 목록은 런타임 계약에서 읽어 실제 `-encoders`, `-filters` 출력으로 확인한다. 런처는
uv나 FFmpeg를 자동 다운로드하지 않는다. 누락된 uv는 불완전한 설치로 분류한다.

## 구성, 복구와 실행 프로세스

구성과 복구는 확인 대화상자에서 네트워크·디스크 변경을 설명하고 사용자가 승인한 뒤에만
시작한다. 구성 명령의 의미는 다음과 같다.

```text
uv --managed-python sync --locked --no-dev
```

복구는 기존 `.venv`를 앱 루트 안의 고유한 임시 이름으로 먼저 이동한다. 새 환경 구성과 GUI
`--check`가 모두 성공해야 이전 생성 환경을 제거한다. 실패하면 불완전한 새 환경을 지우고 이전
환경을 원래 이름으로 복원한다. 강제 종료로 PowerShell의 예외 정리가 실행되지 않는 경우를 위해
변경 전 트랜잭션 표식을 기록한다. 런처는 취소한 자식 트리를 종료한 직후 복구 전용 호출을 실행해
이전 환경을 복원하거나 최초 구성의 불완전한 `.venv`를 제거한다.

정상 실행 명령의 고정 prefix는 다음과 같다.

```text
uv --managed-python run --locked --no-sync -- movie-maker
```

런처 옵션은 첫 `--` 전까지만 소비한다. 이후 값은 빈 문자열, 공백, 유니코드, 따옴표와 후행
백슬래시를 포함한 원래 argv 단위와 순서로 prefix 뒤에 붙인다. 각 인자는 Microsoft Windows
명령행 역슬래시·따옴표 규칙으로 개별 인코딩하고 `CreateProcessW`의 application name과 command
line에 분리해 전달한다. `cmd.exe`, 셸 확장과 문자열 이어 붙이기는 사용하지 않는다.

파이프는 stdout과 stderr를 분리해 제한된 크기만 보관하되 제한 이후에도 끝까지 drain한다.
구성·복구 프로세스는 kill-on-close Job Object에 넣는다. 취소, 시간 초과, 창 닫기 또는 예외에서
job을 종료해 하위 프로세스와 핸들을 정리하고 별도 복구 프로세스를 완료한 뒤 결과를 표시한다.
편집기 시작은 3초 안에 종료하면 종료 코드를 가진 `시작 직후 실패`, 계속 실행하면 `정상 시작`으로
구분하고 job을 인계한다.

Python 앱은 `--project PATH`를 받아 창을 표시하기 전에 `ProjectFileStore` 경로를 실제 세션에
연다. 읽기 실패는 종료 코드 2로 즉시 반환한다. `--online`은 앱 속성만 설정하며 환경 설치나
업데이트를 허용하지 않는다.

## 런처 UI와 접근성

런처는 상태 제목과 기호, 구성 요소 표, 복사 가능한 읽기 전용 진단, FFmpeg 폴더, 프로젝트,
온라인 모드, 점검·구성/복구·실행·취소·로그·유지관리 버튼을 제공한다. 색상만으로 상태를
구분하지 않는다. 비활성 실행 버튼 위에는 실행할 수 없는 이유가 항상 텍스트로 보인다.

모든 컨트롤은 생성 순서의 Tab 탐색과 `&` 접근 키를 가진다. 실행은 기본 버튼이고 확인
대화상자의 기본은 변경을 시작하지 않는 `아니요`다. 진단과 경로는 표준 edit/list view라 선택과
복사가 가능하다. 사용자 정의 색을 강제하지 않아 Windows 고대비 시스템 색을 유지하고,
Per-Monitor V2 DPI 인식 manifest와 최소 720×600 크기로 고배율에서 내용을 다시 배치한다.

## 진단 로그와 개인정보 경계

로그 위치는 Python `platformdirs`와 같은
`%LOCALAPPDATA%\OpenAI\MovieMakerReproduction\Logs`다. 한 줄에는 로컬 시각, 단계, 확인한 실행
파일, 버전, 종료 코드와 제한된 stdout/stderr만 기록한다. 전체 환경 변수와 전달한 프로젝트
argv는 기록하지 않으며 사용자 프로필 경로는 `%USERPROFILE%`로 바꾼다. `password`, `token`,
`secret`, `apikey`, `api_key` 값은 대소문자와 관계없이 `<redacted>`로 가린다.

프로세스 출력은 기본 8 KiB, 로그 전체는 10 MiB, 보존 기간은 14일이다. 정리는 정확한 로그
디렉터리의 `launcher-*.log`만 대상으로 오래된 파일부터 수행한다. 디렉터리 생성이나 쓰기가
실패해도 점검·실행은 계속하며 화면의 복사 가능한 진단이 대체 수단이다.

## 설치, 업데이트와 제거 트랜잭션

`MovieMakerSetup.exe`는 버전, 변경 내용, payload 크기, 사용 가능 공간과 서명 상태를 먼저
보인다. 설치·업데이트·제거는 각각 사용자의 확인 뒤 구조화된 인자로
`install-lifecycle.ps1`을 숨김 실행한다.

설치 상태는 `%LOCALAPPDATA%\MovieMakerReproduction\Installer\install-state.json`에 원자적으로
기록한다. 다음 소유권을 포함한다.

- 설치 파일의 상대 경로와 SHA-256
- 만든 바로가기 경로와 SHA-256
- 파일 연결 레지스트리 값의 설치 전 존재 여부·값·종류와 설치 값
- 사용자 PATH 전체 이전 값, 설치 값과 추가한 제품 디렉터리
- 설치 옵션, 제품 식별자·버전·절대 제품 디렉터리와 서명 상태

설치 전 `payload-manifest.json`의 모든 상대 경로가 payload 안에 있는지와 SHA-256을 검증한다.
업데이트는 새 payload를 비공개 sibling 디렉터리에 stage한 뒤 기존 제품 디렉터리를 backup으로
이동하고 새 디렉터리를 게시한다. 실패 주입을 포함한 어느 단계든 실패하면 새 디렉터리를 제거하고
기존 디렉터리를 복원한다. 사용자가 제품 디렉터리에 추가하거나 변경한 비소유 파일은 새 제품에
보존하고 충돌하면 installer state의 `PreservedFiles`로 옮긴다. 다운그레이드는 프로젝트 스키마
위험 때문에 거부한다. 프로세스 강제 종료가 예외 처리를 건너뛰어도 설치 관리자가 취소 직후
복구 전용 호출을 실행해 소유 경계 안의 sibling stage/backup을 판별하고 기존 설치를 되돌린다.
권한, 사용 중 파일과 디스크 부족은 구분된 오류 표식과 다음 행동을 출력한다. 사용자 PATH 변경은
Windows 재시작을 요구하지 않으며 이미 열린 터미널만 다시 열도록 안내한다.

시작 메뉴, 선택적 바탕 화면, `.mmrproj` 연결과 선택적 사용자 PATH는 현재 사용자 범위만 쓴다.
파일 연결 명령은 다음 의미를 가진다.

```text
"MovieMakerLauncher.exe" -- --project "%1"
```

제거는 현재 값/해시가 설치 값과 같을 때만 이전 값을 복원하거나 항목을 삭제한다. 사용자가 이후
바꾼 레지스트리, PATH의 다른 항목, 바로가기와 제품 파일은 보존한다. 제품 파일도 상태에 기록된
해시와 같은 것만 개별 삭제한 뒤 빈 디렉터리만 지우므로 `.mmrproj`, 원본 미디어와 알 수 없는
파일을 재귀 삭제하지 않는다.

캐시, 로그와 자동 저장 삭제는 각각 별도 선택이며 실제 platformdirs 경로와 영향을 확인
대화상자에서 설명한다. 최근 프로젝트, 세션 기록과 사용자 설정은 이 선택으로도 삭제하지 않는다.

## 코드 서명과 릴리스 검증

인증서 thumbprint를 지정하지 않으면 빌드는 서명을 만들지 않고 `SIGNING-STATUS.txt`에
`unsigned`를 쓴다. 인증서를 지정하면 Windows SDK `signtool`로 SHA-256 digest와 RFC 3161
timestamp를 적용하고 `/pa /all` 검증이 성공해야 패키징한다. 가짜 인증서나 서명 표시를 만들지
않는다. ZIP 옆에는 전체 archive SHA-256을 기록한다.

## 자동 검증 근거

- `LauncherContractTests.exe`: Windows argv 왕복, 첫 `--`, 빈/유니코드/따옴표/후행 역슬래시,
  stdout/stderr·종료 코드, 취소, 시간 초과와 하위 프로세스 종료, 출력 제한, 즉시 실패/정상 시작,
  로그 redaction·쓰기 실패·보존 기간·용량 정리
- `tests/launcher/test_runtime_inspection.py`: 필수 파일 누락, 구버전·내부 우선 uv, `.venv`
  누락·손상, FFmpeg 경로 우선순위·쌍 불일치·필터 누락, 실패·중단된 환경 복구
- `tests/launcher/test_windows_lifecycle.py`: 공백·한글 경로의 설치/업데이트/예외·강제 중단
  롤백/제거, payload 변조 거부, 실제 배포물 왕복, 바로가기·파일 연결·PATH와 사용자 변경·프로젝트·
  원본 보존
- `tests/test_command_line.py`: `--project`와 `--online`, 지정 프로젝트를 창 표시 전에 여는 앱
  세션
- `tests/launcher/test_native_artifacts.py`: 두 x64 GUI PE의 import table에 CLR, Visual C++
  재배포 패키지 또는 MinGW 런타임 DLL 의존성이 없는지 확인
- `build-windows.ps1` 연속 실행: 네이티브 테스트, payload hash, ZIP SHA-256 재현성

실제 인증서 서명과 Windows 고대비/125~200% 배율 시각 확인은 해당 환경이 제공되는 릴리스
게이트에서 수행한다. 인증서가 없을 때는 미서명 상태 확인이 합격 조건이다.
