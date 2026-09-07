# 화면 요소와 상세 기능

- 문서 번호: `DESIGN-0002`
- 상태: 승인됨
- 단계: 기능 정의 및 UI 목업 작업 방식의 3단계
- 작성일: 2026-09-06
- 화면 기준: [DESIGN-0001: 화면 목록과 레이아웃](design-0001-screen-layout.md)
- 기능 기준:
  [PRODUCT-0002: 제품 기능 인벤토리](../product/product-0002-feature-inventory.md)
- 승인 기준선: [MOCK-0003의 UI-MOCK-BASELINE-1](../mock/mock-0003-review-log.md)

## 목적

이 문서는 각 화면 안에 표시할 요소, 요소가 제공하는 상세 기능, 활성 조건과 즉시 표시할
결과를 정의한다. 실제 데이터 처리나 FFmpeg 동작은 규정하지 않으며, 목업과 이후 UI가 같은
사용자 계약을 갖도록 하는 기준이다.

## 공통 상태 규칙

모든 조작 요소는 다음 상태를 구분한다.

| 상태 | 표현 및 동작 |
| --- | --- |
| 기본 | 현재 문맥에서 실행할 수 있으며 이름과 아이콘이 의미를 함께 전달한다. |
| 가리킴 | 모양 변화와 도구 설명으로 결과를 예고한다. |
| 키보드 포커스 | 배경과 독립적인 외곽선으로 현재 포커스를 표시한다. |
| 선택됨 | 포커스와 다른 지속적인 배경·테두리 및 접근성 선택 상태를 사용한다. |
| 비활성화 | 실행되지 않으며 도구 설명 또는 상태 도움말에서 필요한 조건을 알린다. |
| 진행 중 | 중복 실행을 막고 진행 상태와 취소 가능 여부를 연결한다. |
| 경고/오류 | 아이콘, 짧은 텍스트와 다음 행동을 함께 제공한다. |

버튼은 클릭 뒤 아무 변화 없이 남지 않는다. 화면 상태 변화, 상태 표시줄 메시지, 패널 전환,
대화상자 또는 알림 중 하나 이상으로 결과를 확인할 수 있어야 한다.

이 공통 상태와 입력 규칙은 `F-EXPERIENCE-02`~`05`, `F-EXPERIENCE-07`에 연결한다. 목업은
한국어 문구와 확장된 오류 메시지에서 잘림을 확인하고, 실제 1.0 배선에서는 사용자 문자열을
UI 구조와 분리해 같은 용어를 메뉴, 상태와 대화상자에서 재사용한다.

## E-WINDOW: 편집기 프레임

| 요소 | 범위 | 표시 내용과 상세 기능 | 주요 상태 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `E-WINDOW-TITLE` | MVP | 프로젝트 이름과 수정됨 `*` 표시 | 제목 없음, 저장됨, 수정됨 | `F-PROJECT-01`~`05`, `F-EXPERIENCE-01` |
| `E-WINDOW-MENU` | MVP/1.0 | 모든 기능으로 이동하는 파일·편집·클립·오디오·텍스트·효과·보기·도움말 메뉴 | 메뉴별 활성/비활성 | 모든 MVP/1.0 기능군 |
| `E-WINDOW-TOOLBAR` | MVP | 새로 만들기, 열기, 저장, 가져오기, 제거, 실행 취소·다시 실행, 분할, 삭제, 출력 | 선택 및 프로젝트 상태에 따라 변경 | `F-PROJECT`, `F-MEDIA`, `F-TIMELINE`, `F-EXPORT` |
| `E-WINDOW-STATUS` | MVP | 현재 도움말, 성공·오류 메시지, 프로젝트 길이와 백그라운드 작업 수 | 준비, 일시 메시지, 경고, 진행 | `F-EXPERIENCE-01`, `02` |
| `E-WINDOW-LIBRARY-TOGGLE` | MVP | 미디어 보관함을 접거나 다시 연다. | 펼침, 접힘 | `F-EXPERIENCE-01` |
| `E-WINDOW-INSPECTOR-TOGGLE` | MVP | 속성 패널을 접거나 다시 연다. | 펼침, 접힘 | `F-EXPERIENCE-01` |
| `E-EDITOR-WORKSPACE` | MVP | 미리 보기와 타임라인을 공유하는 중앙 작업 영역 | 빈 상태, 편집 상태 | `F-PREVIEW`, `F-TIMELINE` |
| `E-EDITOR-SPLITTER` | MVP | 미리 보기와 타임라인 높이 비율 조절 | 최소 높이 도달, 사용자 조절 | `F-EXPERIENCE-01`, `04` |

### 파일 및 공통 명령

