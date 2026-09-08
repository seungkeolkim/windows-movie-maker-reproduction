# 프로젝트 문서 안내

이 파일은 프로젝트 문서의 진입점이다. 오랜만에 프로젝트에 돌아왔거나 특정 결정을 찾아야
할 때 파일 트리를 추측하지 말고 이 안내에서 읽기 순서와 목적별 경로를 먼저 확인한다.

## 처음 읽는 순서

| 순서 | 문서 | 무엇을 이해하는가 |
| --- | --- | --- |
| 1 | [프로젝트 README](../README.md) | 제품 목표, 현재 개발 상태, 실행 방법과 전체 로드맵 |
| 2 | [PRODUCT-0001: UI 설계 기준](product/product-0001-ui-design-baseline.md) | 주요 사용자, 제품 원칙, 대표 시나리오와 MVP·1.0 경계 |
| 3 | [PRODUCT-0002: 제품 기능 인벤토리](product/product-0002-feature-inventory.md) | 전체 기능, 기능별 우선순위, 관련 시나리오와 주요 제약 |
| 4 | [WORKFLOW-0001: 기능 정의 및 UI 목업 작업 방식](workflows/workflow-0001-feature-ui-mockup.md) | 기능 목록부터 화면·동작 명세, 목업 검토와 실제 로직 연결까지의 절차 |
| 5 | [DESIGN-0001: 화면 목록과 레이아웃](design/design-0001-screen-layout.md) | MVP·1.0 화면, 패널 관계와 반응형 배치 |
| 6 | [DESIGN-0002: 화면 요소와 상세 기능](design/design-0002-element-functions.md) | 각 요소의 입력, 활성 조건, 결과와 단축키 |
| 7 | [MOCK-0001: 목업 동작 및 상태 명세](mock/mock-0001-behavior-specification.md) | 샘플 데이터, 정상·빈·오류 상태와 전이 규칙 |
| 8 | [MOCK-0002: 인터랙티브 목업 검증](mock/mock-0002-validation-scenarios.md) | 목업 실행법, MVP·1.0 검증 절차와 합격 기준 |
| 9 | [MOCK-0003: 검토 기록과 UI 기준선](mock/mock-0003-review-log.md) | 승인된 기준선, 반영 이슈와 실제 로직 연결 순서 |
| 10 | [ADR-0001: Python, uv 및 네이티브 런처](decisions/adr-0001-python-uv-native-launcher.md) | 현재 기술 스택과 실행 환경을 선택한 이유 및 제약 |
| 11 | [ADR-0002: 명령 기반 편집과 세션 행동 이력](decisions/adr-0002-command-based-edit-history.md) | MVP 편집 명령 구조와 1.0 실행 취소·다시 실행의 기반 |
| 12 | [ADR-0003: 프로젝트 코어 상태, 시간 단위와 저장 경계](decisions/adr-0003-project-core-state-and-storage.md) | W-01 시간·불변 모델·명령 원자성과 DB 미사용 결정 |
| 13 | [ADR-0004: 파일 기반 경량 데이터베이스 단일화](decisions/adr-0004-single-embedded-database.md) | SQLite·DuckDB 병행 금지와 외부 서버 DB의 예외 승인 기준 |
| 14 | [DESIGN-0003: 프로젝트 코어 계약](design/design-0003-project-core-contract.md) | 프로젝트 스키마 초안, 모델 불변식과 명령 프로토콜 |
| 15 | [DESIGN-0004: 실제 미디어 분석과 보관함 계약](design/design-0004-media-library-contract.md) | W-02 ffprobe 분석, 부분 성공, 중복·썸네일·제거 안전 경계 |
| 16 | [DESIGN-0005: 프로젝트 저장·열기 계약](design/design-0005-project-persistence-contract.md) | W-03 JSON 스키마, 원자적 저장, 검증 후 세션 교체와 Qt 흐름 |
| 17 | [DESIGN-0006: 타임라인 편집 명령 계약](design/design-0006-timeline-editing-contract.md) | W-04 클립 편집, 경계 스냅, 리플과 실제 명령·UI 연결 |
| 18 | [DESIGN-0007: 미리 보기 시간축과 디코딩 계약](design/design-0007-preview-playback-contract.md) | W-05 시간 변환, 실제 프레임 디코딩, 동시성·취소와 오류 경계 |
| 19 | [POLICY-0001: 라이선스와 출력물 권리](policies/policy-0001-licensing-and-output-rights.md) | 소프트웨어·코덱·기본 자산과 사용자 출력물 권리의 경계 |

