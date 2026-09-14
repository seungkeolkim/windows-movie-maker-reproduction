# QWEN.md — Windows Movie Maker Reproduction

사라진 Windows Movie Maker의 간결한 편집 경험을 재구현하는 데스크톱 영상 편집기입니다.
"영상 두 개를 불러와 자르고 이어 붙인 뒤 MP4로 내보내기"를 안정적으로 달성한 뒤
나머지 Movie Maker 경험을 단계적으로 쌓는 것이 목표입니다.

## 핵심 제약(절대 준수)

- **subagent를 절대 실행하지 마세요.** 현재 사용 중인 local LLM의 parallel은 1이므로 병렬 agent
  작업을 해서는 안 됩니다. 모든 탐색·구현·검증 작업을 메인 세션에서 순차적으로 수행합니다.

## 프로젝트 개요

- **제품:** Windows Movie Maker(Windows Essentials 2012)의 핵심 사용 경험을 새로 구현하는
  비공식 독립 프로젝트입니다. 전문 NLE가 아니라 초보자가 쉽게 배우고 빠르게 완결하는 편집기에 집중합니다.
- **기술 스택:** Python 3.13 + PySide6(Qt Widgets) UI, FFmpeg/ffprobe로 미디어 분석·디코딩·렌더링.
  저장소에는 Python 런타임이 없으며 `uv`가 관리형 CPython(3.13.14)과 `.venv`를 구성합니다.
- **편집 모델:** 모든 편집은 원본 파일을 건드리지 않는 **비파괴 명령**으로 기록됩니다.
  프로젝트 파일은 `schema_version`이 있는 UTF-8 JSON으로 원자적으로 저장되며,
  불변 프로젝트 값 + 명령 실행 경계(ADR-0002)가 undo/redo와 복구의 기반입니다.
- **고정 트랙 구조:** 비디오/사진, 음악, 내레이션, 텍스트 레이어, 전환(인접 장면 사이만).
  무제한 영상 레이어는 1.0 범위에 포함하지 않습니다.
- **현재 진행 상황:** W-01~W-09 완료(프로젝트 코어, 미디어 보관함, 저장/열기, 타임라인 편집,
  미리 보기, 원본음·음악 믹싱, MP4 출력, 고급 타임라인, 창작 기능). 다음 작업은 W-10
  (복구·백그라운드 작업)이고, 이후 W-11(Windows 런처·설치)을 진행합니다. 명세는 `tasks/`에
  있습니다.

## 실행 환경 구성

요건: 64비트 Windows 또는 Linux, `uv` 0.12.1 이상, 같은 배포본의 FFmpeg+ffprobe
(`libx264`/`aac` 인코더 필수). FFmpeg는 Python 패키지가 아니라 uv가 설치하지 않습니다.

```powershell
# Windows — 실행 환경 구성(개발 의존성은 -Dev)
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\environment\setup.ps1 `
  -FFmpegDirectory "C:\Tools\ffmpeg\bin"