| 상호작용 | 입력 | 활성 조건 | 즉시 결과 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `I-PROJECT-NEW` | 메뉴, 도구 모음, `Ctrl+N` | 긴 모달 작업이 없음 | 필요하면 저장 확인 후 빈 프로젝트 표시 | `F-PROJECT-01`, `05` |
| `I-PROJECT-OPEN` | 메뉴, 도구 모음, `Ctrl+O` | 긴 모달 작업이 없음 | 필요하면 저장 확인 후 파일 선택 | `F-PROJECT-04`, `05` |
| `I-PROJECT-SAVE` | 메뉴, 도구 모음, `Ctrl+S` | 프로젝트가 열림 | 처음 저장이면 위치 선택, 아니면 저장 상태 갱신 | `F-PROJECT-02` |
| `I-PROJECT-SAVE-AS` | 메뉴, `Ctrl+Shift+S` | 프로젝트가 열림 | 새 위치 선택 후 프로젝트명 갱신 | `F-PROJECT-03` |
| `I-PROJECT-EXIT` | 파일 메뉴, 창 닫기 | 긴 모달 작업이 없음 | 필요하면 저장 확인 후 종료 | `F-PROJECT-05` |
| `I-PANEL-LIBRARY-TOGGLE` | 보기 메뉴, 패널 닫기/열기 | 편집기 표시 중 | 보관함 표시만 변경, 선택 유지 | `F-EXPERIENCE-01` |
| `I-PANEL-INSPECTOR-TOGGLE` | 보기 메뉴, 패널 닫기/열기 | 편집기 표시 중 | 속성 표시만 변경, 선택 유지 | `F-EXPERIENCE-01` |

## E-START: 시작 상태 요소

| 요소 | 범위 | 표시 내용과 상세 기능 | 연결 기능 |
| --- | --- | --- | --- |
| `E-START-IMPORT` | MVP | 가장 강조된 `미디어 가져오기` 행동 | `F-MEDIA-01` |
| `E-START-NEW` | MVP | 현재 상태를 빈 새 프로젝트로 초기화 | `F-PROJECT-01` |
| `E-START-OPEN` | MVP | 저장된 프로젝트 파일 선택 | `F-PROJECT-04` |
| `E-START-RECENT` | 1.0 | 최근 프로젝트명, 위치 상태와 마지막 사용 시각 | `F-PROJECT-06` |
| `E-START-EXAMPLE` | 1.0 | 승인된 예제 프로젝트 열기 | `F-EXPERIENCE-06` |

시작 화면의 첫 키보드 포커스는 `E-START-IMPORT`에 둔다. 최근 항목이 누락됐으면 항목 자체를
숨기지 않고 누락 표시와 목록 제거 행동을 제공한다.

## E-LIBRARY: 미디어 보관함 요소

| 요소 | 범위 | 표시 내용과 상세 기능 | 주요 상태 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `E-LIBRARY-IMPORT` | MVP | 파일 선택 가져오기 | 기본, 가져오는 중 | `F-MEDIA-01`, `03` |
| `E-LIBRARY-FILTER` | MVP | 전체·영상·사진·오디오 필터 | 선택된 필터, 결과 없음 | `F-MEDIA-02` |
| `E-LIBRARY-GRID` | MVP | 미디어 항목 그리드 또는 좁은 폭의 목록 | 빈 상태, 채워짐, 부분 실패 | `F-MEDIA-02`, `03` |
| `E-LIBRARY-ITEM` | MVP | 썸네일/아이콘, 이름, 길이와 상태 배지 | 기본, 선택, 사용 중, 누락, 오류 | `F-MEDIA-02`, `03`, `06` |
| `E-LIBRARY-EMPTY` | MVP | 보관함 또는 현재 필터에 결과가 없을 때 다음 행동 | 전체 빈 상태, 필터 결과 없음 | `F-MEDIA-01`, `02` |
| `E-LIBRARY-WARNING` | MVP | 부분 가져오기 실패 수와 이유 요약 | 숨김, 경고 표시 | `F-MEDIA-03`, `F-EXPERIENCE-02` |
| `E-LIBRARY-REMOVE` | MVP | 선택 항목을 보관함에서 제거 | 선택 없음/사용 중이면 조건 안내 | `F-MEDIA-04` |
| `E-LIBRARY-ADD` | MVP | 선택 항목을 알맞은 타임라인 트랙에 추가 | 종류에 따라 영상·음악·내레이션 구분 | `F-TIMELINE-01`, `F-AUDIO-03`, `07` |
| `E-LIBRARY-RELINK` | 1.0 | 누락 항목의 새 원본 찾기 | 정상 항목에서는 숨김 | `F-MEDIA-07` |
| `E-LIBRARY-JOBS` | 1.0 | 썸네일·파형·프록시 작업 수와 상태 | 대기, 진행, 일부 실패 | `F-MEDIA-08`, `09` |
| `E-MEDIA-PROXY` | 1.0 | 선택 영상의 프록시 사용 또는 해제 | 정상 영상에서만 활성화 | `F-MEDIA-08` |
| `E-MEDIA-PROXY-STATUS` | 1.0 | 프록시 사용 여부와 생성 상태 | 사용 안 함, 생성 중, 준비됨, 실패 | `F-MEDIA-08`, `09` |

### 미디어 상호작용