제품 방향만 파악할 때는 1~4번을 읽는다. 화면을 변경하려면 DESIGN과 MOCK 문서까지, 실제
로직을 연결하려면 MOCK-0003의 연결 순서와 관련 ADR까지 읽는다. 정책 문서는 미디어, 출력,
배포 또는 기본 자산을 구현할 때 확인한다.

## 목적별로 찾기

| 알고 싶은 내용 또는 작업 | 먼저 볼 문서 | 함께 볼 문서 |
| --- | --- | --- |
| 프로젝트가 무엇이고 현재 어디까지 구현됐는가 | [프로젝트 README](../README.md) | [PRODUCT-0001](product/product-0001-ui-design-baseline.md) |
| 0단계에서 확정한 사용자·시나리오·범위는 무엇인가 | [PRODUCT-0001](product/product-0001-ui-design-baseline.md) | [WORKFLOW-0001](workflows/workflow-0001-feature-ui-mockup.md) |
| 어떤 기능을 언제 제공하며 주요 제약은 무엇인가 | [PRODUCT-0002](product/product-0002-feature-inventory.md) | [PRODUCT-0001](product/product-0001-ui-design-baseline.md) |
| 기능 목록과 화면 목업을 어떤 순서로 만드는가 | [WORKFLOW-0001](workflows/workflow-0001-feature-ui-mockup.md) | [PRODUCT-0002](product/product-0002-feature-inventory.md) |
| 어떤 화면과 패널이 필요하고 창 크기에 따라 어떻게 배치되는가 | [DESIGN-0001](design/design-0001-screen-layout.md) | [PRODUCT-0002](product/product-0002-feature-inventory.md) |
| 버튼·필드·단축키의 조건과 결과는 무엇인가 | [DESIGN-0002](design/design-0002-element-functions.md) | [DESIGN-0001](design/design-0001-screen-layout.md) |
| 목업의 샘플 데이터와 상태 전이 규칙은 무엇인가 | [MOCK-0001](mock/mock-0001-behavior-specification.md) | [DESIGN-0002](design/design-0002-element-functions.md) |
| 목업을 어떻게 실행하고 어떤 순서로 검증하는가 | [MOCK-0002](mock/mock-0002-validation-scenarios.md) | [MOCK-0001](mock/mock-0001-behavior-specification.md) |
| 확정된 UI 기준선과 실제 로직 연결 순서는 무엇인가 | [MOCK-0003](mock/mock-0003-review-log.md) | [ADR-0002](decisions/adr-0002-command-based-edit-history.md) |
| Python, PySide6, uv, FFmpeg와 런처를 왜 사용하는가 | [ADR-0001](decisions/adr-0001-python-uv-native-launcher.md) | [프로젝트 README](../README.md) |
| 실행 취소·다시 실행을 위해 MVP부터 어떤 편집 구조를 사용하는가 | [ADR-0002](decisions/adr-0002-command-based-edit-history.md) | [PRODUCT-0002](product/product-0002-feature-inventory.md) |
| 프로젝트 시간, 클립 모델과 파일 스키마는 어떻게 표현하는가 | [DESIGN-0003](design/design-0003-project-core-contract.md) | [ADR-0003](decisions/adr-0003-project-core-state-and-storage.md) |
| 프로젝트 상태에 데이터베이스를 사용하는가 | [ADR-0003](decisions/adr-0003-project-core-state-and-storage.md) | [DESIGN-0003](design/design-0003-project-core-contract.md) |
| 파일 기반 DB를 몇 개까지 사용하고 언제 서버 DB를 검토하는가 | [ADR-0004](decisions/adr-0004-single-embedded-database.md) | [ADR-0003](decisions/adr-0003-project-core-state-and-storage.md) |
| 실제 미디어를 어떻게 분석하고 안전하게 보관함에 추가하는가 | [DESIGN-0004](design/design-0004-media-library-contract.md) | [DESIGN-0003](design/design-0003-project-core-contract.md) |
| 프로젝트를 어떤 형식과 안전 경계로 저장하고 여는가 | [DESIGN-0005](design/design-0005-project-persistence-contract.md) | [ADR-0003](decisions/adr-0003-project-core-state-and-storage.md) |
| 타임라인 클립을 어떤 시간·리플·명령 규칙으로 편집하는가 | [DESIGN-0006](design/design-0006-timeline-editing-contract.md) | [ADR-0002](decisions/adr-0002-command-based-edit-history.md) |
| 프로젝트 위치를 실제 영상·사진 프레임으로 어떻게 재생하는가 | [DESIGN-0007](design/design-0007-preview-playback-contract.md) | [ADR-0003](decisions/adr-0003-project-core-state-and-storage.md) |
| 개발 및 실행 명령은 무엇인가 | [프로젝트 README](../README.md) | [스크립트 안내](../scripts/README.md) |
| 출력 영상, FFmpeg, 코덱과 기본 자산의 권리 범위는 무엇인가 | [POLICY-0001](policies/policy-0001-licensing-and-output-rights.md) | [프로젝트 README](../README.md) |
| 문서 파일의 이름과 위치를 어떻게 정하는가 | 이 문서 | [WORKFLOW-0001](workflows/workflow-0001-feature-ui-mockup.md) |