```

```bash
# Linux — --dev 추가 시 개발 의존성까지 구성
bash ./scripts/environment/setup.sh --ffmpeg-dir /opt/ffmpeg/bin
```

FFmpeg 탐색 순서: 스크립트 인자 → `MOVIE_MAKER_FFMPEG_DIR` 환경 변수 →
`tools/ffmpeg/bin` → `PATH`. 저장소는 FFmpeg를 자동 다운로드하거나 시스템 PATH를 수정하지 않습니다.

## 주요 명령

| 목적 | 명령 |
| --- | --- |
| 앱 실행(Windows) | `.\scripts\environment\run.ps1 -FFmpegDirectory "C:\Tools\ffmpeg\bin"` |
| 앱 실행(Linux) | `bash ./scripts/environment/run.sh` |
| 런타임 점검만(창 없이) | 위 명령 끝에 `-- --check` |
| 스크립트 없이 직접 실행 | `uv --managed-python run --locked --no-sync -- movie-maker` |
| 테스트 | `uv --managed-python run --locked --no-sync -- pytest` |
| 린트 | `uv --managed-python run --locked --no-sync -- ruff check .` |
| 타입 검사 | `uv --managed-python run --locked --no-sync -- mypy` |
| 문서 공백 검사 | `git diff --check` |

`--locked --no-sync`는 실행 중 잠금 파일 변경과 암묵적 패키지 설치를 막습니다.
의존성을 바꾼 개발자만 `uv lock`으로 `uv.lock`을 갱신합니다.

## 디렉터리 구조

```text
src/movie_maker/        # 애플리케이션 코드
├── project/            # W-01 시간·클립 모델, W-03 원자적 JSON 저장(persistence.py)
├── media/              # W-02 ffprobe 분석, 썸네일, 보관함
├── timeline/           # W-04 편집 명령, W-08 advanced.py(복제·다중 선택·그룹)
├── preview/            # W-05 실제 시간축 재생, FFmpeg 디코딩
├── audio/              # W-06 원본음·음악 믹싱, 48kHz 스테레오 공통 그래프
├── exporting/          # W-07 H.264/AAC MP4 렌더, 진행·취소·원자적 게시
├── creative/           # W-09 내레이션·텍스트·전환·시각 효과와 공통 합성
└── ui/                 # PySide6 메인 창, 다이얼로그, 미리 보기·오디오 UI
scripts/environment/    # setup/run 스크립트(Windows PowerShell + Linux Bash)
scripts/mock/           # 목업 기준 화면 캡처
tests/                  # 소스 패키지 구조를 거울처럼 반영(test_timeline_split.py 등)
docs/                   # 번호 매긴 문서 — 진입점은 docs/README.md
tasks/                  # W-06~W-11 독립 실행용 작업 명세(다음 작업: W-10)
codex_goals/            # 작업별 목표 파일(txt)
```

## 문서 체계

- 문서 파일명: `<카테고리>-<4자리 순번>-<주제>.md`(예: `design-0003-project-core-contract.md`).
  순번은 카테고리 안에서만 증가하고 삭제된 번호를 재사용하지 않습니다.
- 읽기 순서와 목적별 경로는 `docs/README.md`가 정의합니다. 화면을 변경할 때는
  DESIGN·MOCK 문서, 실제 로직 연결은 MOCK-0003과 관련 ADR까지 함께 봅니다.
- **ADR는 절대 조용히 덮어쓰지 않습니다.** 기존 결정을 바꿀 때 새 ADR을 추가하고
  이전 결정의 상태(대체됨)와 후속 링크를 남깁니다.
- 문서 본문은 한국어, 파일명은 소문자 영문+하이픈을 사용합니다.

## 아키텍처 제약(반드시 준수)

1. **저장 경계:** 프로젝트 파일은 버전 관리 JSON 문서입니다. SQLite와 DuckDB를 동시에 쓰거나
   기능별로 파일 기반 임베디드 DB를 여러 개 도입하지 않습니다(ADR-0004). 운영 데이터에
   임베디드 DB가 필요하면 SQLite를 기본으로 한 엔진만 선택합니다.
2. **시간 단위:** 프로젝트 시간은 **정수 나노초**, 프레임률과 재생 속도는 **유리수(Fraction)** 계약입니다.
3. **명령 경계:** 지속되는 편집은 불변 프로젝트 값과 명령 실행 경계(ADR-0002)를 거칩니다.
   세션 상태나 DB에 편집을 직접 쓰는 방식으로 우회하지 않습니다.
4. **외부 프로세스:** 셸 문자열이 아닌 **구조화된 인자 목록**으로 FFmpeg 등을 실행합니다.
5. **긴 작업:** UI 스레드를 막지 않고 진행률·취소·종료 정리를 제공합니다.
6. **원본 보존:** 원본 미디어를 수정·이동·삭제하지 않습니다.
7. **오류 표시:** 실패 원인과 다음 행동을 한국어로 표시하되 프로젝트와 정상 파일을 보존합니다.
8. **문서 정직성:** 완성된 기능만 문서에서 "지원"한다고 표시합니다.

## 코딩 스타일

- 4칸 들여쓰기, `snake_case`(모듈·함수·변수), `PascalCase`(클래스), `UPPER_SNAKE_CASE`(상수).
- Ruff: Python 3.13, 라인 길이 100. mypy: `src/` strict 모드.
- UI·타임라인·미디어·지속화 관심사는 각 패키지(`ui/`, `timeline/`, `media/`, `project/`)에 유지합니다.
- 컴포넌트 간에는 작고 타입화된 인터페이스를 선호합니다.
- 네이티브 런처(`launcher/`)는 Windows 네이티브 실행기 용도로만 예약해 둡니다.

## 테스트

- pytest + pytest-qt(PySide6 API 고정, `pyproject.toml` 참조), 테스트 대상은 `tests/`.
- 소스 패키지 구조를 거울처럼 반영하고 `test_<주제>.py` 형식(예: `test_timeline_split.py`).
- 우선순위: 프로젝트 파일 안전성, 프레임 정확도, FFmpeg 인자 구성, 런처 오류 처리.
- 새 동작은 집중 테스트 + 기존 전체 회귀 테스트로 검증합니다.

## 커밋 관례

- Conventional Commit 스타일: `type: concise imperative summary`
  (예: `feat: add media probe service`, `test: cover missing source recovery`).
- PR에는 변경 내용, 수행한 검증, 관련 로드맵 항목을 설명합니다.
- UI 변경은 스크린샷/영상, 호환성 변경은 샘플 미디어 세부 정보 포함.
- 승인된 아키텍처 결정을 수정하면 새 ADR을 함께 추가합니다.

## 작업 진행 방식

- 다음 작업은 `tasks/w-10-recovery-and-background-jobs.md`부터 시작하며, 각 명세 파일 전체가
  작업 목표입니다.
- 후속 작업은 선행 작업의 공개 계약을 재사용하며 선행 기능을 임시 구현으로 복제하지 않습니다.
- 요구사항 해석 시 제품 범위(`docs/product/`)와 승인된 ADR을 우선합니다.
