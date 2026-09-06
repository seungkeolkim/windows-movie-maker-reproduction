# 프로젝트 문서 안내

이 파일은 프로젝트 문서의 진입점이다. 오랜만에 프로젝트에 돌아왔거나 특정 결정을 찾아야
할 때 파일 트리를 추측하지 말고 이 안내에서 읽기 순서와 목적별 경로를 먼저 확인한다.

## 처음 읽는 순서

| 순서 | 문서 | 무엇을 이해하는가 |
| --- | --- | --- |
| 1 | [프로젝트 README](../README.md) | 제품 목표, 현재 개발 상태, 실행 방법과 전체 로드맵 |
| 2 | [PRODUCT-0001: UI 설계 기준](product/product-0001-ui-design-baseline.md) | 주요 사용자, 제품 원칙, 대표 시나리오와 MVP·1.0 경계 |
| 3 | [WORKFLOW-0001: 기능 정의 및 UI 목업 작업 방식](workflows/workflow-0001-feature-ui-mockup.md) | 기능 목록부터 화면·동작 명세, 목업 검토와 실제 로직 연결까지의 절차 |
| 4 | [ADR-0001: Python, uv 및 네이티브 런처](decisions/adr-0001-python-uv-native-launcher.md) | 현재 기술 스택과 실행 환경을 선택한 이유 및 제약 |
| 5 | [POLICY-0001: 라이선스와 출력물 권리](policies/policy-0001-licensing-and-output-rights.md) | 소프트웨어·코덱·기본 자산과 사용자 출력물 권리의 경계 |

2번과 3번은 현재 UI 목업 설계에 참여할 때 필수다. ADR과 정책 문서는 해당 영역을
변경하거나 구현할 때 읽고, 단순히 현재 제품 방향을 파악할 때는 필요 부분만 확인한다.

## 목적별로 찾기

| 알고 싶은 내용 또는 작업 | 먼저 볼 문서 | 함께 볼 문서 |
| --- | --- | --- |
| 프로젝트가 무엇이고 현재 어디까지 구현됐는가 | [프로젝트 README](../README.md) | [PRODUCT-0001](product/product-0001-ui-design-baseline.md) |
| 0단계에서 합의 중인 사용자·시나리오·범위는 무엇인가 | [PRODUCT-0001](product/product-0001-ui-design-baseline.md) | [WORKFLOW-0001](workflows/workflow-0001-feature-ui-mockup.md) |
| 기능 목록과 화면 목업을 어떤 순서로 만드는가 | [WORKFLOW-0001](workflows/workflow-0001-feature-ui-mockup.md) | [PRODUCT-0001](product/product-0001-ui-design-baseline.md) |
| Python, PySide6, uv, FFmpeg와 런처를 왜 사용하는가 | [ADR-0001](decisions/adr-0001-python-uv-native-launcher.md) | [프로젝트 README](../README.md) |
| 개발 및 실행 명령은 무엇인가 | [프로젝트 README](../README.md) | [스크립트 안내](../scripts/README.md) |
| 출력 영상, FFmpeg, 코덱과 기본 자산의 권리 범위는 무엇인가 | [POLICY-0001](policies/policy-0001-licensing-and-output-rights.md) | [프로젝트 README](../README.md) |
| 문서 파일의 이름과 위치를 어떻게 정하는가 | 이 문서 | [WORKFLOW-0001](workflows/workflow-0001-feature-ui-mockup.md) |

## 0~6단계 산출물 지도

아래 표는 UI 목업 작업 단계와 현재 또는 예정된 문서 위치를 연결한다. `예정` 항목은 실제
작업에 착수할 때 생성하며 빈 파일을 미리 만들지 않는다.

| 단계 | 산출물 | 상태 |
| --- | --- | --- |
| 0. 기준 정의 | [PRODUCT-0001](product/product-0001-ui-design-baseline.md) | 초안, 범위 기준 검토 필요 |
| 1. 기능 인벤토리 | `product/product-0002-feature-inventory.md` | 예정 |
| 2. 화면 및 레이아웃 | `design/design-0001-screen-layout.md` | 예정 |
| 3. 요소별 상세 기능 | `design/design-0002-element-functions.md` | 예정 |
| 4. 목업 동작 명세 | `mock/mock-0001-behavior-specification.md` | 예정 |
| 5. 인터랙티브 목업 | UI 소스와 `mock/mock-0002-validation-scenarios.md` | 예정 |
| 6. 검토와 기준선 | `mock/mock-0003-review-log.md` | 예정 |

단계의 상세 작업과 완료 조건은 [WORKFLOW-0001](workflows/workflow-0001-feature-ui-mockup.md)을
기준으로 한다. 예정 파일의 이름이나 분리 단위가 달라지면 이 표를 같은 변경에서 갱신한다.

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
| `PRODUCT-0001` | 초안 | [UI 설계 기준](product/product-0001-ui-design-baseline.md) |
| `WORKFLOW-0001` | 승인됨 | [기능 정의 및 UI 목업 작업 방식](workflows/workflow-0001-feature-ui-mockup.md) |
| `ADR-0001` | 승인됨 | [Python, uv 및 네이티브 런처](decisions/adr-0001-python-uv-native-launcher.md) |
| `POLICY-0001` | 초안 | [라이선스와 출력물 권리](policies/policy-0001-licensing-and-output-rights.md) |

아키텍처 결정을 변경할 때 기존 ADR을 조용히 덮어쓰지 않는다. 새 ADR을 추가하고 이전 결정의
상태와 대체 관계를 표시한다. 다른 승인 문서를 의미 있게 대체할 때도 기존 문서의 상태와
후속 문서 링크를 남긴다.