## 0~6단계 산출물 지도

아래 표는 UI 목업 작업 단계와 승인된 결과물을 연결한다.

| 단계 | 산출물 | 상태 |
| --- | --- | --- |
| 0. 기준 정의 | [PRODUCT-0001](product/product-0001-ui-design-baseline.md) | 승인됨, 완료 |
| 1. 기능 인벤토리 | [PRODUCT-0002](product/product-0002-feature-inventory.md) | 승인됨, 완료 |
| 2. 화면 및 레이아웃 | [DESIGN-0001](design/design-0001-screen-layout.md) | 승인됨, 완료 |
| 3. 요소별 상세 기능 | [DESIGN-0002](design/design-0002-element-functions.md) | 승인됨, 완료 |
| 4. 목업 동작 명세 | [MOCK-0001](mock/mock-0001-behavior-specification.md) | 승인됨, 완료 |
| 5. 인터랙티브 목업 | [UI 소스](../src/movie_maker/ui/)와 [MOCK-0002](mock/mock-0002-validation-scenarios.md) | 승인됨, 완료 |
| 6. 검토와 기준선 | [MOCK-0003](mock/mock-0003-review-log.md) | 승인됨, 완료 |

단계의 상세 작업과 완료 조건은 [WORKFLOW-0001](workflows/workflow-0001-feature-ui-mockup.md)을
기준으로 한다. 기준선을 변경하면 관련 문서, 목업과 검증 시나리오를 같은 변경에서 갱신한다.

## 실제 로직 연결 현황