| 상호작용 | 입력 | 활성 조건 | 즉시 결과 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `I-MEDIA-IMPORT` | 가져오기 버튼, `Ctrl+I`, 파일 드롭 | 중복 모달 작업 없음 | 성공 항목 추가, 실패 항목 요약, 마지막 성공 항목 선택 | `F-MEDIA-01`, `03`, `05` |
| `I-MEDIA-FILTER` | 필터 선택 | 항상 | 표시 항목만 변경하고 실제 보관함 내용과 선택은 유지 | `F-MEDIA-02` |
| `I-MEDIA-SELECT` | 클릭, 방향키, `Space` | 항목 존재 | 항목 선택과 미디어 속성 표시 | `F-MEDIA-02`, `F-EXPERIENCE-01` |
| `I-MEDIA-ADD` | 더블클릭, 추가 버튼, `Enter`, 타임라인 드롭 | 정상 미디어 선택 | 종류에 맞는 트랙 끝 또는 드롭 위치에 클립 표시 | `F-TIMELINE-01`, `F-AUDIO-03`, `07` |
| `I-MEDIA-REMOVE` | 제거 버튼, 컨텍스트 메뉴, `Delete` | 보관함에 선택 항목 존재 | 미사용 항목은 제거, 사용 중 항목은 영향 확인 | `F-MEDIA-04` |
| `I-MEDIA-RELINK` | 경고 배지, 다시 연결 버튼 | 누락 항목 선택 | 파일 선택 후 일치 여부와 결과 표시 | `F-MEDIA-07` |

`Delete`는 포커스가 보관함 안에 있을 때 보관함 제거로 동작하고, 타임라인 안에 있을 때는
클립 삭제로 동작한다. 실행 전 대상이 선택 표현으로 명확해야 한다.

## E-PREVIEW: 미리 보기 요소

| 요소 | 범위 | 표시 내용과 상세 기능 | 주요 상태 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `E-PREVIEW-CANVAS` | MVP | 프로젝트 화면 비율의 미리 보기 프레임 | 빈 상태, 정지, 재생, 누락 프레임 | `F-PREVIEW-01`, `04`, `F-VISUAL-04` |
| `E-PREVIEW-PLAY` | MVP | 재생/일시 정지 토글 | 정지 아이콘, 재생 아이콘 | `F-PREVIEW-02` |
| `E-PREVIEW-STEP-BACK` | 1.0 | 이전 프레임 이동 | 시작점 또는 정확도 미지원 시 비활성화 | `F-PREVIEW-05` |
| `E-PREVIEW-STEP-FORWARD` | 1.0 | 다음 프레임 이동 | 끝점 또는 정확도 미지원 시 비활성화 | `F-PREVIEW-05` |
| `E-PREVIEW-TIME` | MVP | `현재 / 전체` 시간 표시 | 유효 시간, 미디어 없음 | `F-PREVIEW-03`, `F-EXPERIENCE-01` |
| `E-PREVIEW-SEEK` | MVP | 프로젝트 전체 위치 슬라이더 | 빈 프로젝트에서는 비활성화 | `F-PREVIEW-03` |
| `E-PREVIEW-MUTE` | MVP | 미리 보기 출력만 음소거 | 음소거, 소리 켜짐 | `F-PREVIEW-01` |

### 미리 보기 상호작용

| 상호작용 | 입력 | 활성 조건 | 즉시 결과 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `I-PREVIEW-TOGGLE` | 재생 버튼, `Space` | 프로젝트 길이 > 0, 텍스트 입력 중 아님 | 아이콘과 상태 변경, 목업 시간 진행 | `F-PREVIEW-02` |
| `I-PREVIEW-SEEK` | 슬라이더, 타임라인 눈금 클릭 | 프로젝트 길이 > 0 | 재생 헤드·시간·캔버스가 같은 위치로 변경 | `F-PREVIEW-03` |
| `I-PREVIEW-STEP-BACK` | 버튼, `Left` 보조키 조합 | 1.0, 현재 위치 > 0 | 이전 프레임 위치와 정지 상태 | `F-PREVIEW-05` |
| `I-PREVIEW-STEP-FORWARD` | 버튼, `Right` 보조키 조합 | 1.0, 현재 위치 < 전체 길이 | 다음 프레임 위치와 정지 상태 | `F-PREVIEW-05` |
| `I-PREVIEW-MUTE` | 음소거 버튼 | 오디오 출력 가능 | 프로젝트 오디오 설정을 바꾸지 않고 미리 보기만 음소거 | `F-PREVIEW-01` |

## E-INSPECTOR: 속성 요소

### 공통