| 작업 | 상태 | 구현 및 계약 |
| --- | --- | --- |
| `W-01` 프로젝트 코어 | 완료 | [DESIGN-0003](design/design-0003-project-core-contract.md), [프로젝트 코어 소스](../src/movie_maker/project/), [코어 테스트](../tests/project/) |
| `W-02` 미디어 선택·분석·보관함 | 완료 | [DESIGN-0004](design/design-0004-media-library-contract.md), [미디어 소스](../src/movie_maker/media/), [미디어 테스트](../tests/media/) |
| `W-03` 프로젝트 새로 만들기·저장·열기 | 완료 | [DESIGN-0005](design/design-0005-project-persistence-contract.md), [저장 소스](../src/movie_maker/project/persistence.py), [저장 테스트](../tests/project/test_persistence.py) |
| `W-04` 타임라인 편집 명령 | 완료 | [DESIGN-0006](design/design-0006-timeline-editing-contract.md), [타임라인 소스](../src/movie_maker/timeline/), [타임라인 테스트](../tests/timeline/) |
| `W-05` 미리 보기 시간축과 디코딩 | 완료 | [DESIGN-0007](design/design-0007-preview-playback-contract.md), [미리 보기 소스](../src/movie_maker/preview/), [미리 보기 테스트](../tests/preview/) |
| `W-06` 원본음·음악 믹싱 | 다음 작업 | [MOCK-0003 연결 순서](mock/mock-0003-review-log.md#실제-로직-연결-순서) |

W-06 이후 작업은 선행 서비스 결과에 따라 시작하며 전체 순서와 검증 기준은 MOCK-0003을
따른다. W-05까지 DB와 새 JSON 필드 없이 단일 JSON 프로젝트, 세션 메모리 썸네일·프레임
캐시를 사용한다.

## 카테고리와 파일명 규칙

일반 문서 파일명은 다음 형식을 사용한다.

```text
<category>-<index>-<topic>.md
```

예를 들면 `product-0001-ui-design-baseline.md`에서 `product`는 카테고리, `0001`은 해당
카테고리 안의 고정 순번, 나머지는 문서 주제를 뜻한다.

| 폴더 | 파일명 접두사 | 내용 |
| --- | --- | --- |
| `product/` | `product-0001-...` | 사용자, 범위, 시나리오와 기능 인벤토리 |
| `workflows/` | `workflow-0001-...` | 반복해서 적용할 작업 절차와 완료 기준 |
| `design/` | `design-0001-...` | 화면, 레이아웃, 요소와 상호작용 설계 |
| `mock/` | `mock-0001-...` | 목업 동작, 검증 시나리오와 검토 기록 |
| `decisions/` | `adr-0001-...` | 장기적인 아키텍처 결정과 대안 |
| `policies/` | `policy-0001-...` | 라이선스, 품질, 보안 등 지속적인 정책 |

다음 규칙을 함께 적용한다.

- 순번은 카테고리별로 독립적으로 증가하고 삭제된 번호를 재사용하지 않는다.
- 숫자는 읽기 순서가 아니라 문서의 안정적인 식별자다. 읽기 순서는 이 안내가 정의한다.
- 파일명은 소문자 영문과 하이픈을 사용하고 제목은 문서 안에서 한국어로 명확히 쓴다.
- 문서 상단에는 문서 번호와 `초안`, `검토 중`, `승인됨`, `대체됨` 중 현재 상태를 기록한다.
- 다른 문서를 먼저 이해해야 한다면 상단이나 도입부에서 직접 링크한다.
- 문서를 추가, 이동 또는 대체할 때 이 안내와 모든 내부 링크를 같은 변경에서 갱신한다.
- `README.md`, `AGENTS.md`와 디렉터리별 `README.md`는 도구와 관례가 기대하는 진입점이므로
  파일명 규칙의 예외로 둔다.

## 현재 문서 목록

| 번호 | 상태 | 문서 |
| --- | --- | --- |
| `PRODUCT-0001` | 승인됨 | [UI 설계 기준](product/product-0001-ui-design-baseline.md) |
| `PRODUCT-0002` | 승인됨 | [제품 기능 인벤토리](product/product-0002-feature-inventory.md) |
| `WORKFLOW-0001` | 승인됨 | [기능 정의 및 UI 목업 작업 방식](workflows/workflow-0001-feature-ui-mockup.md) |
| `DESIGN-0001` | 승인됨 | [화면 목록과 레이아웃](design/design-0001-screen-layout.md) |
| `DESIGN-0002` | 승인됨 | [화면 요소와 상세 기능](design/design-0002-element-functions.md) |
| `DESIGN-0003` | 승인됨 | [프로젝트 코어 계약](design/design-0003-project-core-contract.md) |
| `DESIGN-0004` | 승인됨 | [실제 미디어 분석과 보관함 계약](design/design-0004-media-library-contract.md) |
| `DESIGN-0005` | 승인됨 | [프로젝트 저장·열기 계약](design/design-0005-project-persistence-contract.md) |
| `DESIGN-0006` | 승인됨 | [타임라인 편집 명령 계약](design/design-0006-timeline-editing-contract.md) |
| `DESIGN-0007` | 승인됨 | [미리 보기 시간축과 디코딩 계약](design/design-0007-preview-playback-contract.md) |
| `MOCK-0001` | 승인됨 | [목업 동작 및 상태 명세](mock/mock-0001-behavior-specification.md) |
| `MOCK-0002` | 승인됨 | [인터랙티브 목업 검증 시나리오](mock/mock-0002-validation-scenarios.md) |
| `MOCK-0003` | 승인됨 | [목업 검토 기록과 UI 기준선](mock/mock-0003-review-log.md) |
| `ADR-0001` | 승인됨 | [Python, uv 및 네이티브 런처](decisions/adr-0001-python-uv-native-launcher.md) |
| `ADR-0002` | 승인됨 | [명령 기반 편집과 세션 행동 이력](decisions/adr-0002-command-based-edit-history.md) |
| `ADR-0003` | 승인됨 | [프로젝트 코어 상태, 시간 단위와 저장 경계](decisions/adr-0003-project-core-state-and-storage.md) |
| `ADR-0004` | 승인됨 | [파일 기반 경량 데이터베이스 단일화](decisions/adr-0004-single-embedded-database.md) |
| `POLICY-0001` | 초안 | [라이선스와 출력물 권리](policies/policy-0001-licensing-and-output-rights.md) |

아키텍처 결정을 변경할 때 기존 ADR을 조용히 덮어쓰지 않는다. 새 ADR을 추가하고 이전 결정의
상태와 대체 관계를 표시한다. 다른 승인 문서를 의미 있게 대체할 때도 기존 문서의 상태와
후속 문서 링크를 남긴다.