| 요소 | 범위 | 상세 기능 | 연결 기능 |
| --- | --- | --- | --- |
| `E-INSPECTOR-HEADER` | MVP | 선택 대상 종류, 이름과 다중 선택 수 표시 | `F-EXPERIENCE-01` |
| `E-INSPECTOR-STACK` | MVP | 선택 문맥에 맞는 페이지 하나만 표시 | 모든 편집 기능 |
| `E-INSPECTOR-RESET` | 1.0 | 현재 페이지의 변경 가능한 값을 기본값으로 복원 | `F-TIMELINE`, `F-AUDIO`, `F-TEXT`, `F-VISUAL` |
| `E-INSPECTOR-PROJECT` | MVP | 선택이 없을 때 프로젝트 이름·화면·요약 표시 | `F-PROJECT-01`~`04`, `F-VISUAL-04` |
| `E-INSPECTOR-MEDIA` | MVP/1.0 | 보관함 선택의 원본 정보, 상태, 프록시와 다시 연결 | `F-MEDIA-02`, `06`~`09` |
| `E-INSPECTOR-CLIP` | MVP/1.0 | 영상·사진 클립의 구간, 속도, 소리와 시각 속성 | `F-TIMELINE-05`~`07`, `F-AUDIO-01`, `02`, `11`, `F-VISUAL-01`~`03`, `07`, `08` |
| `E-INSPECTOR-AUDIO` | MVP/1.0 | 음악·내레이션의 위치, 구간, 음량, 페이드와 더킹 | `F-AUDIO-03`~`10` |
| `E-INSPECTOR-TEXT` | 1.0 | 텍스트 내용, 서식, 위치, 시간과 애니메이션 | `F-TEXT-01`~`06` |
| `E-INSPECTOR-TRANSITION` | 1.0 | 선택 경계의 전환 종류와 길이 | `F-VISUAL-05`, `06` |
| `E-INSPECTOR-APPLY` | MVP/1.0 | 현재 페이지의 유효한 입력을 편집 명령 하나로 적용 | 모든 속성 편집 기능 |

### 프로젝트 페이지

| 요소 | 범위 | 값 또는 동작 | 연결 기능 |
| --- | --- | --- | --- |
| `E-PROJECT-NAME` | MVP | 프로젝트 표시 이름 | `F-PROJECT-01`~`04` |
| `E-PROJECT-ASPECT` | MVP | `원본 유지`, `16:9`, 1.0 `4:3` | `F-VISUAL-04` |
| `E-PROJECT-SUMMARY` | MVP | 전체 길이, 시각 클립 수, 오디오 항목 수 | `F-EXPERIENCE-01` |

### 영상·사진 클립 페이지

| 요소 | 범위 | 값 또는 동작 | 연결 기능 |
| --- | --- | --- | --- |
| `E-CLIP-IN` | MVP | 원본 시작 시간, 클립 내부에서 유효한 값 | `F-TIMELINE-05` |
| `E-CLIP-OUT` | MVP | 원본 끝 시간, 시작보다 뒤인 값 | `F-TIMELINE-05` |
| `E-CLIP-DURATION` | MVP | 결과 길이, 사진은 직접 변경 | `F-TIMELINE-05`, `06` |
| `E-CLIP-SPEED` | MVP | 지원 속도 배율 선택 | `F-TIMELINE-07`, `F-AUDIO-11` |
| `E-CLIP-SOURCE-VOLUME` | MVP | 0~100% 음량 | `F-AUDIO-01` |
| `E-CLIP-SOURCE-MUTE` | MVP | 원본음 음소거 토글 | `F-AUDIO-02` |
| `E-CLIP-FIT` | 1.0 | 맞춤/채움 선택 | `F-VISUAL-01`, `02` |
| `E-CLIP-ROTATE` | 1.0 | 왼쪽/오른쪽 90도 회전 | `F-VISUAL-03` |
| `E-CLIP-ROTATE-LEFT` | 1.0 | 선택 클립을 왼쪽으로 90도 회전 | `F-VISUAL-03` |
| `E-CLIP-ROTATE-RIGHT` | 1.0 | 선택 클립을 오른쪽으로 90도 회전 | `F-VISUAL-03` |
| `E-CLIP-EFFECT` | 1.0 | 기본 효과 프리셋과 밝기 | `F-VISUAL-07`, `08` |

### 오디오 페이지

| 요소 | 범위 | 값 또는 동작 | 연결 기능 |
| --- | --- | --- | --- |
| `E-AUDIO-START` | MVP | 프로젝트 안의 시작 시간 | `F-AUDIO-03`, `04` |
| `E-AUDIO-RANGE` | MVP | 원본 사용 시작·끝 | `F-AUDIO-04` |
| `E-AUDIO-VOLUME` | MVP | 0~100% 음량 | `F-AUDIO-01`, `04`, `09` |
| `E-AUDIO-MUTE` | MVP | 음소거 토글 | `F-AUDIO-02`, `04` |
| `E-AUDIO-FADE-IN` | 1.0 | 없음 또는 지원 길이 | `F-AUDIO-06` |
| `E-AUDIO-FADE-OUT` | 1.0 | 없음 또는 지원 길이 | `F-AUDIO-06` |
| `E-AUDIO-DUCKING` | 1.0 | 꺼짐, 약하게, 보통, 강하게 | `F-AUDIO-10` |
| `E-AUDIO-WAVEFORM` | 1.0 | 실제 분석과 구분되는 장식 파형 및 생성 상태 | `F-AUDIO-05`, `F-MEDIA-09` |

### 텍스트 페이지

| 요소 | 범위 | 값 또는 동작 | 연결 기능 |
| --- | --- | --- | --- |
| `E-TEXT-KIND` | 1.0 | 제목, 캡션, 크레딧 | `F-TEXT-01`~`03` |
| `E-TEXT-CONTENT` | 1.0 | 여러 줄 텍스트 편집 | `F-TEXT-01`~`03` |
| `E-TEXT-FONT` | 1.0 | 글꼴, 크기, 굵기와 정렬 | `F-TEXT-04` |
| `E-TEXT-COLOR` | 1.0 | 전경색과 읽기 쉬운 기본 외곽선 | `F-TEXT-04` |
| `E-TEXT-POSITION` | 1.0 | 위, 가운데, 아래 및 안전 영역 안의 위치 | `F-TEXT-05` |
| `E-TEXT-TIME` | 1.0 | 시작과 표시 길이 | `F-TEXT-05` |
| `E-TEXT-ANIMATION` | 1.0 | 제한된 등장·퇴장 프리셋 | `F-TEXT-06` |

### 전환 페이지

| 요소 | 범위 | 값 또는 동작 | 연결 기능 |
| --- | --- | --- | --- |
| `E-TRANSITION-TYPE` | 1.0 | 없음, 페이드와 승인된 기본 프리셋 | `F-VISUAL-05`, `06` |
| `E-TRANSITION-DURATION` | 1.0 | 인접 클립이 허용하는 범위의 길이 | `F-VISUAL-05`, `06` |

속성값을 변경하면 명령 경계를 통해 프로젝트가 수정됨 상태가 된다. 숫자 입력은 유효 범위를
벗어난 값을 적용하지 않고 해당 필드 가까이에 이유를 표시한다.

## E-TIMELINE: 타임라인 요소

| 요소 | 범위 | 표시 내용과 상세 기능 | 주요 상태 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `E-TIMELINE-MODE` | 1.0 | 스토리보드/타임라인 전환 | 두 보기 중 하나 선택 | `F-TIMELINE-13` |
| `E-TIMELINE-RULER` | MVP | 시간 눈금과 클릭 탐색 | 확대 수준별 눈금 | `F-PREVIEW-03`, `F-TIMELINE-14` |
| `E-TIMELINE-PLAYHEAD` | MVP | 현재 시간의 수직선과 손잡이 | 정지, 재생, 끄는 중 | `F-PREVIEW-03` |
| `E-TIMELINE-VIDEO-TRACK` | MVP | 순차 영상·사진 클립과 전환 | 빈 상태, 채워짐, 누락 | `F-TIMELINE-01`~`10` |
| `E-TIMELINE-MUSIC-TRACK` | MVP | 배경 음악 클립 | 빈 상태, 채워짐 | `F-AUDIO-03`~`06` |
| `E-TIMELINE-NARRATION-TRACK` | 1.0 | 내레이션 클립 | 빈 상태, 녹음/파일 클립 | `F-AUDIO-07`~`10` |
| `E-TIMELINE-TEXT-TRACK` | 1.0 | 텍스트 표시 구간 | 제목, 캡션, 크레딧 | `F-TEXT-01`~`06` |
| `E-TIMELINE-CLIP` | MVP | 이름, 썸네일/파형, 길이와 상태 | 기본, 선택, 포커스, 누락 | `F-TIMELINE-01`~`12` |
| `E-TIMELINE-TRIM-START` | MVP | 선택 클립의 왼쪽 트리밍 손잡이 | 끌기 가능/경계 도달 | `F-TIMELINE-05` |
| `E-TIMELINE-TRIM-END` | MVP | 선택 클립의 오른쪽 트리밍 손잡이 | 끌기 가능/경계 도달 | `F-TIMELINE-05` |
| `E-TIMELINE-TRANSITION` | 1.0 | 인접 클립 경계의 전환 표식 | 없음, 선택, 적용됨 | `F-VISUAL-05`, `06` |
| `E-TIMELINE-ZOOM` | 1.0 | 축소, 확대와 전체 맞춤 | 최소, 중간, 최대 | `F-TIMELINE-14` |

### 타임라인 상호작용

| 상호작용 | 입력 | 활성 조건 | 즉시 결과 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `I-TIMELINE-SELECT` | 클릭, `Tab`/방향키 | 클립 존재 | 단일 선택, 속성 페이지와 재생 위치 갱신 | `F-EXPERIENCE-01` |
| `I-TIMELINE-MULTISELECT` | `Ctrl`/`Shift`+클릭 | 1.0, 호환되는 클립 존재 | 선택 수와 공통 명령 표시 | `F-TIMELINE-09` |
| `I-TIMELINE-MOVE` | 끌기, 이동 명령 | 클립 선택 | 삽입 위치 표시 후 순서·시간 갱신 | `F-TIMELINE-02`, `10` |
| `I-TIMELINE-DELETE` | 삭제 버튼, `Delete` | 클립 선택 | 클립 제거와 전체 길이 갱신 | `F-TIMELINE-03`, `10` |
| `I-TIMELINE-SPLIT` | 분할 버튼, `Ctrl+B` | 한 클립 선택, 재생 헤드가 내부 | 두 클립 생성, 뒤쪽 클립 선택 | `F-TIMELINE-04` |
| `I-TIMELINE-TRIM-START` | 왼쪽 손잡이 끌기, 값 입력 | 트리밍 가능한 클립 선택 | 미리 보기와 시작·길이 갱신 | `F-TIMELINE-05` |
| `I-TIMELINE-TRIM-END` | 오른쪽 손잡이 끌기, 값 입력 | 트리밍 가능한 클립 선택 | 미리 보기와 끝·길이 갱신 | `F-TIMELINE-05` |
| `I-TIMELINE-DUPLICATE` | 클립 메뉴, `Ctrl+D` | 1.0, 클립 선택 | 원본 뒤에 복제본 삽입 | `F-TIMELINE-08` |
| `I-TIMELINE-UNDO` | 메뉴, 버튼, `Ctrl+Z` | 1.0, 되돌릴 명령 존재 | 이전 상태와 다시 실행 가능 상태 표시 | `F-TIMELINE-11` |
| `I-TIMELINE-REDO` | 메뉴, 버튼, `Ctrl+Y` | 1.0, 재실행 명령 존재 | 다음 상태와 실행 취소 가능 상태 표시 | `F-TIMELINE-12` |
| `I-TIMELINE-MODE` | 보기 전환 | 1.0 | 같은 선택·재생 위치를 다른 보기로 표시 | `F-TIMELINE-13` |
| `I-TIMELINE-ZOOM` | 버튼, 슬라이더, `Ctrl+휠` | 1.0 | 눈금과 클립 폭만 변경 | `F-TIMELINE-14` |

## E-CREATE: 음악, 내레이션, 텍스트와 전환 생성

| 요소/상호작용 | 범위 | 활성 조건과 결과 | 연결 기능 |
| --- | --- | --- | --- |
| `I-AUDIO-ADD-MUSIC` | MVP | 오디오 미디어 선택 또는 파일 선택 후 음악 트랙에 추가 | `F-AUDIO-03` |
| `I-AUDIO-ADD-NARRATION` | 1.0 | 오디오 파일을 내레이션 트랙에 추가 | `F-AUDIO-07` |
| `I-AUDIO-RECORD-NARRATION` | 1.0 | `S-NARRATION`을 열고 완료된 녹음을 현재 위치에 추가 | `F-AUDIO-08` |
| `I-TEXT-ADD-TITLE` | 1.0 | 현재 장면 앞 또는 시작 위치에 제목 생성 후 텍스트 속성 표시 | `F-TEXT-01` |
| `I-TEXT-ADD-CAPTION` | 1.0 | 현재 재생 위치에 캡션 생성 후 내용 필드에 포커스 | `F-TEXT-02` |
| `I-TEXT-ADD-CREDITS` | 1.0 | 프로젝트 끝에 크레딧 생성 후 내용 필드에 포커스 | `F-TEXT-03` |
| `I-TRANSITION-ADD` | 1.0 | 인접한 두 시각 클립 경계에서 전환 생성 | `F-VISUAL-05`, `06` |

## E-EXPORT: 출력 설정과 진행 요소

| 요소 | 범위 | 값 또는 상세 기능 | 연결 기능 |
| --- | --- | --- | --- |
| `E-EXPORT-PATH` | MVP | 출력 파일명과 위치 선택 | `F-EXPORT-02` |
| `E-EXPORT-PRESET` | MVP | 원본 유지, 720p, 1080p | `F-EXPORT-01`, `F-VISUAL-04` |
| `E-EXPORT-ORIGINAL-SUMMARY` | MVP | 기준 미디어, 결과 비율과 해상도 설명 | `F-EXPORT-01` |
| `E-EXPORT-FRAMERATE` | 1.0 | 원본/24/25/30/50/60 중 지원 값 | `F-EXPORT-06` |
| `E-EXPORT-QUALITY` | 1.0 | 작게, 권장, 높음 | `F-EXPORT-06` |
| `E-EXPORT-SUMMARY` | MVP | 형식, 화면, 길이와 예상 크기 | `F-EXPORT-01`, `02` |
| `E-EXPORT-START` | MVP | 설정 검증 후 출력 시작 | `F-EXPORT-02`, `03` |
| `E-EXPORT-CANCEL` | MVP | 설정 창 닫기 또는 진행 중 작업 취소 | `F-EXPORT-04` |
| `E-TASK-PROGRESS` | MVP | 작업 단계, 진행 막대와 백분율 | `F-EXPORT-03`, `07` |
| `E-TASK-RESULT` | MVP | 완료 파일 열기/위치 열기 또는 실패 해결 행동 | `F-EXPORT-05`, `07` |

### 출력 상호작용

| 상호작용 | 입력 | 활성 조건 | 즉시 결과 | 연결 기능 |
| --- | --- | --- | --- | --- |
| `I-EXPORT-OPEN` | 동영상 저장, `Ctrl+E` | 시각 클립 존재 | 출력 설정 대화상자와 현재 프로젝트 요약 | `F-EXPORT-01`~`02` |
| `I-EXPORT-PRESET` | 프리셋 선택 | 설정 대화상자 열림 | 결과 비율·해상도와 요약 즉시 갱신 | `F-EXPORT-01`, `F-VISUAL-04` |
| `I-EXPORT-START` | 저장 시작 | 경로와 설정 유효 | 설정 창 닫힘, 진행 패널 표시, 중복 출력 방지 | `F-EXPORT-02`, `03` |
| `I-EXPORT-CANCEL` | 취소 버튼 | 출력 진행 중 | 확인 뒤 취소 중, 취소됨과 임시 파일 결과 표시 | `F-EXPORT-04` |
| `I-EXPORT-RESULT` | 결과 행동 | 완료 또는 실패 | 파일/위치 열기 또는 해결 행동 실행 | `F-EXPORT-05`, `07` |

## E-MESSAGE: 확인과 오류 요소

| 상황 | 주 메시지 | 행동 순서 |
| --- | --- | --- |
| 저장하지 않은 변경 | 변경을 저장할지 질문하고 대상 프로젝트명을 표시 | 저장, 저장하지 않음, 취소 |
| 사용 중 미디어 제거 | 영향을 받는 클립 수와 원본은 변경되지 않음을 표시 | 확인, 관련 클립을 먼저 제거 |
| 부분 가져오기 실패 | 성공 수와 실패 파일별 이유 표시 | 확인, 상세 정보 |
| 누락 미디어 | 마지막 경로와 영향을 받는 클립 표시 | 다시 연결(1.0), 누락 상태로 열기, 취소 |
| 출력 취소 | 불완전 파일 처리 결과를 표시 | 출력 취소, 계속 |
| 출력 실패 | 짧은 원인, 해결 행동과 진단 정보 진입 | 다시 시도, 설정으로 돌아가기, 닫기 |

`Esc`는 파괴적 행동을 실행하지 않고 대화상자를 닫거나 취소한다. 기본 포커스는 안전하면서
가장 일반적인 행동에 둔다. 파괴 가능성이 있는 행동은 색상뿐 아니라 구체적인 동사로
구분한다.

## E-SUPPORT: 누락, 복구, 녹음과 시작 안내 요소

| 요소 | 범위 | 값 또는 상세 기능 | 연결 기능 |
| --- | --- | --- | --- |
| `E-MISSING-LIST` | MVP | 누락 파일명, 마지막 경로와 영향을 받는 클립 수 | `F-MEDIA-06`, `F-PROJECT-04` |
| `E-MISSING-RELINK` | 1.0 | 선택 파일을 검증한 뒤 새 원본 경로에 다시 연결 | `F-MEDIA-07` |
| `E-MISSING-OPEN` | MVP | 누락 상태를 보존한 채 프로젝트 열기 | `F-MEDIA-06` |
| `E-MISSING-CANCEL` | MVP | 프로젝트를 열지 않고 기존 상태 유지 | `F-PROJECT-04`, `05` |
| `E-RECOVERY-COMPARISON` | 1.0 | 정상 저장본과 자동 저장본의 시각·변경 요약 비교 | `F-PROJECT-07`, `08` |
| `E-RECOVERY-AUTO` | 1.0 | 자동 저장본을 수정 상태로 열고 정상본은 보존 | `F-PROJECT-08` |
| `E-RECOVERY-NORMAL` | 1.0 | 정상 저장본을 저장된 상태로 열기 | `F-PROJECT-08` |
| `E-RECOVERY-LATER` | 1.0 | 어느 파일도 바꾸지 않고 복구 선택 닫기 | `F-PROJECT-08` |
| `E-PROJECT-VERSION-MESSAGE` | 1.0 | 지원 버전 변환 또는 더 새 스키마의 읽기 거부 이유 | `F-PROJECT-09` |
| `E-NARRATION-DEVICE` | 1.0 | 입력 장치 선택과 장치 없음 상태 | `F-AUDIO-08` |
| `E-NARRATION-LEVEL` | 1.0 | 입력 수준 또는 장치 오류 상태 | `F-AUDIO-08`, `F-EXPERIENCE-01`, `02` |
| `E-NARRATION-TIME` | 1.0 | 현재 녹음 길이와 시작 위치 | `F-AUDIO-08` |
| `E-NARRATION-RECORD` | 1.0 | 녹음 시작, 일시 정지와 계속 | `F-AUDIO-08` |
| `E-NARRATION-FINISH` | 1.0 | 완료한 녹음을 내레이션 트랙에 추가 | `F-AUDIO-08` |
| `E-NARRATION-CANCEL` | 1.0 | 임시 결과를 버리고 프로젝트를 유지 | `F-AUDIO-08` |
| `E-ONBOARDING-STEPS` | 1.0 | 가져오기, 편집, 저장·출력의 짧은 세 단계 | `F-EXPERIENCE-06` |
| `E-ONBOARDING-EXAMPLE` | 1.0 | 승인된 예제 프로젝트 열기 | `F-EXPERIENCE-06` |
| `E-ONBOARDING-SKIP` | 1.0 | 상태 변경 없이 안내 닫기 | `F-EXPERIENCE-06` |
| `E-ONBOARDING-NEXT` | 1.0 | 다음 안내로 이동하고 마지막에는 편집 시작 | `F-EXPERIENCE-06` |

누락 항목을 다시 연결할 때 실제 구현은 파일 크기, 미디어 종류, 길이와 스트림 정보를
비교하고 불일치 이유를 먼저 표시한다. 스키마 변환은 원본 프로젝트의 안전한 사본에서
수행하며, 목업에서는 화면 계약만 제공하고 파일을 만들거나 변경하지 않는다.

## E-RUNTIME: 실행 준비 요소

| 요소 | 범위 | 상세 기능 | 연결 기능 |
| --- | --- | --- | --- |
| `E-RUNTIME-STATUS` | MVP/1.0 | uv, Python, 환경, FFmpeg와 앱 상태 요약 | `F-RUNTIME-01`, `02` |
| `E-RUNTIME-PREPARE` | 1.0 | 환경 구성 또는 복구 시작 | `F-RUNTIME-04` |
| `E-RUNTIME-OPTIONS` | 1.0 | 일반 앱 옵션과 고급 인자 경계 | `F-RUNTIME-04` |
| `E-RUNTIME-LAUNCH` | 1.0 | 준비된 환경에서 앱 실행 | `F-RUNTIME-03`, `04` |
| `E-RUNTIME-LOG` | 1.0 | 진단 요약과 상세 로그 열기 | `F-RUNTIME-05` |
| `E-RUNTIME-MAINTENANCE` | 1.0 | 설치, 업데이트와 제거 진입 | `F-RUNTIME-06` |

## 원본 유지 선택의 상세 규칙

- `원본 유지`를 처음 선택하면 주 스토리 라인의 첫 번째 영상 클립을 기준 미디어로 사용한다.
- 영상이 없으면 첫 번째 사진을 사용하고 시각 미디어가 없으면 선택을 비활성화한다.
- 기준 미디어의 표시 회전 메타데이터를 적용한 비율과 해상도를 프로젝트 출력 화면으로
  저장한다.
- 이후 클립을 이동하거나 기준 미디어를 제거해도 저장된 프로젝트 출력 화면은 자동으로
  바뀌지 않는다.
- 서로 다른 비율의 미디어는 MVP에서 전체 내용을 보존하는 `맞춤`을 자동 적용하고 남는
  영역에는 중립 배경을 사용한다.
- 1.0에서는 클립별 `맞춤` 또는 `채움`을 선택할 수 있다.
- 사용자가 720p 또는 1080p 프리셋을 선택하면 출력은 각각 1280×720 또는 1920×1080의
  16:9 화면을 사용한다.

## 키보드 기본값

| 키 | 동작 | 문맥 |
| --- | --- | --- |
| `Ctrl+N` | 새 프로젝트 | 전역 |
| `Ctrl+O` | 프로젝트 열기 | 전역 |
| `Ctrl+S` | 저장 | 전역 |
| `Ctrl+Shift+S` | 다른 이름으로 저장 | 전역 |
| `Ctrl+I` | 미디어 가져오기 | 전역 |
| `Ctrl+E` | 동영상 저장 | 전역 |
| `Space` | 재생/일시 정지 | 텍스트 입력 중이 아닐 때 |
| `Delete` | 선택한 보관함 항목 또는 타임라인 클립 제거 | 현재 포커스 영역 |
| `Ctrl+B` | 재생 위치에서 분할 | 타임라인 문맥 |
| `Ctrl+D` | 클립 복제 | 1.0 타임라인 문맥 |
| `Ctrl+Z` | 실행 취소 | 1.0, 되돌릴 명령이 있을 때 |
| `Ctrl+Y` | 다시 실행 | 1.0, 재실행할 명령이 있을 때 |
| `Esc` | 현재 끌기·입력·대화상자 취소 | 현재 문맥 |

운영체제나 텍스트 입력과 충돌이 발견되면 6단계 검토에서 변경한다. 단축키는 메뉴 항목과
도구 설명에 함께 표시한다.

## 3단계 완료 확인

- [x] 주요 화면의 표시 및 조작 요소에 `E-` 식별자를 부여했다.
- [x] 사용자 입력, 활성 조건, 즉시 결과와 연결 기능을 정의했다.
- [x] 선택, 포커스, 비활성화, 진행 및 오류 상태를 정의했다.
- [x] 프로젝트·미디어·타임라인·미리 보기·속성·출력 요소를 정의했다.
- [x] 원본 비율·해상도 유지의 기준과 혼합 미디어 기본 처리를 정의했다.
- [x] 기본 키보드 경로를 정의했다.
- [x] 인터랙티브 목업에서 요소 배치와 상태 변화를 검증했다.
- [x] 목업 검토 결과를 반영하고 문서 상태를 `승인됨`으로 변경했다.

요소별 검증 시나리오와 반영 내용은 [MOCK-0002](../mock/mock-0002-validation-scenarios.md)와
[MOCK-0003](../mock/mock-0003-review-log.md)에 기록한다.
